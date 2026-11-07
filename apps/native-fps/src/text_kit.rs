//! HUD text in real fonts: shaped once, rasterized into one atlas, drawn as
//! quads in the overlay's own vertex stream.
//!
//! The HUD used to draw text with a hand-made 5×7 bitmap font, one quad per lit
//! pixel. That font is still here (`hud::glyph`) and still the fallback, but once
//! the OS fonts have loaded, `Painter::text` asks this module instead.
//!
//! **Glyphs go into the same vertex buffer as the panels.** A glyph is a quad
//! with texture coordinates into one atlas, so text and shapes are drawn by one
//! draw call in call order — a label sits on its panel and under the menu scrim
//! for free. Chat used to be the counter-example: a separately rasterized
//! texture drawn after the HUD, which covered whatever was under it and so had
//! to hide whenever a menu was open. It is a `Paragraph` here now.
//!
//! **Two shapes of text.** A *label* (`emit`) is one line in one face and one
//! colour — everything the HUD writes itself. A *paragraph* (`paragraph`,
//! `emit_paragraph`) is what a person wrote: several coloured spans, wrapped to
//! a width, with bidi and font fallback, so an emoji or a right-to-left name
//! lays out as it should. Both end as quads into the same atlas.
//!
//! **Three faces, all the operating system's** — nothing is bundled, the same rule
//! as the maps and the sounds:
//!
//! | Face | Windows | Elsewhere |
//! |---|---|---|
//! | `Display` (labels, headings) | Bahnschrift | the system sans |
//! | `Mono` (numbers, stats, timers) | Cascadia Mono, then Consolas | the system mono |
//! | `Body` (names, prose) | Segoe UI | the system sans |
//!
//! **Sized by cap height, not by em.** Every layout in `hud.rs` reserves a
//! `7·scale` box for a line of text, because that is how tall a 5×7 glyph is. A
//! real face is sized so its capitals are exactly that tall, so text drops into
//! the space the layout already made for it whichever face drew it.
//!
//! Things that are bugs if changed:
//!
//! - **Discovery is on a thread.** Scanning the system's fonts takes a few hundred
//!   milliseconds; the bitmap font covers those frames (`fonts_ready`).
//! - **The atlas is cleared between frames, never during one.** A glyph that
//!   doesn't fit is skipped for the frame and the clear happens at the next
//!   `begin_frame`, because the vertices already emitted this frame point at the
//!   atlas as it is now.
//! - **Shaped runs are cached by string.** Shaping is the expensive part, and a
//!   HUD says the same few dozen strings every frame.

use std::cell::RefCell;
use std::collections::HashMap;
use std::sync::mpsc::{self, Receiver};
use std::thread;

use cosmic_text::fontdb;
use cosmic_text::{
    Attrs, Buffer, CacheKey, Family, FontSystem, LayoutGlyph, Metrics, PhysicalGlyph, Shaping,
    SwashCache, SwashContent, Weight, Wrap,
};

use crate::hud::OverlayVertex;

/// The atlas is square, this many texels a side. RGBA8, so 16 MiB.
pub const ATLAS_SIZE: u32 = 2048;
/// Empty texels around every glyph, so a sampled edge never picks up its
/// neighbour.
const PAD: u32 = 1;
/// Runs not drawn for this many frames are dropped from the cache.
const RUN_TTL_FRAMES: u64 = 240;
/// The bitmap font's cap height in cells. Real faces are sized to match.
const CAP_CELLS: f32 = 7.0;

/// Which typeface a string is set in.
#[derive(Copy, Clone, Debug, PartialEq, Eq, Hash)]
pub enum Face {
    /// Labels and headings.
    Display,
    /// Numbers, stats, timers, money — anything that changes in place and must
    /// not jitter sideways as its digits do.
    Mono,
    /// Player names and anything else a person wrote.
    Body,
}

impl Face {
    const ALL: [Face; 3] = [Face::Display, Face::Mono, Face::Body];

    fn index(self) -> usize {
        match self {
            Face::Display => 0,
            Face::Mono => 1,
            Face::Body => 2,
        }
    }

    /// Families to try, in order. The first one installed wins.
    fn families(self) -> &'static [&'static str] {
        match self {
            Face::Display => &["Bahnschrift", "Segoe UI", "SF Pro Text", "Helvetica Neue", "Noto Sans", "DejaVu Sans"],
            Face::Mono => &["Cascadia Mono", "Consolas", "SF Mono", "Menlo", "Noto Sans Mono", "DejaVu Sans Mono"],
            Face::Body => &["Segoe UI", "SF Pro Text", "Helvetica Neue", "Noto Sans", "DejaVu Sans"],
        }
    }

    fn generic(self) -> Family<'static> {
        match self {
            Face::Mono => Family::Monospace,
            _ => Family::SansSerif,
        }
    }

    /// Semibold throughout: a HUD is read over a moving, lit scene, and a
    /// regular weight at 12 px thins out to nothing against a bright wall.
    fn weight(self) -> Weight {
        Weight(600)
    }

    /// Tracking, in em. A little for the display face, which is set in capitals
    /// and reads cramped without it; none for the others.
    fn tracking(self) -> f32 {
        match self {
            Face::Display => 0.04,
            _ => 0.0,
        }
    }
}

