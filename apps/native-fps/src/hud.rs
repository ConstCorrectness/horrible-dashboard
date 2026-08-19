//! The heads-up display: health, ammo, the crosshair, the kill feed.
//!
//! Everything the snapshot already carried and the native client simply did not
//! draw. `you` in a snapshot is per-recipient precisely so a client can show the
//! player their own health, magazine, reload clock and respawn clock — and until
//! now all of it arrived and was thrown away, which made the native client
//! playable only by someone who already knew the game.
//!
//! **Drawn, not composited.** There is no DOM here and no text renderer: this
//! module turns a frame's state into triangles in one pass, which
//! `renderer::Renderer::set_overlay` uploads and draws last with alpha blending
//! and no depth test. Geometry rather than a texture atlas keeps the whole HUD
//! resolution-independent and adds no asset to a repo that ships none.
//!
//! **The font is synthesized, like the sounds and the maps.** A 5×7 bitmap
//! defined in code, written as binary literals so each glyph is legible as its
//! own shape in the source. Shipping a TTF would mean shipping someone else's
//! licence for the sake of four numbers on a screen. Uppercase only, because a
//! 5×7 cell has no room for descenders and a HUD has nothing to say in prose.
//!
//! Two rules that are quietly load-bearing:
//!
//! - **Layout is in pixels, converted to NDC at the end.** A HUD laid out in
//!   normalized coordinates stretches with the window: the crosshair becomes an
//!   oblong on an ultrawide monitor, and a crosshair whose arms differ in length
//!   by aspect ratio is a crosshair that lies about the spread it is drawing.
//! - **Nothing here invents a fact.** The hitmarker comes from `you.hits`, which
//!   the server drains as it builds the envelope, and the muzzle flash from the
//!   `shot` effect. A marker driven by the fire key lights up on shots that hit
//!   a wall — which is the same class of lie as drawing footstep positions.

use std::collections::VecDeque;

use crate::protocol::{Fx, HitMarker, SelfState};

/// One overlay vertex: a position already in clip space, and a colour with an
/// alpha the blend state actually uses.
#[repr(C)]
#[derive(Copy, Clone, Debug, bytemuck::Pod, bytemuck::Zeroable)]
pub struct OverlayVertex {
    pub position: [f32; 2],
    pub color: [f32; 4],
}

/// How long a kill note stays up. The browser client's `KILL_TTL_MS`.
const KILL_TTL: f32 = 6.0;

/// How long a hitmarker and a damage flash stay on screen.
const MARKER_LIFE: f32 = 0.18;
const FLASH_LIFE: f32 = 0.35;

/// The most kill notes drawn at once, newest first — the browser's five.
const MAX_FEED: usize = 5;

const WHITE: [f32; 4] = [0.92, 0.94, 0.96, 0.9];
const DIM: [f32; 4] = [0.72, 0.76, 0.80, 0.65];
const AMBER: [f32; 4] = [0.94, 0.83, 0.54, 0.95];
const RED: [f32; 4] = [0.97, 0.32, 0.28, 0.95];
const GREEN: [f32; 4] = [0.49, 0.91, 0.53, 0.95];
const PANEL: [f32; 4] = [0.05, 0.07, 0.09, 0.45];

/// The margin every edge-anchored block keeps, in HUD units. Deliberately more
/// than a couple of pixels: a HUD flush against the edge is the first thing an
/// overscanning display or a window manager's shadow eats.
const MARGIN: f32 = 5.0;

/// One line of the kill feed, already phrased.
#[derive(Debug, Clone)]
pub struct KillNote {
    pub text: String,
    /// Whether we did it or it was done to us — worth colouring differently.
    pub mine: bool,
    pub age: f32,
}

/// Everything about this frame that is not already inside `Hud`.
pub struct HudView<'a> {
    pub width: u32,
    pub height: u32,
    /// The private half of the snapshot. `None` in Train, which has no server
    /// and therefore no health, ammo or respawn clock to report — drawing a
    /// hundred hit points there would be a number this client made up.
    pub you: Option<&'a SelfState>,
    pub weapon_name: &'a str,
    /// The cone the **next** shot would use, in radians — a weapon's
    /// `hipfireSpread`, served like every other weapon number.
    ///
    /// Passed as the cone rather than as a gap in pixels so the conversion lives
    /// here with the rest of the layout. Drawing `spread` instead would hide the
    /// hip-fire penalty, which is the one number an unscoped sniper is about.
    pub spread: f32,
    /// Scope magnification: 1 when unscoped, so a caller can pass it blind.
    ///
    /// Above 1 the crosshair is replaced by the sight, whose blacked-out surround
    /// is the **mechanical** half of a scope and not decoration: it is what the
    /// magnification *costs*. A zoom with a clear view all round would be a free
    /// upgrade rather than the trade the weapon is built around.
    pub magnification: f32,
    /// Horizontal speed and the run speed it is measured against — the number
    /// the movement is *about*, and the only way a chained jump is learnable.
    pub speed: f32,
    pub move_speed: f32,
    pub on_ground: bool,
    pub crouching: bool,
    /// Whether there is a body in the world to report on — deployed and not
    /// still connecting. Deliberately **not** "the pointer is captured":
    /// releasing the pointer is not a menu here, and a world drawn with no HUD
    /// in it reads as a client that half-loaded.
    pub playing: bool,
}

