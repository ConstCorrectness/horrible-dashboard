//! Chat in real fonts: every script, and colour emoji.
//!
//! The HUD draws with a hand-made 5×7 bitmap font, one quad per lit pixel. That
//! is right for numbers and labels this client writes itself, and hopeless for
//! text a *person* typed: every emoji, every accented capital, every non-Latin
//! script came out as blank space. So chat alone is laid out with `cosmic-text`
//! (shaping, bidi, line breaking, font fallback) and rasterized with `swash`,
//! which renders colour glyphs — COLR and bitmap emoji — as real colour.
//!
//! **The fonts are the operating system's**, discovered by `fontdb`: Segoe UI and
//! Segoe UI Emoji on Windows, Apple Color Emoji on macOS, Noto on Linux. Nothing
//! is bundled — the same rule as the maps and the sounds — and a machine missing
//! an emoji font shows the text font's monochrome glyph or nothing, never a crash.
//!
//! Three things that are bugs if changed:
//!
//! - **Font discovery is on a thread.** Scanning the system's fonts takes a few
//!   hundred milliseconds on a machine with many installed. Doing it on the first
//!   chat message would hitch mid-fight, so it starts with the client and the
//!   bitmap font covers the moment until it lands (`ready`).
//! - **Each line is its own buffer, faded while compositing.** `swash` ignores
//!   the alpha of the colour a glyph is drawn in, so a whole-paragraph layout
//!   could not fade one old line while keeping a new one solid.
//! - **Rasterized only when something changed.** The result is a texture drawn
//!   as one quad; the key (`Key`) is the chat's revision plus each visible line's
//!   fade *step*, so a line fading out costs eight rasterizations, not ninety.

use std::sync::mpsc::{self, Receiver};
use std::thread;

use cosmic_text::{Attrs, Buffer, Color, Family, FontSystem, Metrics, Shaping, SwashCache, Wrap};

use crate::chat::{ChatChannel, ChatState, CHAT_TTL};

/// How many fade steps a line goes through — the number of re-rasterizations a
/// fading line costs.
const FADE_STEPS: f32 = 8.0;
/// Seconds a line takes to fade once its time is up.
const FADE_SECS: f32 = 1.5;
/// Lines shown with the prompt closed, and with it open.
const CLOSED_LINES: usize = 8;
const OPEN_LINES: usize = 14;

/// A rasterized chat panel, in screen pixels.
pub struct ChatImage {
    pub width: u32,
    pub height: u32,
    /// Straight (not premultiplied) RGBA, sRGB-encoded, row-major.
    pub rgba: Vec<u8>,
    /// Top-left corner on screen.
    pub x: f32,
    pub y: f32,
}

#[derive(Clone, PartialEq)]
struct Key {
    revision: u64,
    open: bool,
    /// (line index, fade step) for every drawn line.
    fades: Vec<(usize, u8)>,
    screen: (u32, u32),
    unit: u32,
}

struct Fonts {
    system: FontSystem,
    cache: SwashCache,
}

pub struct TextLayer {
    loading: Option<Receiver<FontSystem>>,
    fonts: Option<Fonts>,
    key: Option<Key>,
    image: Option<ChatImage>,
}

impl TextLayer {
    /// Start discovering the system's fonts in the background.
    pub fn spawn() -> TextLayer {
        let (tx, rx) = mpsc::channel();
        let started = thread::Builder::new()
            .name("fonts".into())
            .spawn(move || {
                let _ = tx.send(FontSystem::new());
            })
            .is_ok();
        TextLayer {
            loading: started.then_some(rx),
            fonts: None,
            key: None,
            image: None,
        }
    }

    /// Load the system fonts now, on this thread — for tests and tools, where a
    /// blocking load is the point.
    pub fn blocking() -> TextLayer {
        TextLayer {
            loading: None,
            fonts: Some(Fonts {
                system: FontSystem::new(),
                cache: SwashCache::new(),
            }),
            key: None,
            image: None,
        }
    }