/// One face as resolved on this machine.
#[derive(Clone, Debug)]
struct Resolved {
    /// The installed family name, or `None` for the generic family.
    family: Option<String>,
    /// Cap height as a fraction of the em.
    cap: f32,
}

/// A string shaped in one face at one size.
struct Run {
    glyphs: Vec<LayoutGlyph>,
    width: f32,
    used: u64,
}

#[derive(Clone, PartialEq, Eq, Hash)]
struct RunKey {
    face: Face,
    /// Font size in half pixels.
    half_px: u32,
    text: String,
}

/// Coloured spans laid out and wrapped: a chat line. Colour is not part of the
/// layout — each glyph carries the index of the span it came from, and the
/// colours are supplied when it is drawn — so a line that fades re-uses its
/// shape every frame.
#[derive(Clone, Debug)]
pub struct Paragraph {
    glyphs: Vec<Placed>,
    /// The widest line, in pixels.
    pub width: f32,
    /// Every line, in pixels.
    pub height: f32,
    pub line_h: f32,
}

#[derive(Clone, Debug)]
struct Placed {
    glyph: LayoutGlyph,
    /// The line's baseline and top, from the paragraph's top.
    line_y: f32,
    line_top: f32,
}

impl Paragraph {
    /// Where a caret after byte `byte` of the spans (concatenated) sits, from
    /// the paragraph's top-left: just after the last glyph that ends at or
    /// before it, on that glyph's line. The start is the origin.
    pub fn caret(&self, byte: usize) -> (f32, f32) {
        let mut pos = (0.0, 0.0);
        for p in &self.glyphs {
            if p.glyph.end <= byte {
                pos = (p.glyph.x + p.glyph.w, p.line_top);
            }
        }
        pos
    }

    /// How many lines it wrapped to.
    pub fn lines(&self) -> usize {
        (self.height / self.line_h.max(1.0)).round() as usize
    }
}

#[derive(Clone, PartialEq, Eq, Hash)]
struct ParaKey {
    face: Face,
    half_px: u32,
    /// The wrap width, in whole pixels.
    width: u32,
    spans: Vec<String>,
}

struct CachedPara {
    para: Paragraph,
    used: u64,
}

/// Where a glyph is in the atlas, and how to place it from its pen position.
#[derive(Copy, Clone, Debug)]
struct Slot {
    x: u32,
    y: u32,
    w: u32,
    h: u32,
    left: i32,
    top: i32,
    /// A colour bitmap (emoji): drawn as is, not tinted.
    color: bool,
}

/// A rectangle of new atlas pixels for the renderer to copy up: whole rows, so
/// the bytes are one contiguous slice of the atlas.
pub struct AtlasUpload {
    pub y: u32,
    pub rows: u32,
    /// `ATLAS_SIZE * rows * 4` bytes, straight RGBA.
    pub bytes: Vec<u8>,
}

/// A shelf packer: rows of glyphs of similar height.
struct Atlas {
    pixels: Vec<u8>,
    shelves: Vec<Shelf>,
    next_y: u32,
    slots: HashMap<CacheKey, Option<Slot>>,
    /// Dirty rows, `[start, end)`.
    dirty: Option<(u32, u32)>,
    /// A glyph didn't fit this frame; clear at the next frame.
    full: bool,
}

struct Shelf {
    y: u32,
    h: u32,
    x: u32,
}

impl Atlas {
    /// Pixels are allocated on first use: a bitmap-only kit (every test that
    /// builds a HUD) never needs the 16 MiB.
    fn new() -> Atlas {
        Atlas {
            pixels: Vec::new(),
            shelves: Vec::new(),
            next_y: 0,
            slots: HashMap::new(),
            dirty: None,
            full: false,
        }
    }

    fn ensure(&mut self) {
        if self.pixels.is_empty() {
            self.pixels = vec![0; (ATLAS_SIZE * ATLAS_SIZE * 4) as usize];
        }
    }

    fn clear(&mut self) {
        self.ensure();
        self.pixels.fill(0);
        self.shelves.clear();
        self.next_y = 0;
        self.slots.clear();
        self.full = false;
        self.dirty = Some((0, ATLAS_SIZE));
    }