/// The HUD's own memory: things that persist across frames because they are
/// *events*, not state the snapshot repeats.
pub struct Hud {
    feed: VecDeque<KillNote>,
    /// Seconds since the last landed hit, and whether it killed.
    hit_age: f32,
    hit_killed: bool,
    /// Seconds since we last took damage.
    damage_age: f32,
    /// The health we last saw, so a drop can be told from a snapshot repeating.
    last_hp: f32,
    /// Fall damage from the most recent landing, and how long ago.
    fell: f32,
    fell_age: f32,
}

impl Default for Hud {
    fn default() -> Hud {
        Hud {
            feed: VecDeque::new(),
            hit_age: f32::MAX,
            hit_killed: false,
            damage_age: f32::MAX,
            last_hp: f32::MAX,
            fell: 0.0,
            fell_age: f32::MAX,
        }
    }
}

impl Hud {
    /// Fold one effect into the feed.
    pub fn on_fx(&mut self, fx: &Fx, self_id: &str) {
        let Fx::Kill {
            victim,
            victim_name,
            killer,
            killer_name,
            head,
            ..
        } = fx
        else {
            return;
        };
        // An empty killer is what the feed reads as "the map did it" — a fall,
        // which goes through `_fall_damage` and has no killer by construction.
        let text = if killer.is_empty() {
            format!("{} FELL", name_of(victim_name, victim))
        } else {
            format!(
                "{} {} {}",
                name_of(killer_name, killer),
                if *head { "X" } else { ">" },
                name_of(victim_name, victim)
            )
        };
        let mine = killer == self_id || victim == self_id;
        self.feed.push_front(KillNote {
            text,
            mine,
            age: 0.0,
        });
        self.feed.truncate(MAX_FEED);
    }

    /// Hits we landed, as the server counted them.
    pub fn on_hits(&mut self, hits: &[HitMarker]) {
        if hits.is_empty() {
            return;
        }
        self.hit_age = 0.0;
        // A burst that kills is a kill marker even if the other pellets only
        // wounded: the louder of the two is the one worth showing.
        self.hit_killed = hits.iter().any(|h| h.killed);
    }

    /// The private half of a snapshot, for the things derived from a *change*.
    pub fn on_self(&mut self, you: &SelfState) {
        if you.hp < self.last_hp && self.last_hp != f32::MAX {
            self.damage_age = 0.0;
        }
        self.last_hp = you.hp;
        if you.fell > 0.0 {
            self.fell = you.fell;
            self.fell_age = 0.0;
        }
    }

    /// A respawn: the health jump it causes is not damage in reverse, and the
    /// last-seen value has to move with it or the next real hit flashes twice.
    pub fn on_respawn(&mut self) {
        self.last_hp = f32::MAX;
        self.damage_age = f32::MAX;
        self.fell_age = f32::MAX;
    }

    pub fn update(&mut self, dt: f32) {
        self.hit_age = advance(self.hit_age, dt);
        self.damage_age = advance(self.damage_age, dt);
        self.fell_age = advance(self.fell_age, dt);
        for note in &mut self.feed {
            note.age += dt;
        }
        while self.feed.back().is_some_and(|n| n.age > KILL_TTL) {
            self.feed.pop_back();
        }
    }

    #[cfg(test)]
    fn feed_texts(&self) -> Vec<String> {
        self.feed.iter().map(|n| n.text.clone()).collect()
    }

    /// This frame's triangles.
    pub fn build(&self, view: &HudView, out: &mut Vec<OverlayVertex>) {
        out.clear();
        let mut p = Painter {
            out,
            width: view.width.max(1) as f32,
            height: view.height.max(1) as f32,
        };
        // One unit of HUD, in pixels. Derived from the window rather than fixed,
        // so the same layout is legible on a 720p laptop and on a 4K monitor
        // instead of shrinking to a smear on the second.
        let u = (p.height / 360.0).round().max(2.0);
        let hit = self.hit_age < MARKER_LIFE;

        if self.damage_age < FLASH_LIFE {
            // An edge wash rather than a full-screen tint: taking a hit must not
            // hide the person who is shooting at you.
            let a = 0.45 * (1.0 - self.damage_age / FLASH_LIFE);
            p.vignette([0.85, 0.12, 0.12, a], u * 14.0);
        }

        self.paint_feed(&mut p, u);

        if !view.playing {
            // Not in the world yet: connecting, or between matches. The kill feed
            // above is the one thing that still makes sense there.
            return;
        }

        let dead = view.you.is_some_and(|y| !y.alive);
        if !dead && view.magnification > 1.0 {
            p.scope(u, hit, self.hit_killed, view.magnification);
        } else if !dead {
            // The browser's `crosshairSpread`, in its units, scaled to this
            // window's. Same curve, so the two clients' crosshairs open by the
            // same amount for the same weapon.
            let gap = (4.0 + view.spread * 260.0) * u * 0.5;
            p.crosshair(gap.max(2.0), u, hit, self.hit_killed);
        }

        self.paint_health(&mut p, view, u);
        paint_weapon(&mut p, view, u);
        self.paint_center(&mut p, view, u);
        paint_movement(&mut p, view, u);
    }

