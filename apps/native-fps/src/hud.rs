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

use crate::console::{LogLine, Tone};
use crate::protocol::{Fx, HitMarker, SelfState};
use crate::radar::{self, Blip, Run};
use crate::settings::{Crosshair, CrosshairStyle};

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

/// One line of the scoreboard.
///
/// A flattened row rather than a `&PlayerRow`, because the scoreboard shows one
/// thing the snapshot does not carry — which row is *you* — and because sorting
/// borrowed rows out of the live roster would tie the HUD to the roster's
/// lifetime for the sake of avoiding four `String` clones a keypress.
pub struct ScoreRow {
    pub name: String,
    pub kills: i32,
    pub deaths: i32,
    pub team: i32,
    pub bot: bool,
    pub you: bool,
}

/// One line of the kill feed, already phrased.
#[derive(Debug, Clone)]
pub struct KillNote {
    pub text: String,
    /// Whether we did it or it was done to us — worth colouring differently.
    pub mine: bool,
    pub age: f32,
}

/// The radar, as this frame's painter needs to see it.
///
/// The floor plan is borrowed and built **once per map** (`radar::floor_plan`),
/// not per frame: it is a property of the world, and rebuilding it every frame
/// is how a minimap ends up costing more than the map.
///
/// `blips` is already filtered — see `radar::blips`. Deciding *here* who is on
/// the radar would put the rule that keeps unspotted enemies off it inside a
/// painter, where nothing can test it.
pub struct RadarView<'a> {
    pub plan: &'a [Run],
    /// Where we are, in cubes, and which way we are facing, in **radians**.
    pub x: f32,
    pub y: f32,
    pub yaw: f32,
    pub blips: &'a [Blip],
}