    /// Room for a `w`×`h` image, or `None` when the atlas is full.
    fn allocate(&mut self, w: u32, h: u32) -> Option<(u32, u32)> {
        let (pw, ph) = (w + PAD * 2, h + PAD * 2);
        if pw > ATLAS_SIZE || ph > ATLAS_SIZE {
            return None;
        }
        // The best existing shelf: tall enough, not wastefully so, with room.
        let mut best: Option<usize> = None;
        for (i, s) in self.shelves.iter().enumerate() {
            if s.h >= ph && s.h <= ph + ph / 3 + 2 && s.x + pw <= ATLAS_SIZE {
                if best.is_none_or(|b| self.shelves[b].h > s.h) {
                    best = Some(i);
                }
            }
        }
        let shelf = match best {
            Some(i) => &mut self.shelves[i],
            None => {
                if self.next_y + ph > ATLAS_SIZE {
                    return None;
                }
                self.shelves.push(Shelf {
                    y: self.next_y,
                    h: ph,
                    x: 0,
                });
                self.next_y += ph;
                self.shelves.last_mut().expect("just pushed")
            }
        };
        let at = (shelf.x + PAD, shelf.y + PAD);
        shelf.x += pw;
        Some(at)
    }

    fn mark(&mut self, y0: u32, y1: u32) {
        self.dirty = Some(match self.dirty {
            Some((a, b)) => (a.min(y0), b.max(y1)),
            None => (y0, y1),
        });
    }

    fn write(&mut self, x: u32, y: u32, w: u32, h: u32, rgba: impl Fn(u32, u32) -> [u8; 4]) {
        self.ensure();
        for row in 0..h {
            for col in 0..w {
                let i = (((y + row) * ATLAS_SIZE + x + col) * 4) as usize;
                self.pixels[i..i + 4].copy_from_slice(&rgba(col, row));
            }
        }
        self.mark(y, y + h);
    }

    fn take_upload(&mut self) -> Option<AtlasUpload> {
        let (y0, y1) = self.dirty.take()?;
        self.ensure();
        let start = (y0 * ATLAS_SIZE * 4) as usize;
        let end = (y1 * ATLAS_SIZE * 4) as usize;
        Some(AtlasUpload {
            y: y0,
            rows: y1 - y0,
            bytes: self.pixels[start..end].to_vec(),
        })
    }
}

struct Fonts {
    system: FontSystem,
    swash: SwashCache,
    faces: [Resolved; 3],
}

struct Inner {
    loading: Option<Receiver<FontSystem>>,
    fonts: Option<Fonts>,
    runs: HashMap<RunKey, Run>,
    paras: HashMap<ParaKey, CachedPara>,
    atlas: Atlas,
    frame: u64,
}

/// The HUD's text engine. Shared by `&` reference: painting a label caches its
/// shape and its glyphs, which is interior state, not something a caller owns.
pub struct TextKit {
    inner: RefCell<Inner>,
}

impl TextKit {
    fn with(loading: Option<Receiver<FontSystem>>, fonts: Option<FontSystem>) -> TextKit {
        TextKit {
            inner: RefCell::new(Inner {
                loading,
                fonts: fonts.map(resolve),
                runs: HashMap::new(),
                paras: HashMap::new(),
                atlas: Atlas::new(),
                frame: 0,
            }),
        }
    }

    /// Discover the system's fonts in the background; the bitmap font draws
    /// until they land.
    pub fn spawn() -> TextKit {
        let (tx, rx) = mpsc::channel();
        let started = thread::Builder::new()
            .name("hud-fonts".into())
            .spawn(move || {
                let _ = tx.send(FontSystem::new());
            })
            .is_ok();
        TextKit::with(started.then_some(rx), None)
    }

    /// Load the fonts now, on this thread — for tools and tests that want them.
    pub fn blocking() -> TextKit {
        TextKit::with(None, Some(FontSystem::new()))
    }

    /// The bitmap font, always.
    pub fn bitmap() -> TextKit {
        TextKit::with(None, None)
    }

    /// `spawn`, unless `HUDFONT=bitmap` asks for the old font — for comparing
    /// the two and for a machine whose fonts misbehave.
    pub fn from_env() -> TextKit {
        if forced_bitmap() {
            TextKit::bitmap()
        } else {
            TextKit::spawn()
        }
    }

    /// `blocking`, unless `HUDFONT=bitmap`.
    pub fn blocking_from_env() -> TextKit {
        if forced_bitmap() {
            TextKit::bitmap()
        } else {
            TextKit::blocking()
        }
    }

    /// Whether real fonts are drawing. Picks up the background load.
    pub fn fonts_ready(&self) -> bool {
        let mut inner = self.inner.borrow_mut();
        if inner.fonts.is_none() {
            if let Some(rx) = &inner.loading {
                if let Ok(system) = rx.try_recv() {
                    inner.fonts = Some(resolve(system));
                    inner.loading = None;
                }
            }
        }
        inner.fonts.is_some()
    }

    /// The family a face resolved to, for the docs and the console.
    pub fn family(&self, face: Face) -> Option<String> {
        let inner = self.inner.borrow();
        let fonts = inner.fonts.as_ref()?;
        Some(
            fonts.faces[face.index()]
                .family
                .clone()
                .unwrap_or_else(|| format!("{:?}", face.generic())),
        )
    }