    /// No fonts, ever — for tests and headless runs.
    pub fn disabled() -> TextLayer {
        TextLayer {
            loading: None,
            fonts: None,
            key: None,
            image: None,
        }
    }

    /// Whether real fonts are available. Picks up the background load.
    pub fn ready(&mut self) -> bool {
        if self.fonts.is_none() {
            if let Some(rx) = &self.loading {
                if let Ok(system) = rx.try_recv() {
                    self.fonts = Some(Fonts {
                        system,
                        cache: SwashCache::new(),
                    });
                    self.loading = None;
                }
            }
        }
        self.fonts.is_some()
    }

    /// The chat panel for this frame, and whether it changed since the last one
    /// (so the renderer uploads only then). `None` when there is nothing to draw
    /// or the fonts have not loaded — the bitmap font draws chat meanwhile.
    ///
    /// `unit` is the HUD's own size unit (`u` in `hud.rs`), so chat scales with
    /// the rest of the HUD and its `hudScale` setting.
    pub fn chat(
        &mut self,
        chat: &ChatState,
        screen_w: u32,
        screen_h: u32,
        unit: f32,
    ) -> Option<(&ChatImage, bool)> {
        if !self.ready() {
            return None;
        }
        let limit = if chat.open { OPEN_LINES } else { CLOSED_LINES };
        let fades: Vec<(usize, u8)> = chat
            .messages
            .iter()
            .enumerate()
            .take(limit)
            .filter_map(|(i, m)| {
                let fade = if chat.open {
                    1.0
                } else {
                    ((CHAT_TTL - m.age) / FADE_SECS).clamp(0.0, 1.0)
                };
                let step = (fade * FADE_STEPS).ceil() as u8;
                (step > 0).then_some((i, step))
            })
            .collect();
        if fades.is_empty() && !chat.open {
            self.key = None;
            self.image = None;
            return None;
        }
        let key = Key {
            revision: chat.revision,
            open: chat.open,
            fades,
            screen: (screen_w, screen_h),
            unit: (unit * 100.0) as u32,
        };
        let changed = self.key.as_ref() != Some(&key);
        if changed {
            let fonts = self.fonts.as_mut()?;
            self.image = Some(rasterize(fonts, chat, &key, screen_w, screen_h, unit));
            self.key = Some(key);
        }
        self.image.as_ref().map(|img| (img, changed))
    }
}

/// One line to lay out: coloured spans, a backdrop, and how opaque it is.
struct Line {
    spans: Vec<(String, [u8; 4])>,
    alpha: f32,
    /// Byte offset into the concatenated text where the caret goes, for the prompt.
    caret: Option<usize>,
    backdrop: [u8; 4],
}