    fn paint_feed(&self, p: &mut Painter, u: f32) {
        let scale = u * 0.75;
        let mut y = u * 6.0;
        for note in &self.feed {
            // The last second is a fade rather than a disappearance, so a line
            // does not vanish mid-read.
            let fade = ((KILL_TTL - note.age) / 1.0).clamp(0.0, 1.0);
            let mut color = if note.mine { AMBER } else { DIM };
            color[3] *= fade;
            let w = text_width(&note.text, scale);
            let x = p.width - w - u * 6.0;
            p.rect(
                x - scale,
                y - scale,
                w + scale * 2.0,
                7.0 * scale + scale * 2.0,
                [PANEL[0], PANEL[1], PANEL[2], PANEL[3] * fade],
            );
            p.text(x, y, scale, color, &note.text);
            y += 7.0 * scale + u * 3.0;
        }
    }

    fn paint_health(&self, p: &mut Painter, view: &HudView, u: f32) {
        let Some(you) = view.you else { return };
        let big = u * 2.4;
        let small = u * 0.8;
        let left = u * 6.0;
        // Laid out **upwards from the movement line**, which owns the bottom margin.
        // Anchoring downwards from a top instead puts the last line off the bottom
        // of the screen on a small window — where it is not clipped with a warning,
        // it is simply absent.
        let bar_h = u * 1.8;
        let bar_y = p.height - MARGIN * u - 7.0 * (u * 0.75) - u * 1.5 - bar_h;
        let number_y = bar_y - u * 2.0 - 7.0 * big;

        let hp = you.hp.max(0.0).round() as i32;
        let color = if you.hp > 30.0 { WHITE } else { RED };
        let label = hp.to_string();
        p.text(left, number_y, big, color, &label);
        p.text(
            left + text_width(&label, big) + small * 2.0,
            number_y + 7.0 * big - 7.0 * small,
            small,
            DIM,
            "HP",
        );
        // A bar as well as a number: a number is exact and a bar is instant, and
        // in a firefight only one of those gets read.
        let bar_w = u * 44.0;
        p.rect(left, bar_y, bar_w, bar_h, [0.1, 0.12, 0.15, 0.7]);
        let frac = (you.hp / 100.0).clamp(0.0, 1.0);
        p.rect(left, bar_y, bar_w * frac, bar_h, color);
        if you.protected {
            p.text(
                left + bar_w + u * 2.0,
                bar_y - u * 0.5,
                small,
                AMBER,
                "SPAWN SHIELD",
            );
        }
    }

    fn paint_center(&self, p: &mut Painter, view: &HudView, u: f32) {
        let Some(you) = view.you else { return };
        let scale = u * 0.9;
        if !you.alive {
            let big = u * 1.6;
            p.center_text(p.height * 0.42, big, RED, "DEAD");
            let line = format!("RESPAWN IN {:.1}", you.respawn_in.max(0.0));
            p.center_text(p.height * 0.42 + 9.0 * big, scale, DIM, &line);
            return;
        }
        if self.fell_age < 1.2 && self.fell > 0.0 {
            // Fall damage has no killer, so it needs saying: without it a chunk
            // of health simply vanishes on landing and reads as a hit from
            // someone you never saw.
            let line = format!("-{} FALL", self.fell.round() as i32);
            p.center_text(p.height * 0.58, scale, [0.98, 0.62, 0.58, 0.9], &line);
        }
    }
}

fn paint_weapon(p: &mut Painter, view: &HudView, u: f32) {
    let Some(you) = view.you else { return };
    let big = u * 2.4;
    let small = u * 0.8;
    let right = p.width - u * 6.0;
    // Upwards from the bottom margin, like the health block — and the reload
    // line is *always* accounted for, so the ammo counter does not jump a line
    // every time a magazine runs out.
    let reload_y = p.height - MARGIN * u - 7.0 * small;
    let ammo_y = reload_y - u * 2.0 - 7.0 * big;
    let name_y = ammo_y - u * 1.5 - 7.0 * small;

    p.text_right(right, name_y, small, DIM, &view.weapon_name.to_uppercase());

    // A magazine of zero is a weapon that has none — the knife — and "0 rounds
    // left" is a different statement from "this does not take rounds".
    let (ammo, reserve) = if you.mag > 0 {
        (
            you.ammo.to_string(),
            if you.reserve < 0 {
                // Bottomless, the sidearm's supply.
                "∞".to_string()
            } else {
                you.reserve.to_string()
            },
        )
    } else {
        ("-".to_string(), String::new())
    };
    let tail = if reserve.is_empty() {
        String::new()
    } else {
        format!(" / {reserve}")
    };
    let tail_w = text_width(&tail, small);
    let ammo_color = if you.mag > 0 && you.ammo == 0 {
        RED
    } else {
        WHITE
    };
    p.text_right(right - tail_w, ammo_y, big, ammo_color, &ammo);
    if !tail.is_empty() {
        p.text_right(right, ammo_y + 7.0 * big - 7.0 * small, small, DIM, &tail);
    }
    if you.reloading {
        p.text_right(right, reload_y, small, AMBER, "RELOADING");
    }
}