    /// Start a frame: clear the atlas if it overflowed last frame, and drop runs
    /// nobody has drawn for a while.
    pub fn begin_frame(&self) {
        let mut inner = self.inner.borrow_mut();
        inner.frame += 1;
        if inner.atlas.full {
            crate::divergence::note_overflow("hud-atlas", 1, 0);
            inner.atlas.clear();
        }
        if inner.frame % 60 == 0 {
            let now = inner.frame;
            inner.runs.retain(|_, r| now - r.used < RUN_TTL_FRAMES);
            inner.paras.retain(|_, p| now - p.used < RUN_TTL_FRAMES);
        }
    }

    /// New atlas pixels since the last call, for the renderer.
    pub fn take_upload(&self) -> Option<AtlasUpload> {
        self.inner.borrow_mut().atlas.take_upload()
    }

    /// Make the whole atlas dirty — for a renderer that has just been created and
    /// has none of it yet.
    pub fn mark_all_dirty(&self) {
        self.inner.borrow_mut().atlas.mark(0, ATLAS_SIZE);
    }

    /// Width of `s` in pixels, in `face`, with capitals `7 * scale` tall.
    /// `None` when real fonts are not drawing.
    pub fn width(&self, face: Face, s: &str, scale: f32) -> Option<f32> {
        if s.is_empty() {
            return Some(0.0);
        }
        let mut inner = self.inner.borrow_mut();
        let inner = &mut *inner;
        let fonts = inner.fonts.as_mut()?;
        let run = shaped(fonts, &mut inner.runs, inner.frame, face, s, scale);
        Some(run.width)
    }

    /// Emit `s` as glyph quads with its capitals' top at `y` and its pen starting
    /// at `x`, in pixels; `ndc` converts a pixel position to clip space.
    /// Returns `false` (having drawn nothing) when real fonts are not drawing.
    #[allow(clippy::too_many_arguments)]
    pub fn emit(
        &self,
        face: Face,
        x: f32,
        y: f32,
        scale: f32,
        color: [f32; 4],
        s: &str,
        ndc: impl Fn(f32, f32) -> [f32; 2],
        out: &mut Vec<OverlayVertex>,
    ) -> bool {
        let mut inner = self.inner.borrow_mut();
        let inner = &mut *inner;
        let Some(fonts) = inner.fonts.as_mut() else {
            return false;
        };
        if s.is_empty() || color[3] <= 0.0 {
            return true;
        }
        let frame = inner.frame;
        let run = shaped(fonts, &mut inner.runs, frame, face, s, scale);
        // The baseline sits at the bottom of the reserved 7-cell box, where the
        // bitmap font's capitals end.
        let baseline = y + CAP_CELLS * scale;
        let glyphs = run.glyphs.clone();
        for g in &glyphs {
            let phys = g.physical((x, baseline.round()), 1.0);
            put_glyph(fonts, &mut inner.atlas, phys, color, &ndc, out);
        }
        true
    }

    /// Lay `spans` out as one paragraph in `face`, wrapped at `max_w` pixels,
    /// capitals `7 * scale` tall. `None` when real fonts are not drawing — the
    /// caller falls back to the bitmap font.
    ///
    /// Cached by the spans' text, so a chat line is shaped once however many
    /// frames it is on screen.
    pub fn paragraph(&self, face: Face, spans: &[&str], scale: f32, max_w: f32) -> Option<Paragraph> {
        let mut inner = self.inner.borrow_mut();
        let inner = &mut *inner;
        let fonts = inner.fonts.as_mut()?;
        let key = ParaKey {
            face,
            half_px: half_px(fonts, face, scale),
            width: max_w.max(1.0) as u32,
            spans: spans.iter().map(|s| s.to_string()).collect(),
        };
        let frame = inner.frame;
        let cached = inner.paras.entry(key).or_insert_with_key(|key| CachedPara {
            para: lay_out(fonts, key),
            used: frame,
        });
        cached.used = frame;
        Some(cached.para.clone())
    }

    /// Draw a `paragraph` with its top-left at (`x`, `y`). Span `i` is drawn in
    /// `colors[i]` with its alpha multiplied by `alpha`; colour glyphs (emoji)
    /// keep their own colours and take only the alpha, so a fading line fades
    /// its emoji with it.
    #[allow(clippy::too_many_arguments)]
    pub fn emit_paragraph(
        &self,
        para: &Paragraph,
        x: f32,
        y: f32,
        colors: &[[f32; 4]],
        alpha: f32,
        ndc: impl Fn(f32, f32) -> [f32; 2],
        out: &mut Vec<OverlayVertex>,
    ) {
        let mut inner = self.inner.borrow_mut();
        let inner = &mut *inner;
        let Some(fonts) = inner.fonts.as_mut() else {
            return;
        };
        if alpha <= 0.0 {
            return;
        }
        for p in &para.glyphs {
            let mut color = colors
                .get(p.glyph.metadata)
                .copied()
                .unwrap_or([1.0, 1.0, 1.0, 1.0]);
            color[3] *= alpha;
            let phys = p.glyph.physical((x, (y + p.line_y).round()), 1.0);
            put_glyph(fonts, &mut inner.atlas, phys, color, &ndc, out);
        }
    }
}