fn rasterize(
    fonts: &mut Fonts,
    chat: &ChatState,
    key: &Key,
    screen_w: u32,
    screen_h: u32,
    unit: f32,
) -> ChatImage {
    let font_px = (unit * 5.2).max(13.0);
    let line_h = (font_px * 1.35).ceil();
    let pad = (unit * 1.5).round().max(3.0);
    let width = ((screen_w as f32 * 0.42).clamp(360.0, 980.0)).round() as u32;
    let text_w = width as f32 - pad * 2.0;
    let metrics = Metrics::new(font_px, line_h);

    // Oldest at the top, newest just above the prompt.
    let mut lines: Vec<Line> = Vec::new();
    for &(index, step) in key.fades.iter().rev() {
        let Some(m) = chat.messages.get(index) else {
            continue;
        };
        let alpha = step as f32 / FADE_STEPS;
        if m.sender_name.is_empty() {
            // A local notice ("Not sent: slow down").
            lines.push(Line {
                spans: vec![(m.text.clone(), [248, 113, 113, 255])],
                alpha,
                caret: None,
                backdrop: [10, 12, 18, 150],
            });
            continue;
        }
        let tag = if m.is_team { "TEAM  " } else { "ALL  " };
        let tag_col = if m.is_team {
            [74, 222, 128, 255]
        } else {
            [148, 163, 184, 255]
        };
        lines.push(Line {
            spans: vec![
                (tag.to_string(), tag_col),
                // U+2068/U+2069 isolate the name, so a right-to-left name cannot
                // reorder the tag or the message around it — `<bdi>` in the pane.
                (
                    format!("\u{2068}{}\u{2069}", m.sender_name),
                    to_bytes(crate::hud::team_color(m.team)),
                ),
                (": ".to_string(), [200, 205, 215, 255]),
                (format!("\u{2068}{}\u{2069}", m.text), [240, 243, 248, 255]),
            ],
            alpha,
            caret: None,
            backdrop: [10, 12, 18, 128],
        });
    }
    if chat.open {
        let (tag, col) = match chat.channel {
            ChatChannel::All => ("ALL  ", [148, 163, 184, 255]),
            ChatChannel::Team => ("TEAM  ", [74, 222, 128, 255]),
        };
        let cursor = chat.cursor.min(chat.input.len());
        let before = &chat.input[..cursor];
        let after = &chat.input[cursor..];
        let caret = tag.len() + before.len() + chat.preedit.len();
        let mut spans = vec![
            (tag.to_string(), col),
            (before.to_string(), [255, 255, 255, 255]),
        ];
        if !chat.preedit.is_empty() {
            // Still being composed by an input method: shown, dimmer, at the caret.
            spans.push((chat.preedit.clone(), [180, 200, 255, 255]));
        }
        spans.push((after.to_string(), [255, 255, 255, 255]));
        lines.push(Line {
            spans,
            alpha: 1.0,
            caret: Some(caret),
            backdrop: [8, 10, 16, 210],
        });
    }

    // Lay every line out first, to know the panel's height.
    let mut buffers = Vec::with_capacity(lines.len());
    let mut total_h = 0.0;
    for line in &lines {
        let mut buffer = Buffer::new(&mut fonts.system, metrics);
        buffer.set_wrap(Wrap::WordOrGlyph);
        buffer.set_size(Some(text_w), None);
        let attrs = Attrs::new().family(Family::SansSerif);
        let spans: Vec<(&str, Attrs)> = line
            .spans
            .iter()
            .map(|(text, c)| {
                (
                    text.as_str(),
                    attrs.clone().color(Color::rgba(c[0], c[1], c[2], c[3])),
                )
            })
            .collect();
        buffer.set_rich_text(spans, &attrs, Shaping::Advanced, None);
        buffer.shape_until_scroll(&mut fonts.system, false);
        let rows = buffer.layout_runs().count().max(1) as f32;
        let h = rows * line_h + pad * 0.5;
        total_h += h;
        buffers.push((buffer, h));
    }
    let height = (total_h.ceil() as u32).max(1);
    let mut rgba = vec![0u8; (width * height * 4) as usize];

    let mut top = 0.0f32;
    for (line, (buffer, h)) in lines.iter().zip(buffers.iter_mut()) {
        let alpha = line.alpha;
        let used_w = buffer
            .layout_runs()
            .map(|r| r.line_w)
            .fold(0.0f32, f32::max);
        fill(
            &mut rgba,
            width,
            height,
            0,
            top as i32,
            (used_w + pad * 2.0).ceil().min(width as f32) as u32,
            h.ceil() as u32,
            line.backdrop,
            alpha,
        );
        let y0 = top + pad * 0.25;
        buffer.draw(
            &mut fonts.system,
            &mut fonts.cache,
            Color::rgba(255, 255, 255, 255),
            |x, y, w, gh, color| {
                let (r, g, b, a) = color.as_rgba_tuple();
                for dy in 0..gh as i32 {
                    for dx in 0..w as i32 {
                        blend(
                            &mut rgba,
                            width,
                            height,
                            x + dx + pad as i32,
                            y + dy + y0 as i32,
                            [r, g, b, a],
                            alpha,
                        );
                    }
                }
            },
        );
        if let Some(caret) = line.caret {
            let (cx, cy) = caret_position(buffer, caret);
            fill(
                &mut rgba,
                width,
                height,
                (cx + pad).round() as i32,
                (y0 + cy + line_h * 0.15).round() as i32,
                (unit * 0.5).max(2.0) as u32,
                (line_h * 0.7).round() as u32,
                [255, 255, 255, 230],
                1.0,
            );
        }
        top += *h;
    }

    // Anchored bottom-left, above the health panel — where the bitmap chat sat.
    let x = (unit * 6.0 - pad).max(0.0);
    let bottom = screen_h as f32 - unit * 12.0;
    ChatImage {
        width,
        height,
        rgba,
        x,
        y: (bottom - height as f32).max(0.0),
    }
}