fn paint_movement(p: &mut Painter, view: &HudView, u: f32) {
    let scale = u * 0.75;
    let y = p.height - MARGIN * u - 7.0 * scale;
    let mut line = format!("{:.1} / {:.0} C/S", view.speed, view.move_speed.round());
    if view.crouching {
        line.push_str(" - CROUCHED");
    }
    if !view.on_ground {
        line.push_str(" - AIRBORNE");
    }
    // Over the run speed means a chained jump landed, which is the one thing in
    // this movement model you cannot feel without being told.
    let color = if view.speed > view.move_speed + 0.5 {
        GREEN
    } else {
        DIM
    };
    p.text(u * 6.0, y, scale, color, &line);
}

/// A name to draw, falling back to the id when the server sent none.
fn name_of(name: &str, id: &str) -> String {
    let raw = if name.is_empty() { id } else { name };
    // Uppercased because the font has one case, and clipped because a long
    // handle would otherwise push the feed off its own side of the screen.
    let upper: String = raw.to_uppercase();
    if upper.chars().count() > 14 {
        upper.chars().take(14).collect()
    } else {
        upper
    }
}

/// Age a timer without letting it wrap. `f32::MAX + dt` is still `f32::MAX`, but
/// a counter that keeps climbing is one that loses precision for no purpose.
fn advance(age: f32, dt: f32) -> f32 {
    if age > 1e6 {
        age
    } else {
        age + dt
    }
}

/// Turns pixel-space rectangles and text into clip-space triangles.
struct Painter<'a> {
    out: &'a mut Vec<OverlayVertex>,
    width: f32,
    height: f32,
}