/// One glyph's quad, rasterizing it into the atlas first if it is new. A glyph
/// that does not fit is skipped and the atlas marked full, to be cleared at the
/// next `begin_frame` (see the module header for why not now).
fn put_glyph(
    fonts: &mut Fonts,
    atlas: &mut Atlas,
    phys: PhysicalGlyph,
    color: [f32; 4],
    ndc: &impl Fn(f32, f32) -> [f32; 2],
    out: &mut Vec<OverlayVertex>,
) {
    let slot = match atlas.slots.get(&phys.cache_key) {
        Some(slot) => *slot,
        None => {
            if atlas.full {
                return;
            }
            match rasterize(fonts, atlas, phys.cache_key) {
                Ok(slot) => {
                    atlas.slots.insert(phys.cache_key, slot);
                    slot
                }
                Err(()) => {
                    atlas.full = true;
                    return;
                }
            }
        }
    };
    let Some(slot) = slot else {
        return;
    };
    let inv = 1.0 / ATLAS_SIZE as f32;
    let gx = (phys.x + slot.left) as f32;
    let gy = (phys.y - slot.top) as f32;
    let (gw, gh) = (slot.w as f32, slot.h as f32);
    let (u0, v0) = (slot.x as f32 * inv, slot.y as f32 * inv);
    let (u1, v1) = ((slot.x + slot.w) as f32 * inv, (slot.y + slot.h) as f32 * inv);
    let tint = if slot.color {
        [1.0, 1.0, 1.0, color[3]]
    } else {
        color
    };
    let a = (ndc(gx, gy), [u0, v0]);
    let b = (ndc(gx + gw, gy), [u1, v0]);
    let c = (ndc(gx + gw, gy + gh), [u1, v1]);
    let d = (ndc(gx, gy + gh), [u0, v1]);
    for (position, uv) in [a, b, c, a, c, d] {
        out.push(OverlayVertex {
            position,
            color: tint,
            uv,
        });
    }
}

/// Shape and wrap a paragraph. Each span's attributes carry its index as
/// metadata, which every glyph it produces inherits — that is how a glyph
/// finds its colour at draw time.
fn lay_out(fonts: &mut Fonts, key: &ParaKey) -> Paragraph {
    let px = key.half_px as f32 * 0.5;
    let line_h = (px * 1.35).ceil();
    let resolved = fonts.faces[key.face.index()].clone();
    let family = match &resolved.family {
        Some(name) => Family::Name(name),
        None => key.face.generic(),
    };
    let attrs = Attrs::new()
        .family(family)
        .weight(key.face.weight())
        .letter_spacing(key.face.tracking());
    let mut buffer = Buffer::new(&mut fonts.system, Metrics::new(px, line_h));
    buffer.set_wrap(Wrap::WordOrGlyph);
    buffer.set_size(Some(key.width as f32), None);
    buffer.set_rich_text(
        key.spans
            .iter()
            .enumerate()
            .map(|(i, s)| (s.as_str(), attrs.clone().metadata(i))),
        &attrs,
        Shaping::Advanced,
        None,
    );
    buffer.shape_until_scroll(&mut fonts.system, false);
    let mut glyphs = Vec::new();
    let mut width: f32 = 0.0;
    let mut lines = 0usize;
    for run in buffer.layout_runs() {
        lines += 1;
        width = width.max(run.line_w);
        glyphs.extend(run.glyphs.iter().map(|g| Placed {
            glyph: g.clone(),
            line_y: run.line_y,
            line_top: run.line_top,
        }));
    }
    Paragraph {
        glyphs,
        width,
        height: lines.max(1) as f32 * line_h,
        line_h,
    }
}

fn forced_bitmap() -> bool {
    std::env::var("HUDFONT").is_ok_and(|v| v.eq_ignore_ascii_case("bitmap"))
}

/// Pick each face's family from what is installed, and measure its capitals.
fn resolve(mut system: FontSystem) -> Fonts {
    let faces = Face::ALL.map(|face| {
        let family = face
            .families()
            .iter()
            .find(|name| {
                system
                    .db()
                    .faces()
                    .any(|f| f.families.iter().any(|(n, _)| n.eq_ignore_ascii_case(name)))
            })
            .map(|s| s.to_string());
        let query_family = match &family {
            Some(name) => fontdb::Family::Name(name),
            None => face.generic(),
        };
        let id = system.db().query(&fontdb::Query {
            families: &[query_family],
            weight: face.weight(),
            ..Default::default()
        });
        let cap = id
            .and_then(|id| system.get_font(id, face.weight()))
            .map(|font| {
                let m = font.as_swash().metrics(&[]);
                if m.units_per_em > 0 && m.cap_height > 0.0 {
                    m.cap_height / m.units_per_em as f32
                } else {
                    0.7
                }
            })
            .unwrap_or(0.7);
        Resolved { family, cap }
    });
    Fonts {
        system,
        swash: SwashCache::new(),
        faces,
    }
}