/// Where the caret goes: just after the last glyph that ends at or before
/// `byte`, on that glyph's row. A caret at the very start sits at the origin.
fn caret_position(buffer: &Buffer, byte: usize) -> (f32, f32) {
    let mut pos = (0.0, 0.0);
    for run in buffer.layout_runs() {
        for glyph in run.glyphs {
            if glyph.end <= byte {
                pos = (glyph.x + glyph.w, run.line_top);
            }
        }
    }
    pos
}

fn to_bytes(c: [f32; 4]) -> [u8; 4] {
    [
        (c[0].clamp(0.0, 1.0) * 255.0) as u8,
        (c[1].clamp(0.0, 1.0) * 255.0) as u8,
        (c[2].clamp(0.0, 1.0) * 255.0) as u8,
        255,
    ]
}

/// Source-over one straight-alpha pixel, with an extra opacity multiplier.
fn blend(rgba: &mut [u8], w: u32, h: u32, x: i32, y: i32, src: [u8; 4], alpha: f32) {
    if x < 0 || y < 0 || x >= w as i32 || y >= h as i32 {
        return;
    }
    let sa = src[3] as f32 / 255.0 * alpha;
    if sa <= 0.0 {
        return;
    }
    let i = ((y as u32 * w + x as u32) * 4) as usize;
    let da = rgba[i + 3] as f32 / 255.0;
    let out_a = sa + da * (1.0 - sa);
    if out_a <= 0.0 {
        return;
    }
    for c in 0..3 {
        let s = src[c] as f32;
        let d = rgba[i + c] as f32;
        rgba[i + c] = ((s * sa + d * da * (1.0 - sa)) / out_a).round() as u8;
    }
    rgba[i + 3] = (out_a * 255.0).round() as u8;
}