impl Painter<'_> {
    /// Pixels (top-left origin, y down) to clip space (centre origin, y up).
    fn ndc(&self, x: f32, y: f32) -> [f32; 2] {
        [x / self.width * 2.0 - 1.0, 1.0 - y / self.height * 2.0]
    }

    fn rect(&mut self, x: f32, y: f32, w: f32, h: f32, color: [f32; 4]) {
        if w <= 0.0 || h <= 0.0 || color[3] <= 0.0 {
            return;
        }
        let a = self.ndc(x, y);
        let b = self.ndc(x + w, y);
        let c = self.ndc(x + w, y + h);
        let d = self.ndc(x, y + h);
        for position in [a, b, c, a, c, d] {
            self.out.push(OverlayVertex { position, color });
        }
    }

    /// A thick line between two points — the only thing here that is not axis
    /// aligned, and it exists for the hitmarker's X.
    fn line(&mut self, x0: f32, y0: f32, x1: f32, y1: f32, thickness: f32, color: [f32; 4]) {
        let (dx, dy) = (x1 - x0, y1 - y0);
        let len = (dx * dx + dy * dy).sqrt();
        if len <= 0.0 {
            return;
        }
        // The perpendicular, at half thickness either side.
        let (px, py) = (-dy / len * thickness * 0.5, dx / len * thickness * 0.5);
        let a = self.ndc(x0 + px, y0 + py);
        let b = self.ndc(x1 + px, y1 + py);
        let c = self.ndc(x1 - px, y1 - py);
        let d = self.ndc(x0 - px, y0 - py);
        for position in [a, b, c, a, c, d] {
            self.out.push(OverlayVertex { position, color });
        }
    }

    /// A wash around the four edges of the screen.
    fn vignette(&mut self, color: [f32; 4], band: f32) {
        let (w, h) = (self.width, self.height);
        self.rect(0.0, 0.0, w, band, color);
        self.rect(0.0, h - band, w, band, color);
        self.rect(0.0, 0.0, band, h, color);
        self.rect(w - band, 0.0, band, h, color);
    }

    /// The crosshair, and the hitmarker that replaces it.
    ///
    /// The marker rotates the ticks into an X rather than adding a second
    /// element, which reads instantly and needs nothing to fade in and out —
    /// the browser client does exactly the same with a CSS transform.
    fn crosshair(&mut self, gap: f32, u: f32, hit: bool, killed: bool) {
        let color = if killed {
            RED
        } else if hit {
            AMBER
        } else {
            WHITE
        };
        let (cx, cy) = (self.width / 2.0, self.height / 2.0);
        let arm = u * 3.0;
        let thick = (u * 0.6).max(1.0);
        self.rect(cx - thick, cy - thick, thick * 2.0, thick * 2.0, color);
        if hit {
            for (sx, sy) in [(-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0), (1.0, 1.0)] {
                let d = std::f32::consts::FRAC_1_SQRT_2;
                self.line(
                    cx + sx * gap * d,
                    cy + sy * gap * d,
                    cx + sx * (gap + arm) * d,
                    cy + sy * (gap + arm) * d,
                    thick * 2.0,
                    color,
                );
            }
            return;
        }
        self.rect(cx - gap - arm, cy - thick, arm, thick * 2.0, color);
        self.rect(cx + gap, cy - thick, arm, thick * 2.0, color);
        self.rect(cx - thick, cy - gap - arm, thick * 2.0, arm, color);
        self.rect(cx - thick, cy + gap, thick * 2.0, arm, color);
    }

    /// A filled annulus, as a fan of quads.
    ///
    /// The one curved thing the HUD draws. Kept in *pixels* on both axes rather
    /// than as a fraction of each, so the sight stays circular in a window of any
    /// shape instead of stretching into an ellipse — which would misreport where
    /// the edge of the scope's view actually is.
    fn ring(&mut self, cx: f32, cy: f32, inner: f32, outer: f32, color: [f32; 4]) {
        const SEGMENTS: usize = 72;
        for i in 0..SEGMENTS {
            let a0 = (i as f32 / SEGMENTS as f32) * std::f32::consts::TAU;
            let a1 = ((i + 1) as f32 / SEGMENTS as f32) * std::f32::consts::TAU;
            let quad = [
                self.ndc(cx + a0.cos() * inner, cy + a0.sin() * inner),
                self.ndc(cx + a1.cos() * inner, cy + a1.sin() * inner),
                self.ndc(cx + a1.cos() * outer, cy + a1.sin() * outer),
                self.ndc(cx + a0.cos() * outer, cy + a0.sin() * outer),
            ];
            for idx in [0usize, 1, 2, 0, 2, 3] {
                self.out.push(OverlayVertex {
                    position: quad[idx],
                    color,
                });
            }
        }
    }

    /// The sniper's sight: a vignette, two hairlines and the magnification.
    ///
    /// Drawn rather than sampled, like everything else here — two rings and a few
    /// lines is all a scope actually is, and a texture would be an asset.
    fn scope(&mut self, u: f32, hit: bool, killed: bool, magnification: f32) {
        let color = if killed {
            RED
        } else if hit {
            AMBER
        } else {
            // Green, so the sight is never mistaken for the ordinary crosshair
            // at a glance.
            [0.86, 1.0, 0.86, 0.85]
        };
        let (cx, cy) = (self.width / 2.0, self.height / 2.0);
        let sight = self.width.min(self.height) * 0.31;
        // Far enough out to cover the corners of any window: the diagonal, which
        // is the largest distance from the centre to a corner.
        let corner = (self.width * self.width + self.height * self.height).sqrt();
        self.ring(cx, cy, sight, sight * 1.07, [0.0, 0.0, 0.0, 0.55]);
        self.ring(cx, cy, sight * 1.07, corner, [0.0, 0.0, 0.0, 0.97]);

        // Hairlines across the whole sight, with a gap at the centre — so the
        // thing being aimed at is never behind the reticle drawing it.
        let thin = (u * 0.3).max(1.0);
        let gap = sight * 0.12;
        let mut cross = color;
        cross[3] *= 0.6;
        self.rect(cx - sight, cy - thin * 0.5, sight - gap, thin, cross);
        self.rect(cx + gap, cy - thin * 0.5, sight - gap, thin, cross);
        self.rect(cx - thin * 0.5, cy - sight, thin, sight - gap, cross);
        self.rect(cx - thin * 0.5, cy + gap, thin, sight - gap, cross);

        // The centre dot, which is where the shot goes.
        let dot = (u * 0.6).max(1.0);
        self.rect(cx - dot, cy - dot, dot * 2.0, dot * 2.0, color);

        let scale = u * 0.8;
        let label = if magnification.fract().abs() < 0.05 {
            format!("{}X", magnification.round() as i32)
        } else {
            format!("{magnification:.1}X")
        };
        let x = (self.width - text_width(&label, scale)) / 2.0;
        self.text(x, cy + sight * 0.72, scale, DIM, &label);
    }

    fn text(&mut self, x: f32, y: f32, scale: f32, color: [f32; 4], s: &str) {
        let mut cx = x;
        for ch in s.chars() {
            let glyph = glyph(ch);
            for (row, bits) in glyph.iter().enumerate() {
                for col in 0..5 {
                    if bits & (1 << (4 - col)) != 0 {
                        self.rect(
                            cx + col as f32 * scale,
                            y + row as f32 * scale,
                            // A hair over one unit, so neighbouring pixels of a
                            // stroke meet instead of showing a seam at
                            // fractional scales.
                            scale * 1.02,
                            scale * 1.02,
                            color,
                        );
                    }
                }
            }
            cx += 6.0 * scale;
        }
    }

    fn text_right(&mut self, right: f32, y: f32, scale: f32, color: [f32; 4], s: &str) {
        self.text(right - text_width(s, scale), y, scale, color, s);
    }

    fn center_text(&mut self, y: f32, scale: f32, color: [f32; 4], s: &str) {
        let x = (self.width - text_width(s, scale)) / 2.0;
        self.text(x, y, scale, color, s);
    }
}