/// The font size that makes `face`'s capitals `7 * scale` pixels tall, in half
/// pixels — quantised so a HUD with a dozen nearly-equal scales does not fill
/// the atlas with a dozen nearly-equal copies of every glyph.
fn half_px(fonts: &Fonts, face: Face, scale: f32) -> u32 {
    let cap = fonts.faces[face.index()].cap.max(0.3);
    ((CAP_CELLS * scale / cap) * 2.0).round().max(8.0) as u32
}

fn shaped<'r>(
    fonts: &mut Fonts,
    runs: &'r mut HashMap<RunKey, Run>,
    frame: u64,
    face: Face,
    s: &str,
    scale: f32,
) -> &'r Run {
    let key = RunKey {
        face,
        half_px: half_px(fonts, face, scale),
        text: s.to_string(),
    };
    let run = runs.entry(key).or_insert_with_key(|key| shape(fonts, key));
    run.used = frame;
    run
}

fn shape(fonts: &mut Fonts, key: &RunKey) -> Run {
    let px = key.half_px as f32 * 0.5;
    let resolved = fonts.faces[key.face.index()].clone();
    let family = match &resolved.family {
        Some(name) => Family::Name(name),
        None => key.face.generic(),
    };
    let attrs = Attrs::new()
        .family(family)
        .weight(key.face.weight())
        .letter_spacing(key.face.tracking());
    let mut buffer = Buffer::new(&mut fonts.system, Metrics::new(px, px * 1.25));
    buffer.set_wrap(Wrap::None);
    buffer.set_size(None, None);
    buffer.set_text(&key.text, &attrs, Shaping::Advanced, None);
    buffer.shape_until_scroll(&mut fonts.system, false);
    let mut glyphs = Vec::new();
    let mut width: f32 = 0.0;
    // One line by construction (no wrap, and HUD strings carry no newlines);
    // only the first is drawn if one ever does.
    if let Some(run) = buffer.layout_runs().next() {
        glyphs.extend(run.glyphs.iter().cloned());
        width = run.line_w;
    }
    // Tracking is added after every glyph, the last included; take it back off
    // so right-aligned text ends where it says it does — the bitmap font's
    // `text_width` makes the same correction for its spacing column.
    let trailing = key.face.tracking() * px;
    if !glyphs.is_empty() {
        width = (width - trailing).max(0.0);
    }
    Run {
        glyphs,
        width,
        used: 0,
    }
}