#[allow(clippy::too_many_arguments)]
fn fill(
    rgba: &mut [u8],
    w: u32,
    h: u32,
    x: i32,
    y: i32,
    fw: u32,
    fh: u32,
    color: [u8; 4],
    alpha: f32,
) {
    for dy in 0..fh as i32 {
        for dx in 0..fw as i32 {
            blend(rgba, w, h, x + dx, y + dy, color, alpha);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blending_over_transparent_keeps_the_colour() {
        let mut px = vec![0u8; 4];
        blend(&mut px, 1, 1, 0, 0, [200, 100, 50, 255], 0.5);
        assert_eq!(&px[..3], &[200, 100, 50]);
        assert_eq!(px[3], 128);
    }

    #[test]
    fn out_of_bounds_pixels_are_ignored() {
        let mut px = vec![0u8; 4];
        blend(&mut px, 1, 1, 5, -1, [255, 255, 255, 255], 1.0);
        assert_eq!(px, vec![0, 0, 0, 0]);
    }

    /// Rasterize one line with this machine's own fonts.
    fn raster(text: &str) -> ChatImage {
        let mut layer = TextLayer::blocking();
        let mut chat = ChatState::new();
        chat.receive("id", "p1", "rob", 0, false, text);
        let (img, changed) = layer.chat(&chat, 1920, 1080, 3.0).expect("an image");
        assert!(changed);
        ChatImage {
            width: img.width,
            height: img.height,
            rgba: img.rgba.clone(),
            x: img.x,
            y: img.y,
        }
    }

    /// Opaque pixels whose channels differ a lot — colour, not grey text.
    fn colourful(img: &ChatImage) -> usize {
        img.rgba
            .chunks(4)
            .filter(|p| {
                let (r, g, b, a) = (p[0] as i32, p[1] as i32, p[2] as i32, p[3]);
                a > 200 && (r - g).abs().max((g - b).abs()).max((r - b).abs()) > 90
            })
            .count()
    }

    fn has_family(name: &str) -> bool {
        let fonts = FontSystem::new();
        let found = fonts
            .db()
            .faces()
            .any(|f| f.families.iter().any(|(n, _)| n == name));
        found
    }

    #[test]
    fn an_emoji_is_drawn_in_colour() {
        // Only where the OS ships a colour emoji font: nothing is bundled, so a
        // machine without one has nothing to draw an emoji *with*.
        if !["Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji"]
            .iter()
            .any(|f| has_family(f))
        {
            eprintln!("no colour emoji font on this machine; skipped");
            return;
        }
        let plain = colourful(&raster("gg wp"));
        let emoji = colourful(&raster("gg \u{1F389}\u{1F525}\u{1F44D}\u{1F3FD}"));
        assert!(
            emoji > plain + 40,
            "emoji drew no colour: {emoji} colourful pixels vs {plain} for plain text"
        );
    }

    #[test]
    fn other_scripts_draw_glyphs_not_blanks() {
        // A single period: the tag, the name and the backdrop, and almost no ink
        // of its own — the baseline a real line must clearly exceed.
        let blank = raster(".").rgba.chunks(4).filter(|p| p[3] > 200).count();
        for text in ["日本語のチャット", "Привет мир", "مرحبا"] {
            let inked = raster(text).rgba.chunks(4).filter(|p| p[3] > 200).count();
            assert!(
                inked > blank + 50,
                "{text:?} drew nothing ({inked} vs {blank})"
            );
        }
    }

    /// A look at the real thing: `HASSAULT_CHAT_PREVIEW=out.png cargo test --lib
    /// chat_preview -- --ignored`. Ignored by default — it writes a file.
    #[test]
    #[ignore]
    fn chat_preview() {
        let Ok(path) = std::env::var("HASSAULT_CHAT_PREVIEW") else {
            return;
        };
        let mut layer = TextLayer::blocking();
        let mut chat = ChatState::new();
        chat.receive("1", "p1", "constcorrectness", 0, false, "gg wp \u{1F389}");
        chat.receive(
            "2",
            "p2",
            "horrible",
            1,
            true,
            "rotate B, smoke mid \u{1F4A8}\u{1F525}",
        );
        chat.receive(
            "3",
            "p3",
            "\u{3055}\u{304F}\u{3089}",
            1,
            false,
            "日本語もOK \u{1F44D}\u{1F3FD}",
        );
        chat.receive(
            "4",
            "p4",
            "\u{0644}\u{064A}\u{0644}\u{0649}",
            0,
            false,
            "مرحبا 👋",
        );
        chat.receive(
            "5",
            "p1",
            "constcorrectness",
            0,
            false,
            "family \u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}\u{200D}\u{1F466} flag \u{1F1FA}\u{1F1F8}",
        );
        chat.open_prompt(ChatChannel::Team);
        chat.type_text("typing an emoji \u{1F600}");
        let (img, _) = layer.chat(&chat, 1920, 1080, 3.0).expect("an image");
        let file = std::fs::File::create(&path).expect("create");
        let mut enc = png::Encoder::new(std::io::BufWriter::new(file), img.width, img.height);
        enc.set_color(png::ColorType::Rgba);
        enc.set_depth(png::BitDepth::Eight);
        enc.write_header()
            .expect("header")
            .write_image_data(&img.rgba)
            .expect("pixels");
    }

    #[test]
    fn nothing_to_draw_is_no_image() {
        let mut layer = TextLayer::disabled();
        let chat = ChatState::new();
        assert!(layer.chat(&chat, 1920, 1080, 3.0).is_none());
    }
}