/// Width of a string in pixels — five columns per glyph and one of spacing,
/// minus the trailing space so right-aligned text ends where it says it does.
fn text_width(s: &str, scale: f32) -> f32 {
    let n = s.chars().count() as f32;
    if n == 0.0 {
        0.0
    } else {
        n * 6.0 * scale - scale
    }
}

/// The font: 5 wide, 7 tall, one bit per pixel, high bit leftmost.
///
/// Written as binary literals so every glyph is drawn in the source it is
/// defined in — which is the only review a hand-made font can get.
#[rustfmt::skip]
fn glyph(ch: char) -> [u8; 7] {
    match ch.to_ascii_uppercase() {
        'A' => [0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
        'B' => [0b11110, 0b10001, 0b10001, 0b11110, 0b10001, 0b10001, 0b11110],
        'C' => [0b01110, 0b10001, 0b10000, 0b10000, 0b10000, 0b10001, 0b01110],
        'D' => [0b11110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11110],
        'E' => [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111],
        'F' => [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000],
        'G' => [0b01110, 0b10001, 0b10000, 0b10111, 0b10001, 0b10001, 0b01111],
        'H' => [0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
        'I' => [0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b11111],
        'J' => [0b00111, 0b00010, 0b00010, 0b00010, 0b00010, 0b10010, 0b01100],
        'K' => [0b10001, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010, 0b10001],
        'L' => [0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b11111],
        'M' => [0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001],
        'N' => [0b10001, 0b11001, 0b10101, 0b10011, 0b10001, 0b10001, 0b10001],
        'O' => [0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
        'P' => [0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000],
        'Q' => [0b01110, 0b10001, 0b10001, 0b10001, 0b10101, 0b10010, 0b01101],
        'R' => [0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001],
        'S' => [0b01111, 0b10000, 0b10000, 0b01110, 0b00001, 0b00001, 0b11110],
        'T' => [0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100],
        'U' => [0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110],
        'V' => [0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01010, 0b00100],
        'W' => [0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b11011, 0b10001],
        'X' => [0b10001, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b10001],
        'Y' => [0b10001, 0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100],
        'Z' => [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b10000, 0b11111],
        '0' => [0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110],
        '1' => [0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
        '2' => [0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b01000, 0b11111],
        '3' => [0b11111, 0b00010, 0b00100, 0b00010, 0b00001, 0b10001, 0b01110],
        '4' => [0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010],
        '5' => [0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110],
        '6' => [0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110],
        '7' => [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000],
        '8' => [0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110],
        '9' => [0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100],
        '.' => [0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b01100, 0b01100],
        ':' => [0b00000, 0b01100, 0b01100, 0b00000, 0b01100, 0b01100, 0b00000],
        '/' => [0b00001, 0b00010, 0b00010, 0b00100, 0b01000, 0b01000, 0b10000],
        '-' => [0b00000, 0b00000, 0b00000, 0b11111, 0b00000, 0b00000, 0b00000],
        '+' => [0b00000, 0b00100, 0b00100, 0b11111, 0b00100, 0b00100, 0b00000],
        '>' => [0b01000, 0b00100, 0b00010, 0b00001, 0b00010, 0b00100, 0b01000],
        '<' => [0b00010, 0b00100, 0b01000, 0b10000, 0b01000, 0b00100, 0b00010],
        '%' => [0b11001, 0b11010, 0b00010, 0b00100, 0b01000, 0b01011, 0b10011],
        '!' => [0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00000, 0b00100],
        '?' => [0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b00000, 0b00100],
        '_' => [0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b11111],
        // Bottomless reserve. The browser writes ∞ and so does this, rather than
        // a large number that looks like a count.
        '∞' => [0b00000, 0b00000, 0b01010, 0b10101, 0b10101, 0b01010, 0b00000],
        // Anything the font has no shape for, space included, is blank. A
        // missing glyph should be a hole in a word, never a box that reads as a
        // deliberate character.
        _ => [0; 7],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn kill(killer: &str, victim: &str, head: bool) -> Fx {
        Fx::Kill {
            victim: victim.into(),
            victim_name: victim.to_uppercase(),
            killer: killer.into(),
            killer_name: killer.to_uppercase(),
            weapon: "assault".into(),
            head,
        }
    }

    fn view<'a>(you: Option<&'a SelfState>) -> HudView<'a> {
        HudView {
            width: 1280,
            height: 800,
            you,
            weapon_name: "assault rifle",
            spread: 0.02,
            magnification: 1.0,
            speed: 0.0,
            move_speed: 22.0,
            on_ground: true,
            crouching: false,
            playing: true,
        }
    }

    fn alive() -> SelfState {
        SelfState {
            hp: 100.0,
            alive: true,
            ammo: 20,
            reserve: 60,
            mag: 30,
            ..Default::default()
        }
    }

    #[test]
    fn a_fall_has_no_killer_and_says_so() {
        // The server emits a kill with an empty killer for fall damage. Phrased
        // as "X > Y" with a blank on the left it would read as an invisible
        // player having done it.
        let mut hud = Hud::default();
        hud.on_fx(&kill("", "rob", false), "rob");
        assert_eq!(hud.feed_texts(), vec!["ROB FELL".to_string()]);
    }

    #[test]
    fn the_feed_is_newest_first_capped_and_expires() {
        let mut hud = Hud::default();
        for i in 0..8 {
            hud.on_fx(&kill("a", &format!("v{i}"), false), "me");
        }
        let texts = hud.feed_texts();
        assert_eq!(texts.len(), MAX_FEED);
        assert!(texts[0].contains("V7"), "{texts:?}");
        hud.update(KILL_TTL + 0.1);
        assert!(hud.feed_texts().is_empty());
    }

    #[test]
    fn a_headshot_is_marked_and_our_own_kills_are_ours() {
        let mut hud = Hud::default();
        hud.on_fx(&kill("me", "them", true), "me");
        assert_eq!(hud.feed_texts()[0], "ME X THEM");
        assert!(hud.feed.front().unwrap().mine);
        hud.on_fx(&kill("a", "b", false), "me");
        assert!(!hud.feed.front().unwrap().mine);
    }

    #[test]
    fn effects_that_are_not_kills_do_not_reach_the_feed() {
        let mut hud = Hud::default();
        hud.on_fx(
            &Fx::Shot {
                id: "me".into(),
                weapon: 2,
                hit: true,
            },
            "me",
        );
        hud.on_fx(&Fx::Other, "me");
        assert!(hud.feed_texts().is_empty());
    }

    #[test]
    fn a_hitmarker_lasts_a_moment_and_a_kill_colours_it() {
        let mut hud = Hud::default();
        hud.on_hits(&[
            HitMarker {
                damage: 20.0,
                ..Default::default()
            },
            HitMarker {
                damage: 5.0,
                killed: true,
                ..Default::default()
            },
        ]);
        assert!(hud.hit_age < MARKER_LIFE);
        // A burst where one pellet finished them is a kill marker, not a hit.
        assert!(hud.hit_killed);
        hud.update(MARKER_LIFE + 0.01);
        assert!(hud.hit_age > MARKER_LIFE);
    }

    #[test]
    fn the_damage_flash_fires_on_a_drop_and_not_on_a_repeat() {
        let mut hud = Hud::default();
        let mut you = alive();
        hud.on_self(&you);
        // The first snapshot is not damage: there is nothing to have dropped
        // from, and flashing on join would be a hit nobody landed.
        assert!(hud.damage_age > FLASH_LIFE);
        hud.on_self(&you);
        assert!(hud.damage_age > FLASH_LIFE);
        you.hp = 70.0;
        hud.on_self(&you);
        assert!(hud.damage_age < FLASH_LIFE);
    }

    #[test]
    fn respawning_is_not_healing_and_does_not_arm_the_next_flash() {
        // Health jumps back to 100 on respawn. Left as the last-seen value, the
        // first hit of the next life would flash correctly — but the *respawn*
        // itself must not, and the fall note from the death must not linger.
        let mut hud = Hud::default();
        let mut you = alive();
        you.hp = 12.0;
        hud.on_self(&you);
        hud.on_respawn();
        you.hp = 100.0;
        hud.on_self(&you);
        assert!(hud.damage_age > FLASH_LIFE);
    }

    #[test]
    fn a_hud_is_geometry_and_only_the_feed_survives_leaving_the_world() {
        let hud = Hud::default();
        let you = alive();
        let mut out = Vec::new();
        hud.build(&view(Some(&you)), &mut out);
        let playing = out.len();
        assert!(playing > 0, "nothing was drawn");
        assert_eq!(playing % 3, 0, "triangles come in threes");

        let mut idle = view(Some(&you));
        idle.playing = false;
        hud.build(&idle, &mut out);
        assert!(
            out.is_empty(),
            "the HUD stayed up with the pointer released"
        );
    }

    #[test]
    fn train_draws_a_crosshair_but_invents_no_health() {
        // No server means no health, ammo or respawn clock. Drawing 100 hp there
        // would be a number this client made up.
        let hud = Hud::default();
        let mut out = Vec::new();
        hud.build(&view(None), &mut out);
        let without = out.len();
        let you = alive();
        hud.build(&view(Some(&you)), &mut out);
        assert!(without > 0, "no crosshair in training");
        assert!(out.len() > without, "health and ammo were not drawn");
    }

    #[test]
    fn everything_drawn_lands_on_the_screen() {
        // Clip space is -1..1 and there is no scissor: geometry outside it is
        // silently invisible, which would make a layout bug look like a HUD that
        // simply does not draw that element.
        let mut hud = Hud::default();
        hud.on_fx(&kill("averyverylongname", "another", true), "me");
        let mut you = alive();
        you.alive = false;
        you.respawn_in = 3.5;
        you.reserve = -1;
        you.reloading = true;
        let mut out = Vec::new();
        hud.build(&view(Some(&you)), &mut out);
        for v in &out {
            assert!(
                v.position[0] >= -1.001 && v.position[0] <= 1.001,
                "x off screen: {:?}",
                v.position
            );
            assert!(
                v.position[1] >= -1.001 && v.position[1] <= 1.001,
                "y off screen: {:?}",
                v.position
            );
        }
    }

    #[test]
    fn a_scope_replaces_the_crosshair_and_costs_peripheral_vision() {
        // The blacked-out surround is the mechanical half: a zoom with a clear
        // view all round would be a free upgrade rather than a trade.
        let hud = Hud::default();
        let you = alive();
        let mut plain = Vec::new();
        hud.build(&view(Some(&you)), &mut plain);
        let mut scoped_view = view(Some(&you));
        scoped_view.magnification = 4.0;
        let mut scoped = Vec::new();
        hud.build(&scoped_view, &mut scoped);
        assert!(scoped.len() > plain.len(), "the sight drew nothing");
        // And the surround genuinely covers the corners of the window, at any
        // aspect ratio — a ring sized from one axis leaves the other's corners
        // clear, which is a scope that costs nothing on an ultrawide.
        let corner = scoped
            .iter()
            .any(|v| v.position[0].abs() > 0.99 && v.position[1].abs() > 0.99);
        assert!(corner, "the vignette left the corners of the screen open");
    }

    #[test]
    fn a_dead_player_gets_neither_crosshair_nor_sight() {
        let hud = Hud::default();
        let mut you = alive();
        you.alive = false;
        let mut scoped_view = view(Some(&you));
        scoped_view.magnification = 4.0;
        let mut out = Vec::new();
        hud.build(&scoped_view, &mut out);
        // Nothing centred on the crosshair: what is drawn is the death notice,
        // well away from the middle of the screen.
        assert!(
            !out.iter()
                .any(|v| v.position[0].abs() < 0.01 && v.position[1].abs() < 0.01),
            "a sight was drawn over a dead player"
        );
    }

    #[test]
    fn pixels_map_to_clip_space_with_y_flipped() {
        // Pixels count down from the top and clip space counts up from the
        // middle. Getting this backwards draws a HUD that is upside down but
        // otherwise perfect, which is a screenshot away from looking deliberate.
        let mut out = Vec::new();
        let p = Painter {
            out: &mut out,
            width: 800.0,
            height: 600.0,
        };
        assert_eq!(p.ndc(0.0, 0.0), [-1.0, 1.0]);
        assert_eq!(p.ndc(800.0, 600.0), [1.0, -1.0]);
        assert_eq!(p.ndc(400.0, 300.0), [0.0, 0.0]);
    }

    #[test]
    fn a_zero_sized_window_does_not_divide_by_zero() {
        let hud = Hud::default();
        let you = alive();
        let mut v = view(Some(&you));
        v.width = 0;
        v.height = 0;
        let mut out = Vec::new();
        hud.build(&v, &mut out);
        assert!(out.iter().all(|x| x.position.iter().all(|c| c.is_finite())));
    }

    #[test]
    fn a_magazineless_weapon_shows_a_dash_rather_than_zero_rounds() {
        // The knife has `mag: 0`. "0" there says it is empty, which is a
        // different claim from "this does not take rounds".
        let mut you = alive();
        you.mag = 0;
        you.ammo = 0;
        let mut out = Vec::new();
        let hud = Hud::default();
        hud.build(&view(Some(&you)), &mut out);
        let knife = out.len();
        you.mag = 30;
        you.ammo = 0;
        hud.build(&view(Some(&you)), &mut out);
        assert_ne!(knife, out.len(), "a knife drew the same as an empty rifle");
    }

    #[test]
    fn text_width_counts_gaps_between_glyphs_not_after_them() {
        // Right-aligned text uses this to decide where to start. Counting the
        // trailing gap floats every right-aligned line one space off its edge.
        assert_eq!(text_width("", 2.0), 0.0);
        assert_eq!(text_width("A", 2.0), 10.0);
        assert_eq!(text_width("AB", 2.0), 22.0);
    }

    #[test]
    fn the_font_has_a_shape_for_everything_the_hud_writes() {
        // A HUD string containing a glyph the font lacks comes out with a hole
        // in it, and nothing anywhere reports that.
        for ch in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ./-+:>∞".chars() {
            assert_ne!(glyph(ch), [0; 7], "no glyph for {ch:?}");
        }
        assert_eq!(glyph(' '), [0; 7], "a space is blank");
        assert_eq!(glyph('\u{2603}'), [0; 7], "an unknown glyph is blank");
        // Lowercase is folded rather than dropped: every string here is
        // uppercased on the way in, but a weapon name comes from the server.
        assert_eq!(glyph('a'), glyph('A'));
    }
}