/// Rasterize one glyph into the atlas. `Ok(None)` for a glyph with no pixels
/// (a space); `Err` when the atlas is full.
fn rasterize(fonts: &mut Fonts, atlas: &mut Atlas, key: CacheKey) -> Result<Option<Slot>, ()> {
    let Some(image) = fonts.swash.get_image_uncached(&mut fonts.system, key) else {
        return Ok(None);
    };
    let (w, h) = (image.placement.width, image.placement.height);
    if w == 0 || h == 0 {
        return Ok(None);
    }
    let (x, y) = atlas.allocate(w, h).ok_or(())?;
    let data = &image.data;
    let color = matches!(image.content, SwashContent::Color);
    match image.content {
        SwashContent::Mask => atlas.write(x, y, w, h, |c, r| {
            [255, 255, 255, data[(r * w + c) as usize]]
        }),
        SwashContent::Color => atlas.write(x, y, w, h, |c, r| {
            let i = ((r * w + c) * 4) as usize;
            [data[i], data[i + 1], data[i + 2], data[i + 3]]
        }),
        SwashContent::SubpixelMask => atlas.write(x, y, w, h, |c, r| {
            // Not requested, but a font can still hand one back: average the
            // three channels into one coverage.
            let i = ((r * w + c) * 4) as usize;
            let a = (data[i] as u32 + data[i + 1] as u32 + data[i + 2] as u32) / 3;
            [255, 255, 255, a as u8]
        }),
    }
    Ok(Some(Slot {
        x,
        y,
        w,
        h,
        left: image.placement.left,
        top: image.placement.top,
        color,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_shelf_packer_never_overlaps_and_reports_full() {
        let mut atlas = Atlas::new();
        let mut placed: Vec<(u32, u32, u32, u32)> = Vec::new();
        // A run of glyph-sized boxes of a few heights, like three HUD faces.
        let sizes = [(9, 13), (11, 17), (7, 9), (20, 26), (12, 13)];
        for i in 0..4000 {
            let (w, h) = sizes[i % sizes.len()];
            match atlas.allocate(w, h) {
                Some((x, y)) => {
                    assert!(x + w <= ATLAS_SIZE && y + h <= ATLAS_SIZE);
                    for &(px, py, pw, ph) in &placed {
                        let apart = x + w + PAD <= px
                            || px + pw + PAD <= x
                            || y + h + PAD <= py
                            || py + ph + PAD <= y;
                        assert!(apart, "({x},{y},{w},{h}) overlaps ({px},{py},{pw},{ph})");
                    }
                    placed.push((x, y, w, h));
                }
                None => break,
            }
        }
        assert!(placed.len() > 1000, "only {} glyphs fit", placed.len());
        // A box bigger than the atlas never fits.
        assert!(atlas.allocate(ATLAS_SIZE, 4).is_none());
    }

    #[test]
    fn filling_the_atlas_eventually_refuses_rather_than_wrapping() {
        let mut atlas = Atlas::new();
        let mut n = 0;
        while atlas.allocate(60, 60).is_some() {
            n += 1;
            assert!(n < 10_000, "the atlas never filled");
        }
        let per_row = ATLAS_SIZE / 62;
        assert_eq!(n, per_row * per_row);
    }

    #[test]
    fn uploads_are_the_dirty_rows_and_only_once() {
        let mut atlas = Atlas::new();
        assert!(atlas.take_upload().is_none());
        atlas.write(3, 10, 2, 4, |_, _| [1, 2, 3, 4]);
        atlas.write(5, 40, 1, 1, |_, _| [9, 9, 9, 9]);
        let up = atlas.take_upload().expect("dirty");
        assert_eq!((up.y, up.rows), (10, 31));
        assert_eq!(up.bytes.len(), (ATLAS_SIZE * 31 * 4) as usize);
        assert_eq!(&up.bytes[(3 * 4) as usize..(3 * 4 + 4) as usize], &[1, 2, 3, 4]);
        assert!(atlas.take_upload().is_none());
    }

    #[test]
    fn the_bitmap_kit_draws_nothing_and_measures_nothing() {
        let kit = TextKit::bitmap();
        assert!(!kit.fonts_ready());
        assert_eq!(kit.width(Face::Display, "AMMO", 2.0), None);
        let mut out = Vec::new();
        assert!(!kit.emit(Face::Display, 0.0, 0.0, 2.0, [1.0; 4], "AMMO", |x, y| [x, y], &mut out));
        assert!(out.is_empty());
    }

    /// The rest need the machine's fonts. A machine without any skips, the way
    /// `textlayer`'s emoji tests do.
    fn fonts() -> Option<TextKit> {
        let kit = TextKit::blocking();
        let any = kit.inner.borrow().fonts.as_ref().is_some_and(|f| f.system.db().len() > 0);
        any.then_some(kit)
    }

    #[test]
    fn capitals_fill_the_seven_cell_box() {
        let Some(kit) = fonts() else { return };
        for face in Face::ALL {
            for scale in [2.0f32, 3.0, 4.5] {
                let mut out = Vec::new();
                kit.emit(face, 100.0, 50.0, scale, [1.0; 4], "H", |x, y| [x, y], &mut out);
                assert_eq!(out.len(), 6, "{face:?}: one quad for one glyph");
                let top = out.iter().map(|v| v.position[1]).fold(f32::MAX, f32::min);
                let bottom = out.iter().map(|v| v.position[1]).fold(f32::MIN, f32::max);
                let box_bottom = 50.0 + 7.0 * scale;
                // Within a couple of pixels: sizes are quantised to half a pixel
                // and hinting snaps stems.
                assert!((bottom - box_bottom).abs() <= 2.0, "{face:?} @{scale}: baseline {bottom} vs {box_bottom}");
                assert!((top - 50.0).abs() <= 2.0 + scale * 0.5, "{face:?} @{scale}: cap top {top}");
            }
        }
    }

    #[test]
    fn a_label_is_a_quad_per_glyph_not_per_pixel() {
        let Some(kit) = fonts() else { return };
        let mut out = Vec::new();
        kit.emit(Face::Display, 0.0, 0.0, 3.0, [1.0; 4], "AMMO 30", |x, y| [x, y], &mut out);
        // Six letters with ink, one space without.
        assert_eq!(out.len(), 6 * 6);
        assert!(out.iter().all(|v| v.uv[0] >= 0.0), "every glyph vertex samples the atlas");
    }

    #[test]
    fn the_glyphs_the_bitmap_font_lacked_now_draw() {
        let Some(kit) = fonts() else { return };
        // `·` is in the round clock; the rest are names the old font dropped.
        for s in ["·", "é", "Ж", "ß"] {
            let mut out = Vec::new();
            kit.emit(Face::Body, 0.0, 0.0, 3.0, [1.0; 4], s, |x, y| [x, y], &mut out);
            assert!(!out.is_empty(), "{s:?} drew nothing");
        }
    }

    #[test]
    fn widths_are_cached_consistent_and_tracking_free_at_the_end() {
        let Some(kit) = fonts() else { return };
        let a = kit.width(Face::Mono, "1234", 3.0).unwrap();
        let b = kit.width(Face::Mono, "1234", 3.0).unwrap();
        assert_eq!(a, b);
        // Monospace digits: every digit string of a length is one width.
        let c = kit.width(Face::Mono, "8888", 3.0).unwrap();
        assert!((a - c).abs() < 0.01, "{a} vs {c}");
        // Twice the scale, about twice the width.
        let d = kit.width(Face::Mono, "1234", 6.0).unwrap();
        assert!((d / a - 2.0).abs() < 0.1, "{a} -> {d}");
        assert_eq!(kit.width(Face::Display, "", 3.0), Some(0.0));
    }

    #[test]
    fn a_paragraph_wraps_and_finds_its_caret() {
        let Some(kit) = fonts() else { return };
        kit.begin_frame();
        let one = kit.paragraph(Face::Body, &["ALL  ", "rob", ": ", "gg"], 2.0, 400.0).unwrap();
        assert_eq!(one.lines(), 1);
        let long = "a long message that will not fit on one narrow line at all";
        let many = kit.paragraph(Face::Body, &[long], 2.0, 120.0).unwrap();
        assert!(many.lines() > 2, "{} lines", many.lines());
        assert!(many.width <= 121.0, "wrapped to {}", many.width);
        // The caret walks right along the line as the byte offset grows.
        let (a, _) = one.caret(5);
        let (b, _) = one.caret(8);
        assert!(b > a && a > 0.0, "{a} {b}");
        assert_eq!(one.caret(0), (0.0, 0.0));
    }

    /// Emit one chat-like paragraph and read back the atlas texels its quads
    /// cover: (inked texels, colourful texels).
    fn ink(kit: &TextKit, text: &str) -> (usize, usize) {
        kit.begin_frame();
        let para = kit.paragraph(Face::Body, &[text], 2.2, 900.0).unwrap();
        let mut out = Vec::new();
        kit.emit_paragraph(&para, 0.0, 0.0, &[[1.0; 4]], 1.0, |x, y| [x, y], &mut out);
        let inner = kit.inner.borrow();
        let atlas = &inner.atlas;
        let (mut inked, mut colourful) = (0, 0);
        for quad in out.chunks(6) {
            let (u0, v0) = (quad[0].uv[0], quad[0].uv[1]);
            let (u1, v1) = (quad[2].uv[0], quad[2].uv[1]);
            let s = ATLAS_SIZE as f32;
            for y in (v0 * s) as usize..(v1 * s) as usize {
                for x in (u0 * s) as usize..(u1 * s) as usize {
                    let i = (y * ATLAS_SIZE as usize + x) * 4;
                    let p = &atlas.pixels[i..i + 4];
                    if p[3] > 200 {
                        inked += 1;
                        let (r, g, b) = (p[0] as i32, p[1] as i32, p[2] as i32);
                        if (r - g).abs().max((g - b).abs()).max((r - b).abs()) > 90 {
                            colourful += 1;
                        }
                    }
                }
            }
        }
        (inked, colourful)
    }

    #[test]
    fn an_emoji_is_drawn_in_colour() {
        // Only where the OS ships a colour emoji font: nothing is bundled, so a
        // machine without one has nothing to draw an emoji *with*.
        let Some(kit) = fonts() else { return };
        let has_emoji_font = {
            let inner = kit.inner.borrow();
            let db = inner.fonts.as_ref().unwrap().system.db();
            ["Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji"]
                .iter()
                .any(|name| db.faces().any(|f| f.families.iter().any(|(n, _)| n == name)))
        };
        if !has_emoji_font {
            eprintln!("no colour emoji font on this machine; skipped");
            return;
        }
        let (_, plain) = ink(&kit, "gg wp");
        let (_, emoji) = ink(&kit, "gg \u{1F389}\u{1F525}\u{1F44D}\u{1F3FD}");
        assert!(emoji > plain + 40, "emoji drew no colour: {emoji} vs {plain}");
    }

    #[test]
    fn other_scripts_draw_glyphs_not_blanks() {
        let Some(kit) = fonts() else { return };
        for text in ["日本語のチャット", "Привет мир", "مرحبا"] {
            let (inked, _) = ink(&kit, text);
            assert!(inked > 50, "{text:?} drew nothing ({inked} inked texels)");
        }
    }

    #[test]
    fn the_atlas_reuses_a_glyph_it_already_holds() {
        let Some(kit) = fonts() else { return };
        let mut out = Vec::new();
        kit.emit(Face::Display, 0.0, 0.0, 3.0, [1.0; 4], "AAAA", |x, y| [x, y], &mut out);
        let slots = kit.inner.borrow().atlas.slots.len();
        // One per subpixel phase at most: four quarter-pixel bins.
        assert!(slots <= 4, "{slots} slots for one letter at one size");
        kit.emit(Face::Display, 0.0, 0.0, 3.0, [1.0; 4], "AAAA", |x, y| [x, y], &mut out);
        assert_eq!(kit.inner.borrow().atlas.slots.len(), slots);
    }
}