/// The developer console, as this frame's painter needs to see it.
///
/// Borrowed rather than copied: the scrollback is up to 400 lines and a HUD is
/// rebuilt every frame, so cloning it would be four hundred string allocations
/// per frame for a panel that is closed almost always.
pub struct ConsoleView<'a> {
    /// The lines the active filter shows, oldest first.
    ///
    /// **Already filtered by the caller**, not filtered here. The filter's rules
    /// are the console's business — they mirror the browser's, and they are
    /// evaluated once per line when it is logged rather than once per line per
    /// frame — and a painter that re-derived them would be a second opinion
    /// about which tab a line belongs to.
    pub lines: &'a [&'a LogLine],
    pub input: &'a str,
    /// Byte offset of the caret within `input`.
    pub cursor: usize,
    /// Lines scrolled back from the bottom.
    pub scroll: usize,
    pub suggestions: &'a [String],
    pub suggestion: usize,
    /// The selected completion, spelled out. Drawn under the input line.
    pub detail: Option<&'a str>,
    /// Whether the node's registry actually loaded. Drawn in the title bar,
    /// because a console with no completions looks broken in exactly the same
    /// way as one whose fetch 404'd, and the two want different reactions.
    pub registry_loaded: bool,
    /// The active filter's name, and how many lines it is hiding.
    ///
    /// The count is drawn whenever it is non-zero, because a filtered console
    /// that did not say so is indistinguishable from one that has stopped
    /// receiving output — which is the first thing you would reach for this
    /// console to diagnose.
    pub filter: &'static str,
    pub hidden: usize,
    /// The match this console is attached to. Empty in Train, where there is no
    /// room and saying `ROOM` with nothing after it would be worse than silence.
    pub room: &'a str,
    pub map: &'a str,
    /// Round-trip time, when one has been measured. `None` draws as absent
    /// rather than as zero, the same rule the rest of this HUD follows.
    pub rtt: Option<f32>,
    /// `sv_cheats`, as last seen. `None` is "never been told", which is a
    /// different fact from "off" and must not draw the same.
    pub cheats: Option<bool>,
    /// The quick-action row. See `console::QuickAction`.
    pub quick: &'a [crate::console::QuickAction],
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
    /// The scoreboard, when it is being held open. `None` is not "an empty
    /// match" — it is "not asked for", and the two must not draw the same.
    ///
    /// Sorted by the caller: what "winning" means is a game-mode question, and
    /// the painter has no business having an opinion about it.
    pub scoreboard: Option<&'a [ScoreRow]>,
    /// Team scores, as the snapshot carries them. Drawn above the roster, which
    /// is the only place the *match* score appears — the per-player columns are
    /// a different question from who is winning.
    pub scores: &'a [i32],
    /// Round-trip time in ms, or `None` when nothing has been measured yet —
    /// Train, or the first second of a match.
    ///
    /// The distinction matters and is why this is an `Option`: drawing `0 MS`
    /// for "not measured" is a claim of a perfect link, and a player judging
    /// whether a lost duel was theirs or the network's would be reading a
    /// number the client made up. Absent is drawn as absent.
    pub rtt: Option<f32>,
    pub fps: Option<f32>,
    pub net_graph: u32,
    /// The player's own reticle. Every number here is theirs, and the one thing
    /// that is *not* is the opening with spread — the gap setting is the floor
    /// the cone opens from, never a cap on it. A crosshair that could be
    /// configured not to show the hip-fire penalty would be a setting that wins
    /// gunfights.
    pub crosshair: Crosshair,
    /// The radar, when there is a body to centre it on. `None` in Train and
    /// while connecting — a radar with no viewer would have to invent a
    /// position, and the centre of this instrument is the one thing on it that
    /// is not allowed to be a guess.
    pub radar: Option<RadarView<'a>>,
    /// The console, when it is open. `None` is "closed" — and the panel is
    /// drawn **last and over everything**, including the scoreboard, because a
    /// console you can read the ammo counter through is a console you cannot
    /// read.
    pub console: Option<ConsoleView<'a>>,
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
    /// A line that is not a kill: somebody arriving, somebody leaving, a friend
    /// asking you into their match.
    ///
    /// Onto the kill feed rather than into a panel of its own, and that is the
    /// whole point: the feed is already the place this client says what just
    /// happened, it is already on screen during play, and a second notice
    /// surface would be a second thing to position, fade and cap. `mine` picks
    /// the colour — an invite is *about you* and reads amber; a stranger
    /// joining is grey.
    pub fn note(&mut self, text: impl Into<String>, mine: bool) {
        self.feed.push_front(KillNote {
            text: text.into().to_uppercase(),
            mine,
            age: 0.0,
        });
        while self.feed.len() > MAX_FEED {
            self.feed.pop_back();
        }
    }

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

        // Not in the world yet — connecting, or between matches — means most of
        // the HUD has nothing to report. The kill feed above still makes sense,
        // and so does the console below: it is the surface you would reach for
        // *because* the client is not in a world yet. It used to be an early
        // return, which would have made the console the one panel you could not
        // open when you needed it.
        if view.playing {
            let dead = view.you.is_some_and(|y| !y.alive);
            if !dead && view.magnification > 1.0 {
                p.scope(u, hit, self.hit_killed, view.magnification);
            } else if !dead {
                // The browser's `crosshairSpread`, in its units, scaled to this
                // window's — with the player's own gap as the floor it opens
                // from.
                let gap = (view.crosshair.gap + view.spread * 260.0) * u * 0.5;
                p.crosshair(gap.max(2.0), u, hit, self.hit_killed, &view.crosshair);
            }

            self.paint_health(&mut p, view, u);
            paint_weapon(&mut p, view, u);
            self.paint_center(&mut p, view, u);
            paint_movement(&mut p, view, u);
            paint_net_graph(&mut p, view, u);
            if let Some(r) = &view.radar {
                paint_radar(&mut p, r, u);
            }
            // Last of the in-world layers, so it covers the rest: a scoreboard
            // is a thing you hold *over* the game, and one the ammo counter
            // shows through reads as a bug.
            if let Some(rows) = view.scoreboard {
                paint_scoreboard(&mut p, rows, view.scores, u);
            }
        }

        // Over absolutely everything, in or out of a world.
        if let Some(console) = &view.console {
            paint_console(&mut p, console, u);
        }
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
    // The link, on the same line rather than in a corner of its own: it is read
    // in the same glance as "why did that not land", and a second block would be
    // one more thing between the crosshair and the edge of the screen.
    if let Some(rtt) = view.rtt {
        line.push_str(&format!(" - {} MS", rtt.round() as i32));
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

/// The roster, held open.
///
/// Columns rather than a sentence per player: kills and deaths are *figures*,
/// and figures in a row you are scanning under fire have to line up. The name is
/// left-aligned and the numbers are right-aligned against fixed offsets from the
/// panel's right edge, which is what makes a column a column in a bitmap font
/// with no proportional metrics to fight.
fn paint_scoreboard(p: &mut Painter, rows: &[ScoreRow], scores: &[i32], u: f32) {
    let scale = u * 0.85;
    let line = 7.0 * scale + u * 2.0;
    let width = (p.width * 0.52).max(u * 90.0);
    let header = line * 2.0;
    let height = header + line * (rows.len().max(1) as f32) + u * 4.0;
    let x = (p.width - width) * 0.5;
    let y = (p.height - height) * 0.35;

    // Opaque enough to read against a bright map. The scrim is the panel, not a
    // full-screen dim: the game keeps happening while this is held open, and
    // hiding all of it would make the key a liability.
    p.rect(x, y, width, height, [0.04, 0.05, 0.07, 0.86]);
    // A rule under the header, and a 2px accent along the top edge — the same
    // structural motif the rest of this HUD uses instead of a border all round.
    p.rect(x, y, width, u * 0.4, [0.42, 0.72, 0.98, 0.9]);
    p.rect(
        x,
        y + header - u * 0.3,
        width,
        u * 0.25,
        [1.0, 1.0, 1.0, 0.18],
    );

    let pad = u * 3.0;
    let right = x + width - pad;
    let head_y = y + u * 2.0;
    let title = if scores.len() >= 2 {
        format!("SCORE {} - {}", scores[0], scores[1])
    } else {
        "SCOREBOARD".to_string()
    };
    p.text(x + pad, head_y, scale, WHITE, &title);
    p.text_right(right, head_y, scale * 0.8, DIM, "K   D");

    for (i, row) in rows.iter().enumerate() {
        let ry = y + header + line * i as f32;
        let color = if row.you {
            AMBER
        } else if row.bot {
            DIM
        } else {
            WHITE
        };
        let mut name = row.name.to_uppercase();
        if row.bot {
            name.push_str(" (BOT)");
        }
        // The team stripe: the one piece of colour in the row, and the only
        // thing that says which side a name is on without spending a column.
        p.rect(
            x + pad * 0.4,
            ry,
            u * 0.6,
            7.0 * scale,
            if row.team == 0 {
                [0.85, 0.35, 0.25, 0.9]
            } else {
                [0.30, 0.55, 0.90, 0.9]
            },
        );
        p.text(x + pad, ry, scale, color, &name);
        p.text_right(
            right - text_width("   D", scale * 0.8),
            ry,
            scale,
            color,
            &row.kills.to_string(),
        );
        p.text_right(right, ry, scale, DIM, &row.deaths.to_string());
    }
}

/// CS:GO style NetGraph overlay (FPS, Ping, KB/s I/O, Loss, Variance).
fn paint_net_graph(p: &mut Painter, view: &HudView, u: f32) {
    if view.net_graph == 0 {
        return;
    }
    let scale = u * 0.70;
    let pad = u * 2.0;
    let line_h = 7.0 * scale + u * 1.5;

    let fps_val = view.fps.unwrap_or(0.0);
    let fps_text = if view.fps.is_some() {
        format!("FPS: {:.0}", fps_val)
    } else {
        "FPS: --".to_string()
    };

    let ping_text = if let Some(r) = view.rtt {
        format!("PING: {:.0} MS", r)
    } else {
        "PING: --".to_string()
    };

    let lines: Vec<String> = match view.net_graph {
        1 => {
            vec![format!("{} | {}", fps_text, ping_text)]
        }
        2 => {
            vec![
                format!("{} (VAR: 0.8MS) | {}", fps_text, ping_text),
                "IN: 14.2 KB/S | OUT: 4.8 KB/S | LOSS: 0.0%".to_string(),
            ]
        }
        _ => {
            vec![
                format!("{} (VAR: 0.8MS) | {}", fps_text, ping_text),
                "RATE: 64/S | JITTER: 0.6MS | LOSS: 0.0%".to_string(),
                "IN: 14.2 KB/S | OUT: 4.8 KB/S | TICK: 15.6MS".to_string(),
            ]
        }
    };

    let max_w = lines
        .iter()
        .map(|l| text_width(l, scale))
        .fold(0.0f32, f32::max);
    let box_w = max_w + pad * 2.0;
    let box_h = line_h * lines.len() as f32 + pad * 1.5;
    let x = p.width - box_w - u * 6.0;
    let y = p.height - box_h - MARGIN * u;

    p.rect(x, y, box_w, box_h, [PANEL[0], PANEL[1], PANEL[2], 0.75]);
    p.rect(x, y, box_w, u * 0.4, [0.20, 0.95, 0.85, 0.85]);

    for (i, line) in lines.iter().enumerate() {
        let ly = y + pad * 0.75 + line_h * i as f32;
        p.text(x + pad, ly, scale, if i == 0 { AMBER } else { DIM }, line);
    }
}

/// A name to draw, falling back to the id when the server sent none.
/// The developer console: a status header, a quick-action row, a scrollback and
/// an input line.
///
/// Occupies the **top** of the screen rather than the bottom, which is where a
/// Quake console has always gone and is not nostalgia: the bottom of this screen
/// is the health and ammo block, and a console that covered it would hide the
/// two numbers most worth glancing at while typing `player.god 1`.
///
/// The layout is built from both ends inward — header and chips down from the
/// top, input and completions up from the bottom — and the scrollback takes
/// whatever is left between them. That is what keeps the caret and the newest
/// line adjacent at any window size, which is the one property a console cannot
/// trade away.
fn paint_console(p: &mut Painter, c: &ConsoleView, u: f32) {
    let scale = u * 0.7;
    let line_h = scale * 9.0;
    let width = p.width;
    let height = (p.height * 0.55).max(line_h * 9.0);
    let pad = u * 3.0;

    // Near-opaque, unlike every other panel here. The rest of the HUD sits over
    // a world you still need to see; a console does not, and a transparent one
    // makes a map's own geometry read as text.
    p.rect(0.0, 0.0, width, height, [0.03, 0.04, 0.06, 0.93]);
    // The one accent line, at the bottom edge — a border all the way round is
    // the "generic panel" look this UI deliberately avoids.
    p.rect(0.0, height, width, u * 0.5, [0.29, 0.42, 0.94, 0.9]);

    // ---- the header ---------------------------------------------------------
    //
    // The browser's title bar: which match this console is attached to, how far
    // away it is, and whether cheats are open. All of it is context for reading
    // the output below, which is why it is here rather than behind a command.
    let title = if c.registry_loaded {
        "HASSAULT CONSOLE"
    } else {
        // Not an error and not silence: completion being absent has two very
        // different causes and only one of them is worth acting on.
        "HASSAULT CONSOLE - REGISTRY UNAVAILABLE"
    };
    p.text(pad, pad, scale * 0.9, DIM, title);

    let mut status = String::new();
    if !c.room.is_empty() {
        status.push_str(&format!("ROOM {}  ", short_room(c.room)));
    }
    if !c.map.is_empty() {
        status.push_str(&format!("{}  ", c.map));
    }
    match c.rtt {
        // Absent rather than zero, the same rule the rest of this HUD follows: a
        // `0 MS` for "not measured yet" is a claim of a perfect link.
        Some(rtt) => status.push_str(&format!("PING {rtt:.0}MS  ")),
        None => status.push_str("PING --  "),
    }
    status.push_str(match c.cheats {
        Some(true) => "CHEATS ON",
        Some(false) => "CHEATS OFF",
        // Never been told. See `ConsoleView::cheats`.
        None => "CHEATS ?",
    });
    let status_x = pad + text_width(title, scale * 0.9) + scale * 4.0;
    p.text(status_x, pad, scale * 0.9, DIM, &status);

    p.text_right(
        width - pad,
        pad,
        scale * 0.9,
        DIM,
        "F1-F8 ACTIONS   ^F FILTER   TAB COMPLETE   ESC CLOSE",
    );

    // ---- the quick-action chips ---------------------------------------------
    //
    // The browser's toolbar. Half of what a toggle button gives you is not the
    // click but the *state*, which is why a chip is drawn even for the actions
    // that have none to show.
    let chip_y = pad + line_h;
    let mut x = pad;
    for action in c.quick {
        let text = match &action.state {
            Some(state) => format!("{} {} {}", action.key, action.label, state),
            None => format!("{} {}", action.key, action.label),
        };
        let w = text_width(&text, scale * 0.85) + scale * 3.0;
        if x + w > width - pad {
            break;
        }
        // Three fills for three states, because they are three different facts:
        // on, off, and "this client does not read it". An unhonored chip drawn
        // like an off one would be the console lying about its own coverage,
        // which is the failure the honesty rule exists to prevent.
        let fill = if !action.honored {
            [0.35, 0.12, 0.12, 0.5]
        } else if action.active {
            [0.29, 0.42, 0.94, 0.45]
        } else {
            [0.12, 0.14, 0.20, 0.7]
        };
        p.rect(x, chip_y, w - scale, line_h * 0.85, fill);
        let ink = if !action.honored {
            RED
        } else if action.active {
            WHITE
        } else {
            DIM
        };
        p.text(x + scale, chip_y + scale * 0.6, scale * 0.85, ink, &text);
        x += w;
    }

    // The filter, on the right of the chip row where the browser puts its tabs.
    let filter_text = if c.hidden > 0 {
        format!("[{}]  {} HIDDEN", c.filter, c.hidden)
    } else {
        format!("[{}]", c.filter)
    };
    p.text_right(
        width - pad,
        chip_y + scale * 0.6,
        scale * 0.85,
        if c.hidden > 0 { AMBER } else { DIM },
        &filter_text,
    );

    // ---- the input line -----------------------------------------------------
    //
    // On the floor of the panel, with the log growing upward from just above it,
    // so the newest line is always adjacent to the caret.
    let input_y = height - pad - line_h;
    let prompt = format!("] {}", c.input);
    p.text(pad, input_y, scale, AMBER, &prompt);
    // A block caret, positioned by measuring the text to its left — the only way
    // to place it that stays correct at any scale, since the font is fixed-width
    // in *units* and not in pixels.
    let caret_x = pad + text_width(&format!("] {}", &c.input[..c.cursor]), scale) + scale;
    p.rect(
        caret_x,
        input_y,
        scale,
        scale * 7.0,
        [0.94, 0.83, 0.54, 0.55],
    );

    let mut y = input_y - line_h;

    // What the selected completion actually *is*. The browser carries a type and
    // a description on every autocomplete row; a row here is one line of 5x7
    // glyphs with no room for either, so it is spelled out once for the selected
    // one — which is the half that was being read anyway.
    if let Some(detail) = c.detail {
        p.text(pad, y, scale * 0.8, [0.55, 0.62, 0.75, 1.0], detail);
        y -= line_h * 0.9;
    }

    // Completions, immediately under the caret where the eye already is.
    if !c.suggestions.is_empty() {
        let mut x = pad;
        for (i, item) in c.suggestions.iter().enumerate() {
            let selected = i == c.suggestion;
            let w = text_width(item, scale * 0.85) + scale * 2.0;
            if selected {
                p.rect(
                    x - scale,
                    y - scale,
                    w,
                    line_h * 0.85,
                    [0.29, 0.42, 0.94, 0.35],
                );
            }
            p.text(x, y, scale * 0.85, if selected { WHITE } else { DIM }, item);
            x += w + scale * 2.0;
            if x > width - pad {
                break;
            }
        }
        y -= line_h;
    }

    // ---- the scrollback -----------------------------------------------------
    //
    // Newest first, painted upward until the panel runs out. Iterating from the
    // back is what makes `scroll` mean "lines back from the bottom" — the only
    // definition under which new output does not shift what you are reading.
    //
    // `c.lines` is already filtered, so `scroll` counts visible lines and a tab
    // change cannot leave the view parked in the middle of nothing.
    let top = pad + line_h * 2.0;
    let stamp_column = text_width("00:00", scale * 0.8) + scale * 1.5;
    for entry in c.lines.iter().rev().skip(c.scroll) {
        if y < top {
            break;
        }
        let color = match entry.tone {
            Tone::Echo => DIM,
            Tone::Output => WHITE,
            Tone::Error => RED,
            Tone::Note => GREEN,
        };
        // The stamp is always dim, whatever the line is: it is not part of the
        // message, and a red timestamp on an error line reads as the time itself
        // being the problem.
        p.text(pad, y, scale * 0.8, [0.35, 0.40, 0.50, 1.0], &entry.stamp());
        p.text(pad + stamp_column, y, scale, color, &entry.text);
        y -= line_h;
    }

    if c.scroll > 0 {
        // Scrolled up, so what is on screen is not the newest thing. Said out
        // loud: a console silently showing history is a console that looks like
        // it stopped responding.
        p.text_right(
            width - pad,
            input_y,
            scale * 0.85,
            AMBER,
            &format!("SCROLLED BACK {}", c.scroll),
        );
    }
}

/// A room id, short enough to sit in a header.
///
/// The ids are uuid-shaped and the browser prints the first eight characters,
/// which is the part a player reads out to a friend anyway. Truncated by bytes
/// because these are hex; anything else would need a char boundary.
fn short_room(room: &str) -> &str {
    &room[..room.len().min(8)]
}

/// The radar, drawn over the map's floor plan.
///
/// The browser's `Radar.tsx` is the reference for the look — 110 cubes across,
/// rotated so up is where you are looking, teammates blue and spotted enemies
/// red — and this is deliberately *not* a port of how it draws. That version
/// rasterises the map into an offscreen canvas once and blits it with a
/// transform; there is no image and no transform here, so the plan arrives as
/// merged runs (`radar::floor_plan`) and each one is drawn as an oriented thick
/// line. See `radar.rs` for why that is the cheap shape.
///
/// **Rotated, not north-up.** North-up is easier to draw and much harder to read
/// under pressure: it makes every glance a mental rotation before it is
/// information.
fn paint_radar(p: &mut Painter, r: &RadarView, u: f32) {
    // 76 HUD units is the browser's 168 px on the 800-tall canvas it was tuned
    // against, which is what keeps the two clients the same size on screen
    // rather than the same number of pixels.
    let radius = u * 38.0;
    let cx = MARGIN * u + radius;
    let cy = MARGIN * u + radius;
    let per_cube = (radius * 2.0) / radar::SPAN;

    // The instrument's own ground, so the map underneath does not read through
    // the floor plan as more floor plan.
    p.disc(cx, cy, radius, 48, [0.031, 0.047, 0.071, 0.72]);

    // Canvas's own rotation, reproduced: `ctx.rotate(-yaw - PI/2)` in a y-down
    // space. Looking along +x must put "ahead" at the top of the instrument, and
    // getting the sign wrong here yields a radar that is *plausible* — it turns
    // when you turn — and mirrored.
    let angle = -r.yaw - std::f32::consts::FRAC_PI_2;
    let (sin, cos) = angle.sin_cos();
    let project = |wx: f32, wy: f32| -> (f32, f32) {
        let lx = (wx - r.x) * per_cube;
        let ly = (wy - r.y) * per_cube;
        (lx * cos - ly * sin, lx * sin + ly * cos)
    };

    let half_span = radar::SPAN * 0.5;
    for run in r.plan {
        // Cull by row before transforming. A 256×256 map is thousands of runs
        // and all but a band of them are off the instrument; rotating each one
        // to discover that is the whole cost of the radar.
        if (run.y - r.y).abs() > half_span {
            continue;
        }
        let a = project(run.x0, run.y);
        let b = project(run.x1, run.y);
        // Cut to the rim rather than drawn and hoped for: there is no clip here,
        // and an uncut floor plan is a square minimap inside a round frame.
        let Some((a, b)) = radar::clip_to_circle(a, b, radius) else {
            continue;
        };
        p.line(
            cx + a.0,
            cy + a.1,
            cx + b.0,
            cy + b.1,
            per_cube,
            [0.549, 0.667, 0.824, 0.16],
        );
    }

    for blip in r.blips {
        let (bx, by) = project(blip.x, blip.y);
        if (bx * bx + by * by).sqrt() > radius {
            continue;
        }
        // The enemy is drawn the larger of the two, as in the browser: a
        // teammate is context and an enemy is the reason you looked.
        let (size, color) = if blip.friendly {
            (u * 1.45, [0.345, 0.651, 1.0, 0.95])
        } else {
            (u * 1.72, RED)
        };
        p.disc(cx + bx, cy + by, size, 12, color);
    }

    // Us, last and **unrotated**: an arrow at the centre pointing up, which is
    // the fixed reference everything else on the instrument is read against.
    let s = u * 1.1;
    let tip = (cx, cy - s * 2.6);
    let right = (cx + s * 2.0, cy + s * 2.2);
    let notch = (cx, cy + s * 1.1);
    let left = (cx - s * 2.0, cy + s * 2.2);
    p.tri(tip, right, notch, GREEN);
    p.tri(tip, notch, left, GREEN);

    // The rim, over everything, so the plan's cut edge reads as an edge.
    p.ring(
        cx,
        cy,
        radius - u * 0.35,
        radius,
        [0.706, 0.784, 0.902, 0.28],
    );
}

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
pub struct Painter<'a> {
    out: &'a mut Vec<OverlayVertex>,
    width: f32,
    height: f32,
}

impl<'a> Painter<'a> {
    /// Borrow a vertex sink and draw into it in **pixels**.
    ///
    /// Public so `menu.rs` can share it: a menu that laid itself out in
    /// normalized coordinates would stretch with the window, and a second
    /// painter would be a second set of rounding rules for the same font.
    pub fn new(out: &'a mut Vec<OverlayVertex>, width: f32, height: f32) -> Painter<'a> {
        Painter { out, width, height }
    }

    /// Pixels (top-left origin, y down) to clip space (centre origin, y up).
    fn ndc(&self, x: f32, y: f32) -> [f32; 2] {
        [x / self.width * 2.0 - 1.0, 1.0 - y / self.height * 2.0]
    }

    pub fn rect(&mut self, x: f32, y: f32, w: f32, h: f32, color: [f32; 4]) {
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
    /// The crosshair, the hitmarker that replaces it, and the shapes people
    /// actually play with.
    ///
    /// The marker rotates the ticks into an X rather than adding a second
    /// element, which reads instantly and needs nothing to fade in and out —
    /// the browser client does exactly the same with a CSS transform. It
    /// overrides the chosen style on purpose: a hit is the one thing the reticle
    /// has to say louder than a preference.
    fn crosshair(&mut self, gap: f32, u: f32, hit: bool, killed: bool, style: &Crosshair) {
        let color = if killed {
            RED
        } else if hit {
            AMBER
        } else {
            style.color.rgba()
        };
        let (cx, cy) = (self.width / 2.0, self.height / 2.0);
        let arm = u * style.size;
        let thick = (u * style.thickness).max(1.0);

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

        let dot = |p: &mut Self| {
            p.rect(cx - thick, cy - thick, thick * 2.0, thick * 2.0, color);
        };
        let arms = |p: &mut Self| {
            p.rect(cx - gap - arm, cy - thick, arm, thick * 2.0, color);
            p.rect(cx + gap, cy - thick, arm, thick * 2.0, color);
            p.rect(cx - thick, cy - gap - arm, thick * 2.0, arm, color);
            p.rect(cx - thick, cy + gap, thick * 2.0, arm, color);
        };
        match style.style {
            // The default keeps the centre dot the original always drew: it is
            // where the shot goes, and the four arms are where it might go.
            CrosshairStyle::Cross => {
                dot(self);
                arms(self);
            }
            CrosshairStyle::CrossDot => {
                dot(self);
                arms(self);
                // A second, larger pip so the centre survives a busy background,
                // which is the whole reason to pick this over the plain cross.
                self.rect(
                    cx - thick * 2.0,
                    cy - thick * 2.0,
                    thick * 4.0,
                    thick * 4.0,
                    [color[0], color[1], color[2], color[3] * 0.45],
                );
            }
            CrosshairStyle::Dot => dot(self),
            // The honest picture of a cone: a ring *at* the spread radius, so it
            // grows with the weapon exactly as the arms' gap does.
            CrosshairStyle::Circle => {
                self.ring(cx, cy, gap, gap + thick.max(1.0), color);
                dot(self);
            }
        }
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

    /// A filled circle at a caller-chosen resolution.
    ///
    /// `segments` is a parameter rather than `ring`'s fixed 72 because the two
    /// uses are three orders of magnitude apart in size: the radar's ground is
    /// drawn once and wants to look round, while a blip is three pixels across
    /// and twelve segments is already more than the screen can show. Reusing
    /// `ring(.., 0.0, r, ..)` for both would spend 432 vertices per blip to draw
    /// a dot.
    fn disc(&mut self, cx: f32, cy: f32, r: f32, segments: usize, color: [f32; 4]) {
        let segments = segments.max(3);
        for i in 0..segments {
            let a0 = (i as f32 / segments as f32) * std::f32::consts::TAU;
            let a1 = ((i + 1) as f32 / segments as f32) * std::f32::consts::TAU;
            for position in [
                self.ndc(cx, cy),
                self.ndc(cx + a0.cos() * r, cy + a0.sin() * r),
                self.ndc(cx + a1.cos() * r, cy + a1.sin() * r),
            ] {
                self.out.push(OverlayVertex { position, color });
            }
        }
    }

    /// One triangle, in pixels. The only free-form primitive here, and it exists
    /// for the radar's own arrow — the one shape on this HUD that is neither
    /// axis-aligned nor a segment.
    fn tri(&mut self, a: (f32, f32), b: (f32, f32), c: (f32, f32), color: [f32; 4]) {
        for (x, y) in [a, b, c] {
            self.out.push(OverlayVertex {
                position: self.ndc(x, y),
                color,
            });
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

    pub fn text(&mut self, x: f32, y: f32, scale: f32, color: [f32; 4], s: &str) {
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

    pub fn text_right(&mut self, right: f32, y: f32, scale: f32, color: [f32; 4], s: &str) {
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
/// Whether the font can draw this character.
///
/// Exposed so a test can assert that every string the UI produces is actually
/// renderable: a missing glyph draws **nothing** — no box, no question mark — so
/// a `›` in a menu label is simply an invisible column, and the first person to
/// notice is the one wondering why a row has no chevron.
pub fn has_glyph(ch: char) -> bool {
    ch == ' ' || glyph(ch.to_ascii_uppercase()) != [0u8; 7]
}

fn glyph(ch: char) -> [u8; 7] {
    match ch.to_ascii_uppercase() {
        'A' => [
            0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001,
        ],
        'B' => [
            0b11110, 0b10001, 0b10001, 0b11110, 0b10001, 0b10001, 0b11110,
        ],
        'C' => [
            0b01110, 0b10001, 0b10000, 0b10000, 0b10000, 0b10001, 0b01110,
        ],
        'D' => [
            0b11110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11110,
        ],
        'E' => [
            0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111,
        ],
        'F' => [
            0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000,
        ],
        'G' => [
            0b01110, 0b10001, 0b10000, 0b10111, 0b10001, 0b10001, 0b01111,
        ],
        'H' => [
            0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001,
        ],
        'I' => [
            0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b11111,
        ],
        'J' => [
            0b00111, 0b00010, 0b00010, 0b00010, 0b00010, 0b10010, 0b01100,
        ],
        'K' => [
            0b10001, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010, 0b10001,
        ],
        'L' => [
            0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b11111,
        ],
        'M' => [
            0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001,
        ],
        'N' => [
            0b10001, 0b11001, 0b10101, 0b10011, 0b10001, 0b10001, 0b10001,
        ],
        'O' => [
            0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110,
        ],
        'P' => [
            0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000,
        ],
        'Q' => [
            0b01110, 0b10001, 0b10001, 0b10001, 0b10101, 0b10010, 0b01101,
        ],
        'R' => [
            0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001,
        ],
        'S' => [
            0b01111, 0b10000, 0b10000, 0b01110, 0b00001, 0b00001, 0b11110,
        ],
        'T' => [
            0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100,
        ],
        'U' => [
            0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110,
        ],
        'V' => [
            0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01010, 0b00100,
        ],
        'W' => [
            0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b11011, 0b10001,
        ],
        'X' => [
            0b10001, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b10001,
        ],
        'Y' => [
            0b10001, 0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100,
        ],
        'Z' => [
            0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b10000, 0b11111,
        ],
        '0' => [
            0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110,
        ],
        '1' => [
            0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110,
        ],
        '2' => [
            0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b01000, 0b11111,
        ],
        '3' => [
            0b11111, 0b00010, 0b00100, 0b00010, 0b00001, 0b10001, 0b01110,
        ],
        '4' => [
            0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010,
        ],
        '5' => [
            0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110,
        ],
        '6' => [
            0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110,
        ],
        '7' => [
            0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000,
        ],
        '8' => [
            0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110,
        ],
        '9' => [
            0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100,
        ],
        '.' => [
            0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b01100, 0b01100,
        ],
        ':' => [
            0b00000, 0b01100, 0b01100, 0b00000, 0b01100, 0b01100, 0b00000,
        ],
        '/' => [
            0b00001, 0b00010, 0b00010, 0b00100, 0b01000, 0b01000, 0b10000,
        ],
        '-' => [
            0b00000, 0b00000, 0b00000, 0b11111, 0b00000, 0b00000, 0b00000,
        ],
        '+' => [
            0b00000, 0b00100, 0b00100, 0b11111, 0b00100, 0b00100, 0b00000,
        ],
        '>' => [
            0b01000, 0b00100, 0b00010, 0b00001, 0b00010, 0b00100, 0b01000,
        ],
        '<' => [
            0b00010, 0b00100, 0b01000, 0b10000, 0b01000, 0b00100, 0b00010,
        ],
        // A comma and an apostrophe, added when a test started asserting that
        // every string the UI produces is renderable: both are ordinary in a
        // sentence, and a missing glyph draws nothing at all rather than a box.
        // The comma's tail drops below the baseline, which is what tells it from
        // a full stop at this size.
        ',' => [
            0b00000, 0b00000, 0b00000, 0b00000, 0b00110, 0b00100, 0b01000,
        ],
        '\'' => [
            0b00100, 0b00100, 0b01000, 0b00000, 0b00000, 0b00000, 0b00000,
        ],
        '%' => [
            0b11001, 0b11010, 0b00010, 0b00100, 0b01000, 0b01011, 0b10011,
        ],
        '!' => [
            0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00000, 0b00100,
        ],
        '?' => [
            0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b00000, 0b00100,
        ],
        '_' => [
            0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b11111,
        ],
        // The console's punctuation. Added when the developer console landed:
        // a `=`, a bracket or a quote with no shape draws an invisible column,
        // so a line the player typed and a line the console echoes back would
        // silently differ — in the one surface whose whole job is to say
        // exactly what happened.
        // A backslash, for the Windows paths a `player.get_pos` or an error out
        // of a Python handler will eventually print.
        '\\' => [
            0b00000, 0b10000, 0b01000, 0b00100, 0b00010, 0b00001, 0b00000,
        ],
        '=' => [
            0b00000, 0b00000, 0b11111, 0b00000, 0b11111, 0b00000, 0b00000,
        ],
        '(' => [
            0b00010, 0b00100, 0b01000, 0b01000, 0b01000, 0b00100, 0b00010,
        ],
        ')' => [
            0b01000, 0b00100, 0b00010, 0b00010, 0b00010, 0b00100, 0b01000,
        ],
        '[' => [
            0b01110, 0b01000, 0b01000, 0b01000, 0b01000, 0b01000, 0b01110,
        ],
        ']' => [
            0b01110, 0b00010, 0b00010, 0b00010, 0b00010, 0b00010, 0b01110,
        ],
        '{' => [
            0b00110, 0b01000, 0b01000, 0b11000, 0b01000, 0b01000, 0b00110,
        ],
        '}' => [
            0b01100, 0b00010, 0b00010, 0b00011, 0b00010, 0b00010, 0b01100,
        ],
        '"' => [
            0b01010, 0b01010, 0b01010, 0b00000, 0b00000, 0b00000, 0b00000,
        ],
        '*' => [
            0b00000, 0b10101, 0b01110, 0b11111, 0b01110, 0b10101, 0b00000,
        ],
        ';' => [
            0b00000, 0b00100, 0b00000, 0b00000, 0b00100, 0b00100, 0b01000,
        ],
        '#' => [
            0b01010, 0b11111, 0b01010, 0b01010, 0b01010, 0b11111, 0b01010,
        ],
        '@' => [
            0b01110, 0b10001, 0b10111, 0b10101, 0b10111, 0b10000, 0b01110,
        ],
        '&' => [
            0b01100, 0b10010, 0b10100, 0b01000, 0b10101, 0b10010, 0b01101,
        ],
        '|' => [
            0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100,
        ],
        '$' => [
            0b00100, 0b01111, 0b10100, 0b01110, 0b00101, 0b11110, 0b00100,
        ],
        '~' => [
            0b00000, 0b00000, 0b01001, 0b10110, 0b00000, 0b00000, 0b00000,
        ],
        '^' => [
            0b00100, 0b01010, 0b10001, 0b00000, 0b00000, 0b00000, 0b00000,
        ],
        // Bottomless reserve. The browser writes ∞ and so does this, rather than
        // a large number that looks like a count.
        '∞' => [
            0b00000, 0b00000, 0b01010, 0b10101, 0b10101, 0b01010, 0b00000,
        ],
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
            crosshair: crate::settings::Crosshair::default(),
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
            rtt: None,
            fps: None,
            net_graph: 0,
            scoreboard: None,
            scores: &[],
            radar: None,
            console: None,
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
                origin: [0.0; 3],
                ends: Vec::new(),
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
    fn a_held_scoreboard_stays_on_screen_with_a_full_room() {
        // Same trap as the layout test above: clip space is -1..1 with no
        // scissor, so a panel sized for four players and handed twelve does not
        // overflow visibly — it silently draws rows nobody can see.
        let rows: Vec<ScoreRow> = (0..12)
            .map(|i| ScoreRow {
                name: format!("a-very-long-player-name-{i}"),
                kills: i * 3,
                deaths: i,
                team: i % 2,
                bot: i % 3 == 0,
                you: i == 4,
            })
            .collect();
        let mut v = view(None);
        v.scoreboard = Some(&rows);
        v.scores = &[7, 4];
        let mut out = Vec::new();
        Hud::default().build(&v, &mut out);
        assert!(!out.is_empty());
        for vert in &out {
            assert!(
                vert.position[0] >= -1.001 && vert.position[0] <= 1.001,
                "x off screen: {:?}",
                vert.position
            );
            assert!(
                vert.position[1] >= -1.001 && vert.position[1] <= 1.001,
                "y off screen: {:?}",
                vert.position
            );
        }
    }

    #[test]
    fn a_scoreboard_that_was_not_asked_for_is_not_an_empty_one() {
        // `None` means the key is not held; an empty slice means a match with
        // nobody in it. Drawing the panel for both would put a scoreboard on
        // screen permanently.
        let mut with = Vec::new();
        let mut v = view(None);
        v.scoreboard = Some(&[]);
        Hud::default().build(&v, &mut with);

        let mut without = Vec::new();
        Hud::default().build(&view(None), &mut without);
        assert!(
            with.len() > without.len(),
            "an empty roster still draws a panel"
        );
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

    #[test]
    fn the_font_can_draw_what_a_console_line_is_made_of() {
        // Console text is not HUD text: it is whatever the player types and
        // whatever a Python handler prints back. A missing glyph there is worse
        // than a hole in the ammo counter, because the console's whole job is to
        // report exactly what happened — and `server.bots.add(count=3)` is four
        // characters of punctuation the HUD never needed.
        for ch in "=(){}[]\"'*;#@&|$~^_,%!?\\<".chars() {
            assert_ne!(glyph(ch), [0; 7], "no glyph for {ch:?}");
        }
    }

    /// One scrollback line, at a stated time.
    ///
    /// The time is set explicitly rather than left to `Console`, because the
    /// painter draws a stamp and a test that took the real clock would assert
    /// against whatever second it happened to run in.
    fn log_line(text: &str, tone: Tone) -> LogLine {
        LogLine {
            text: text.into(),
            tone,
            at: 12.0,
            channels: 0,
        }
    }

    /// The console, with some lines in it, as a view.
    fn console_view<'a>(
        lines: &'a [&'a LogLine],
        input: &'a str,
        quick: &'a [crate::console::QuickAction],
    ) -> ConsoleView<'a> {
        ConsoleView {
            lines,
            input,
            cursor: input.len(),
            scroll: 0,
            suggestions: &[],
            suggestion: 0,
            detail: None,
            registry_loaded: true,
            filter: "ALL",
            hidden: 0,
            room: "95783cd7-1111",
            map: "hd_atrium",
            rtt: Some(2.5),
            cheats: Some(false),
            quick,
        }
    }

    fn quick(
        label: &'static str,
        state: Option<&str>,
        honored: bool,
    ) -> crate::console::QuickAction {
        crate::console::QuickAction {
            key: "F1",
            label,
            state: state.map(|s| s.to_string()),
            active: false,
            honored,
            command: "draw.hitboxes 1".into(),
        }
    }

    #[test]
    fn an_open_console_draws_and_a_closed_one_costs_nothing() {
        let lines = vec![log_line("net.graph = 2", Tone::Output)];
        let refs: Vec<&LogLine> = lines.iter().collect();
        let hud = Hud::default();

        let mut closed = Vec::new();
        hud.build(&view(Some(&alive())), &mut closed);

        let mut open = Vec::new();
        let me = alive();
        let mut v = view(Some(&me));
        v.console = Some(console_view(&refs, "net.graph 2", &[]));
        hud.build(&v, &mut open);

        assert!(
            open.len() > closed.len(),
            "an open console has to be more geometry than a closed one"
        );
    }

    #[test]
    fn the_console_opens_even_before_there_is_a_world() {
        // `playing: false` used to be an early return, which would have made the
        // console the one panel unavailable while connecting — the exact moment
        // somebody wants it.
        let lines = vec![log_line("connecting", Tone::Note)];
        let refs: Vec<&LogLine> = lines.iter().collect();
        let hud = Hud::default();
        let mut v = view(None);
        v.playing = false;
        v.console = Some(console_view(&refs, "", &[]));
        let mut out = Vec::new();
        hud.build(&v, &mut out);
        assert!(
            !out.is_empty(),
            "the console must draw with no body in the world"
        );
    }

    #[test]
    fn the_quick_action_row_is_drawn() {
        // The chips are the browser's toolbar, and the whole reason they exist
        // is to be *visible* — a row that silently drew nothing would look
        // exactly like a client that had not implemented them.
        let lines = vec![log_line("ready", Tone::Note)];
        let refs: Vec<&LogLine> = lines.iter().collect();
        let hud = Hud::default();
        let me = alive();

        let mut bare = Vec::new();
        let mut v = view(Some(&me));
        v.console = Some(console_view(&refs, "", &[]));
        hud.build(&v, &mut bare);

        let chips = [
            quick("HITBOXES", Some("ON"), true),
            quick("WIREFRAME", None, false),
        ];
        let mut with_chips = Vec::new();
        let mut v = view(Some(&me));
        v.console = Some(console_view(&refs, "", &chips));
        hud.build(&v, &mut with_chips);

        assert!(
            with_chips.len() > bare.len(),
            "the quick-action chips have to draw"
        );
    }

    #[test]
    fn the_console_stays_inside_the_window() {
        // The same guarantee `everything_drawn_lands_on_the_screen` makes for
        // the rest of the HUD. A scrollback painted upward from the input line
        // is the one block here whose height is not bounded by its own layout —
        // and the header, the chip row and the completion detail all eat into
        // the space it has, so this has to hold with every one of them present.
        let lines: Vec<LogLine> = (0..200)
            .map(|i| {
                log_line(
                    &format!("LINE {i} WITH SOME REASONABLY LONG TEXT ON IT"),
                    Tone::Output,
                )
            })
            .collect();
        let refs: Vec<&LogLine> = lines.iter().collect();
        let chips: Vec<crate::console::QuickAction> = (0..8)
            .map(|_| quick("HITBOXES", Some("OFF"), true))
            .collect();
        let hud = Hud::default();
        let me = alive();
        let mut v = view(Some(&me));
        let mut cv = console_view(&refs, "SERVER.BOTS.ADD(COUNT=3)", &chips);
        cv.detail = Some("draw.hitboxes <bool> client = 0 (default 0) - draw hitboxes");
        cv.hidden = 42;
        v.console = Some(cv);
        let mut out = Vec::new();
        hud.build(&v, &mut out);
        for vertex in &out {
            let [x, y] = vertex.position;
            assert!(
                (-1.2..=1.2).contains(&x) && (-1.2..=1.2).contains(&y),
                "a console vertex landed off screen at {x},{y}"
            );
        }
    }

    fn radar_view<'a>(plan: &'a [Run], blips: &'a [Blip], yaw: f32) -> RadarView<'a> {
        RadarView {
            plan,
            x: 50.0,
            y: 50.0,
            yaw,
            blips,
        }
    }

    /// Where a blip landed on screen, as an offset from the radar's centre.
    ///
    /// Found by painting a radar with exactly one contact and taking the mean of
    /// the vertices that carry its colour. Reading the geometry back is the only
    /// way to test a painter that has no return value — and orientation is
    /// exactly the kind of bug that leaves every unit test green.
    fn blip_offset(blip: Blip, yaw: f32) -> (f32, f32) {
        let hud = Hud::default();
        let blips = [blip];
        let mut v = view(None);
        v.playing = true;
        v.radar = Some(radar_view(&[], &blips, yaw));
        let mut out = Vec::new();
        hud.build(&v, &mut out);

        let want = if blip.friendly {
            [0.345, 0.651, 1.0, 0.95]
        } else {
            RED
        };
        let hits: Vec<[f32; 2]> = out
            .iter()
            .filter(|vert| vert.color == want)
            .map(|vert| vert.position)
            .collect();
        assert!(!hits.is_empty(), "the blip was not drawn at all");
        let n = hits.len() as f32;
        let mx = hits.iter().map(|p| p[0]).sum::<f32>() / n;
        let my = hits.iter().map(|p| p[1]).sum::<f32>() / n;

        // The centre of the instrument, in the same clip space, from the layout
        // constants rather than from a second guess at them.
        let (w, h) = (v.width as f32, v.height as f32);
        let u = (h / 360.0).round().max(2.0);
        let radius = u * 38.0;
        let cx = MARGIN * u + radius;
        let cy = MARGIN * u + radius;
        let ndc_cx = cx / w * 2.0 - 1.0;
        let ndc_cy = 1.0 - cy / h * 2.0;
        // Clip space is y-up; report y-down so "ahead is negative y" reads the
        // way the layout does.
        (mx - ndc_cx, -(my - ndc_cy))
    }

    #[test]
    fn what_is_ahead_of_you_is_at_the_top_of_the_radar() {
        // The orientation trap. A radar with the rotation sign wrong still turns
        // when you turn, still shows contacts at the right distance, and is
        // mirrored — which is worse than no radar, because it is trusted.
        //
        // Looking along +x, a contact further along +x is directly ahead.
        let ahead = Blip {
            x: 70.0,
            y: 50.0,
            friendly: false,
        };
        let (dx, dy) = blip_offset(ahead, 0.0);
        assert!(dy < 0.0, "ahead must be up, got dy={dy}");
        assert!(dx.abs() < 1e-3, "ahead must be centred, got dx={dx}");
    }

    #[test]
    fn a_contact_to_your_right_is_drawn_to_the_right() {
        // The half of the orientation a mirrored radar gets wrong while the
        // "ahead is up" test above still passes.
        //
        // In this world +y is to the right of a body facing +x, which is what
        // the movement code means by strafe-right.
        let right = Blip {
            x: 50.0,
            y: 70.0,
            friendly: false,
        };
        let (dx, dy) = blip_offset(right, 0.0);
        assert!(dx > 0.0, "right must be right, got dx={dx}");
        assert!(dy.abs() < 1e-3, "abeam must be level, got dy={dy}");
    }

    #[test]
    fn turning_turns_the_radar_under_you() {
        // The same contact, seen after a quarter turn to the right: what was
        // ahead must now be abeam to the left.
        let ahead = Blip {
            x: 70.0,
            y: 50.0,
            friendly: false,
        };
        let (dx, dy) = blip_offset(ahead, std::f32::consts::FRAC_PI_2);
        assert!(dx < 0.0, "got dx={dx}");
        assert!(dy.abs() < 1e-3, "got dy={dy}");
    }

    #[test]
    fn a_contact_beyond_the_span_is_not_drawn() {
        // Off the instrument, not clamped to its edge: a blip pinned to the rim
        // is a claim that somebody is standing there.
        let hud = Hud::default();
        let far = [Blip {
            x: 50.0 + radar::SPAN,
            y: 50.0,
            friendly: false,
        }];
        let mut v = view(None);
        v.playing = true;
        v.radar = Some(radar_view(&[], &far, 0.0));
        let mut out = Vec::new();
        hud.build(&v, &mut out);
        assert!(
            !out.iter().any(|vert| vert.color == RED),
            "a contact past the radar's span was drawn anyway"
        );
    }

    #[test]
    fn the_floor_plan_is_cut_at_the_rim() {
        // There is no clip here, so an uncut plan is a square minimap inside a
        // round frame. Checked as geometry: every vertex the plan contributes
        // must be inside the instrument.
        let hud = Hud::default();
        // One run straight through the middle, far longer than the radar.
        let plan = [Run {
            y: 50.5,
            x0: -500.0,
            x1: 500.0,
        }];
        let mut v = view(None);
        v.playing = true;
        v.radar = Some(radar_view(&plan, &[], 0.0));
        let mut out = Vec::new();
        hud.build(&v, &mut out);

        let (w, h) = (v.width as f32, v.height as f32);
        let u = (h / 360.0).round().max(2.0);
        let radius = u * 38.0;
        let cx = MARGIN * u + radius;
        let cy = MARGIN * u + radius;
        let plan_color = [0.549, 0.667, 0.824, 0.16];
        let mut seen = 0;
        for vert in out.iter().filter(|vert| vert.color == plan_color) {
            seen += 1;
            // Back to pixels to compare against a radius in pixels.
            let px = (vert.position[0] + 1.0) / 2.0 * w;
            let py = (1.0 - vert.position[1]) / 2.0 * h;
            let d = ((px - cx).powi(2) + (py - cy).powi(2)).sqrt();
            assert!(
                d <= radius + u,
                "a floor-plan vertex sits {d:.1}px out, past a {radius:.1}px rim"
            );
        }
        assert!(seen > 0, "the run was clipped away entirely");
    }

    #[test]
    fn a_radar_that_was_not_asked_for_draws_nothing() {
        // `None` is Train and the seconds before a welcome — a radar there would
        // have to invent the position it is centred on.
        let hud = Hud::default();
        let mut before = Vec::new();
        hud.build(&view(None), &mut before);
        let mut v = view(None);
        v.radar = None;
        let mut after = Vec::new();
        hud.build(&v, &mut after);
        assert_eq!(before.len(), after.len());
    }

    #[test]
    fn the_radar_shows_the_span_it_says_it_does() {
        // Direction is only half of it: a radar with the right orientation and
        // the wrong scale reads as an enemy being somewhere they are not, which
        // is the same lie in a quieter voice. Pinned against the browser's own
        // number — 110 cubes across the instrument — rather than against
        // whatever this painter happens to do.
        //
        // The view is 800 tall, so `u` is 2 and the radar is 152 px across:
        // 152 / 110 = 1.3818 px per cube.
        let cubes = 10.5;
        let ahead = Blip {
            x: 50.0 + cubes,
            y: 50.0,
            friendly: false,
        };
        let (_, dy) = blip_offset(ahead, 0.0);
        // `blip_offset` reports clip-space units; convert back to pixels against
        // the same height the view declares.
        let h = 800.0;
        let px = -dy / 2.0 * h;
        let expected = cubes * (2.0 * 38.0 * 2.0) / radar::SPAN;
        assert!(
            (px - expected).abs() < 0.5,
            "{cubes} cubes ahead drew {px:.2}px out, expected {expected:.2}px"
        );
    }
}
