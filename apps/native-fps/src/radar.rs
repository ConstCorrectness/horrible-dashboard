//! The radar: what is on it, and the floor plan it is drawn over.
//!
//! **Which enemies appear is not this client's decision.** `you.spotted` is
//! resolved on the server by `MatchRoom.spotted_by`, because the answer depends
//! on two things only the server holds — the level's geometry and the smoke
//! standing in it. A client that decided for itself would be a wall hack with
//! extra steps, which is the same rule the noise mechanic follows and for the
//! same reason. This module *filters by team* and draws; it never judges
//! visibility. Gunfire (`you.heard`) is the same: resolved per side on the
//! server, drawn here as an origin and an age.
//!
//! Everything here is deliberately outside `hud.rs` and free of the painter, so
//! the rule that decides who is on the radar can be tested without a window: the
//! drawing is decoration, and the filter is the part that would be a cheat if it
//! were wrong.
//!
//! ## One radar, two clients
//!
//! The frame (span, centre, rotation), the blip sizes, the floor bands, the wall
//! outline and the palette are the browser's `radar-view.ts`, replayed from
//! `radar-vectors.json` by `mod conformance` below. A radar is the one HUD piece
//! where a quiet difference is a lie: the same contact at a different distance
//! from the centre depending on which client you launched.
//!
//! ## Why the floor plan is a list of runs
//!
//! The browser rasterises the map into an offscreen canvas once and blits it
//! every frame (`Radar.tsx`), which it can do because a canvas has an image and
//! a transform. This client has neither: the overlay is triangles, rebuilt from
//! nothing each frame. Emitting one quad per open cell would be ~12,000 quads a
//! frame for the 110-cube span the browser shows — more geometry than the map
//! itself.
//!
//! So open cells are merged along each row into **runs**, computed once when the
//! world loads. A floor plan is mostly long corridors, so this collapses to a
//! few hundred segments. A run also breaks where the floor's height band
//! changes, so upper levels read lighter without a quad per cell. It also
//! happens to solve the rotation problem for free: the radar turns with the
//! player, and a rotated axis-aligned rectangle is not axis-aligned any more —
//! but a run is a *segment*, and `Painter::line` already draws an oriented thick
//! quad.

use std::collections::HashMap;

use serde_json::Value;

use crate::protocol::{HeardRow, ModeBomb, NoiseEvent, PlayerRow};
use crate::world::World;

/// How many cubes fit across the radar at the default zoom. The browser's
/// `BASE_SPAN`; anything outside is clipped.
pub const SPAN: f32 = 110.0;
/// CS's `cl_radar_scale` default, at which the span is `SPAN`.
pub const DEFAULT_SCALE: f32 = 0.7;
/// The scale the radar-zoom key holds while pressed.
pub const ZOOM_SCALE: f32 = 0.4;
/// The icon scale at which every blip is the size it always was.
pub const DEFAULT_ICON_SCALE: f32 = 0.6;
/// Seconds a gunshot stays on the radar — the server's `HEARD_SECONDS`.
pub const HEARD_SECONDS: f32 = 2.5;
/// Seconds a footstep blob stays on the radar.
pub const SOUND_PING_SECONDS: f32 = 1.5;
/// Seconds a lost contact keeps its last-known marker.
pub const LAST_KNOWN_SECONDS: f32 = 3.0;
/// Brightness steps the floor plan is cut into, low to high.
pub const FLOOR_BANDS: u8 = 6;
/// World-space spacing of the faint grid, in cubes.
pub const GRID_CUBES: f32 = 16.0;
/// Radius of a footstep blob in cubes.
pub const SOUND_PING_CUBES: f32 = 4.0;
/// Seconds the own-noise ring takes to fade.
pub const OWN_NOISE_SECONDS: f32 = 0.7;

pub const KEY_SCALE: &str = "hassault.radar.scale";
pub const KEY_HUD_SCALE: &str = "hassault.radar.hudScale";
pub const KEY_ICON_SCALE: &str = "hassault.radar.iconScale";
pub const KEY_CENTERED: &str = "hassault.radar.centered";
pub const KEY_ROTATE: &str = "hassault.radar.rotate";
pub const KEY_FULL_ON_SCORES: &str = "hassault.radar.fullOnScores";
pub const KEY_SOUND_PINGS: &str = "hassault.radar.soundPings";

pub const SCALE_RANGE: (f32, f32) = (0.3, 1.3);
pub const HUD_SCALE_RANGE: (f32, f32) = (0.8, 1.3);
pub const ICON_SCALE_RANGE: (f32, f32) = (0.4, 1.0);

/// CS's `cl_radar_*`, as `settings_schema.py` declares them.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RadarSettings {
    pub scale: f32,
    pub hud_scale: f32,
    pub icon_scale: f32,
    pub centered: bool,
    pub rotate: bool,
    pub full_on_scores: bool,
    pub sound_pings: bool,
}

impl Default for RadarSettings {
    fn default() -> Self {
        RadarSettings {
            scale: DEFAULT_SCALE,
            hud_scale: 1.0,
            icon_scale: DEFAULT_ICON_SCALE,
            centered: true,
            rotate: true,
            full_on_scores: true,
            sound_pings: true,
        }
    }
}

impl RadarSettings {
    /// `readRadarSettings`: clamped to the schema, garbage falls back.
    ///
    /// A number may arrive as a string (the bag is "scalars anything may
    /// write"), exactly as the browser's `Number(v)` accepts one; a boolean must
    /// be a boolean, as there.
    pub fn from_values(values: &Value) -> RadarSettings {
        let d = RadarSettings::default();
        let num = |key: &str, (lo, hi): (f32, f32), fallback: f32| -> f32 {
            let n = match values.get(key) {
                Some(Value::Number(n)) => n.as_f64(),
                Some(Value::String(s)) if !s.trim().is_empty() => s.trim().parse::<f64>().ok(),
                _ => None,
            };
            match n {
                Some(n) if n.is_finite() => (n as f32).clamp(lo, hi),
                _ => fallback,
            }
        };
        let flag = |key: &str, fallback: bool| {
            values.get(key).and_then(Value::as_bool).unwrap_or(fallback)
        };
        RadarSettings {
            scale: num(KEY_SCALE, SCALE_RANGE, d.scale),
            hud_scale: num(KEY_HUD_SCALE, HUD_SCALE_RANGE, d.hud_scale),
            icon_scale: num(KEY_ICON_SCALE, ICON_SCALE_RANGE, d.icon_scale),
            centered: flag(KEY_CENTERED, d.centered),
            rotate: flag(KEY_ROTATE, d.rotate),
            full_on_scores: flag(KEY_FULL_ON_SCORES, d.full_on_scores),
            sound_pings: flag(KEY_SOUND_PINGS, d.sound_pings),
        }
    }

    /// The value to persist for one key.
    pub fn value_for(&self, key: &str) -> Option<Value> {
        Some(match key {
            KEY_SCALE => self.scale.into(),
            KEY_HUD_SCALE => self.hud_scale.into(),
            KEY_ICON_SCALE => self.icon_scale.into(),
            KEY_CENTERED => self.centered.into(),
            KEY_ROTATE => self.rotate.into(),
            KEY_FULL_ON_SCORES => self.full_on_scores.into(),
            KEY_SOUND_PINGS => self.sound_pings.into(),
            _ => return None,
        })
    }
}

/// What the instrument is looking at this frame — `radarFrame` in the browser.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Frame {
    /// Cubes across the instrument's diameter.
    pub span: f32,
    /// The world point under the instrument's centre.
    pub cx: f32,
    pub cy: f32,
    /// Rotation applied to the world, y-down canvas convention.
    pub angle: f32,
    /// Rotation of our arrow, drawn pointing up before it.
    pub arrow_angle: f32,
}

/// The view for this frame. See `radarFrame` in `radar-view.ts` for each rule;
/// this is that function, and the fixture holds the two to the same numbers.
pub fn frame(
    s: &RadarSettings,
    x: f32,
    y: f32,
    yaw: f32,
    ssize: f32,
    scores: bool,
    zoom: bool,
) -> Frame {
    use std::f32::consts::{FRAC_PI_2, SQRT_2};
    if scores && s.full_on_scores {
        return Frame {
            span: ssize * SQRT_2,
            cx: ssize / 2.0,
            cy: ssize / 2.0,
            angle: 0.0,
            arrow_angle: yaw + FRAC_PI_2,
        };
    }
    let scale = if zoom {
        s.scale.min(ZOOM_SCALE)
    } else {
        s.scale
    };
    let span = (SPAN * DEFAULT_SCALE) / scale;
    let (mut cx, mut cy) = (x, y);
    if !s.centered {
        let half = span / 2.0;
        let fit = |v: f32| {
            if half * 2.0 >= ssize {
                ssize / 2.0
            } else {
                v.clamp(half, ssize - half)
            }
        };
        cx = fit(x);
        cy = fit(y);
    }
    let angle = if s.rotate { -yaw - FRAC_PI_2 } else { 0.0 };
    let arrow_angle = if s.rotate { 0.0 } else { yaw + FRAC_PI_2 };
    Frame {
        span,
        cx,
        cy,
        angle,
        arrow_angle,
    }
}

/// A world point as an offset from the instrument's centre, rim at `radius`.
pub fn project(f: &Frame, radius: f32, wx: f32, wy: f32) -> (f32, f32) {
    let per_cube = (radius * 2.0) / f.span;
    let lx = (wx - f.cx) * per_cube;
    let ly = (wy - f.cy) * per_cube;
    let (sin, cos) = f.angle.sin_cos();
    (lx * cos - ly * sin, lx * sin + ly * cos)
}

/// What a blip is, for its size.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BlipKind {
    Mate,
    Enemy,
    Heard,
    LastKnown,
    Ping,
    Bomb,
    You,
}

impl BlipKind {
    pub fn name(self) -> &'static str {
        match self {
            BlipKind::Mate => "mate",
            BlipKind::Enemy => "enemy",
            BlipKind::Heard => "heard",
            BlipKind::LastKnown => "lastKnown",
            BlipKind::Ping => "ping",
            BlipKind::Bomb => "bomb",
            BlipKind::You => "you",
        }
    }

    /// Size at the default icon scale, as a fraction of the radius — the
    /// browser's pixels on its 84 px radius.
    fn base(self) -> f32 {
        let px = match self {
            BlipKind::Mate => 3.2,
            BlipKind::Enemy | BlipKind::Heard => 3.8,
            BlipKind::LastKnown => 3.4,
            BlipKind::Ping => 5.0,
            BlipKind::Bomb => 4.6,
            BlipKind::You => 6.0,
        };
        px / 84.0
    }
}

/// A blip's radius as a fraction of the instrument's, at this icon scale.
pub fn blip_radius(kind: BlipKind, icon_scale: f32) -> f32 {
    kind.base() * (icon_scale / DEFAULT_ICON_SCALE)
}

/// Which brightness band a floor height falls in, `0..FLOOR_BANDS`.
pub fn floor_band(floor: f32, lo: f32, hi: f32) -> u8 {
    let top = (FLOOR_BANDS - 1) as f32;
    if hi <= lo {
        return ((FLOOR_BANDS - 1) / 2) as u8;
    }
    let t = (floor - lo) / (hi - lo);
    // `f32::round` rounds half away from zero; `Math.round` rounds half up.
    // For a non-negative `t * top` the two agree, and the clamp keeps it so.
    (t * top).round().clamp(0.0, top) as u8
}

pub type Rgba = [f32; 4];

/// The radar's palette — `RADAR_COLORS` in `radar-view.ts`, pinned by the
/// fixture.
pub mod colors {
    use super::Rgba;
    pub const GROUND: Rgba = [0.031, 0.043, 0.063, 0.88];
    pub const GRID: Rgba = [0.549, 0.667, 0.824, 0.07];
    pub const FLOOR_LOW: Rgba = [0.11, 0.15, 0.21, 0.82];
    pub const FLOOR_HIGH: Rgba = [0.47, 0.57, 0.69, 0.92];
    pub const WALL: Rgba = [0.784, 0.863, 0.961, 0.8];
    pub const SITE: Rgba = [0.984, 0.749, 0.251, 0.16];
    pub const SITE_EDGE: Rgba = [0.984, 0.749, 0.251, 0.85];
    pub const YOU: Rgba = [0.494, 0.906, 0.529, 1.0];
    pub const MATE: Rgba = [0.345, 0.651, 1.0, 0.95];
    pub const ENEMY: Rgba = [0.973, 0.318, 0.286, 1.0];
    pub const SOUND_PING: Rgba = [0.973, 0.318, 0.286, 0.32];
    pub const OWN_NOISE: Rgba = [0.784, 0.863, 0.961, 0.3];
    pub const BOMB: Rgba = [1.0, 0.584, 0.2, 1.0];
    pub const RIM: Rgba = [0.706, 0.784, 0.902, 0.32];

    /// Every entry by its name in the browser's table.
    pub const ALL: [(&str, Rgba); 14] = [
        ("ground", GROUND),
        ("grid", GRID),
        ("floorLow", FLOOR_LOW),
        ("floorHigh", FLOOR_HIGH),
        ("wall", WALL),
        ("site", SITE),
        ("siteEdge", SITE_EDGE),
        ("you", YOU),
        ("mate", MATE),
        ("enemy", ENEMY),
        ("soundPing", SOUND_PING),
        ("ownNoise", OWN_NOISE),
        ("bomb", BOMB),
        ("rim", RIM),
    ];
}

/// A palette entry with its alpha scaled.
pub fn faded(c: Rgba, alpha: f32) -> Rgba {
    [c[0], c[1], c[2], c[3] * alpha.clamp(0.0, 1.0)]
}

/// The floor colour for a band: the two floor ends, mixed.
pub fn floor_color(band: u8) -> Rgba {
    let t = if FLOOR_BANDS > 1 {
        band as f32 / (FLOOR_BANDS - 1) as f32
    } else {
        0.0
    };
    let (lo, hi) = (colors::FLOOR_LOW, colors::FLOOR_HIGH);
    [
        lo[0] + (hi[0] - lo[0]) * t,
        lo[1] + (hi[1] - lo[1]) * t,
        lo[2] + (hi[2] - lo[2]) * t,
        lo[3] + (hi[3] - lo[3]) * t,
    ]
}

/// 0..1, fading out over a life; 0 once it is over.
pub fn fade_out(age: f32, life: f32) -> f32 {
    if life <= 0.0 {
        return 0.0;
    }
    (1.0 - age / life).clamp(0.0, 1.0)
}

/// The audible radius, in cubes, of one of our own noises — what the ring
/// round our arrow is sized to (CS2's "how far can I be heard").
///
/// `noise.py`'s constants, through `OWN_NOISE_RADII` in the browser; the
/// fixture carries the table and the backend suite pins it to the Python. A
/// shot is `noise.shot_loudness`: `damage` is the held weapon's.
pub fn own_noise_radius(kind: &str, damage: Option<f32>, knife: bool) -> Option<f32> {
    let base = kind.split('_').next().unwrap_or(kind);
    Some(match base {
        "step" => 42.0,
        "land" => 55.0,
        "jump" => 30.0,
        "reload" => 34.0,
        "splash" => 62.0,
        "pickup" => 26.0,
        "shot" => shot_radius(damage?, knife),
        _ => return None,
    })
}

/// `noise.shot_loudness`: a shot's radius from the weapon's damage.
pub fn shot_radius(damage: f32, knife: bool) -> f32 {
    if knife {
        return 8.0;
    }
    120.0 * (damage / 60.0 + 0.35).min(1.0)
}

/// One horizontal run of open cells of one floor band, in cube coordinates.
///
/// `x0..x1` is a half-open span along `y`, so a run of one cell is one cube
/// wide rather than zero.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Run {
    pub y: f32,
    pub x0: f32,
    pub x1: f32,
    pub band: u8,
}

/// One straight piece of wall outline, in cube coordinates.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Edge {
    pub x0: f32,
    pub y0: f32,
    pub x1: f32,
    pub y1: f32,
}

/// One body on the radar, already resolved to "should be drawn".
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Blip {
    pub x: f32,
    pub y: f32,
    /// Ours, which is what decides the colour and the size. There is no third
    /// state: a body that is neither is not on the radar at all.
    pub friendly: bool,
}

/// The floor heights of the open cells, low and high.
pub fn floor_range(world: &World) -> (f32, f32) {
    let (mut lo, mut hi) = (f32::INFINITY, f32::NEG_INFINITY);
    for y in 0..world.ssize {
        for x in 0..world.ssize {
            if world.is_solid(x, y) {
                continue;
            }
            let f = world.floor_at(x, y);
            lo = lo.min(f);
            hi = hi.max(f);
        }
    }
    if lo.is_finite() {
        (lo, hi)
    } else {
        (0.0, 0.0)
    }
}

/// Merge the map's open cells into horizontal runs of one band, once per map.
///
/// Built from `is_solid` rather than from the mesh, exactly as the browser does:
/// a radar wants the **floor plan** — where you cannot walk — and a mesh is a
/// set of surfaces, which is a different question with a much more expensive
/// answer.
///
/// Open cells are what gets painted, not solid ones. Painting the solid cube
/// instead gives a map that reads inside-out, and it is an easy mistake to make
/// because both produce a plausible-looking picture.
pub fn floor_plan(world: &World) -> Vec<Run> {
    let (lo, hi) = floor_range(world);
    let mut runs = Vec::new();
    for y in 0..world.ssize {
        let mut start: Option<(i32, u8)> = None;
        for x in 0..world.ssize {
            let here = (!world.is_solid(x, y)).then(|| floor_band(world.floor_at(x, y), lo, hi));
            match (here, start) {
                (Some(band), None) => start = Some((x, band)),
                (Some(band), Some((x0, open))) if band != open => {
                    runs.push(Run {
                        y: y as f32 + 0.5,
                        x0: x0 as f32,
                        x1: x as f32,
                        band: open,
                    });
                    start = Some((x, band));
                }
                (None, Some((x0, band))) => {
                    runs.push(Run {
                        y: y as f32 + 0.5,
                        x0: x0 as f32,
                        x1: x as f32,
                        band,
                    });
                    start = None;
                }
                _ => {}
            }
        }
        // A row that is open all the way to the edge has no closing cell to end
        // it. Forgetting this drops the last run of every such row, which on an
        // open map is most of the floor plan and reads as a map with a chunk
        // missing on one side.
        if let Some((x0, band)) = start {
            runs.push(Run {
                y: y as f32 + 0.5,
                x0: x0 as f32,
                x1: world.ssize as f32,
                band,
            });
        }
    }
    runs
}

/// Every boundary between an open cell and a solid one, merged into the longest
/// straight segments — `wallEdges` in the browser, in the same order.
///
/// This is what turns a floor plan into a map: filled cells alone read as a
/// blur at radar scale, and a crisp line where floor meets wall is what an eye
/// finds a corridor by. Outside the grid counts as solid.
pub fn wall_edges(ssize: i32, is_solid: impl Fn(i32, i32) -> bool) -> Vec<Edge> {
    let open = |x: i32, y: i32| x >= 0 && y >= 0 && x < ssize && y < ssize && !is_solid(x, y);
    let mut out = Vec::new();
    for y in 0..=ssize {
        let mut start: Option<i32> = None;
        for x in 0..=ssize {
            let edge = x < ssize && open(x, y - 1) != open(x, y);
            match (edge, start) {
                (true, None) => start = Some(x),
                (false, Some(x0)) => {
                    out.push(Edge {
                        x0: x0 as f32,
                        y0: y as f32,
                        x1: x as f32,
                        y1: y as f32,
                    });
                    start = None;
                }
                _ => {}
            }
        }
    }
    for x in 0..=ssize {
        let mut start: Option<i32> = None;
        for y in 0..=ssize {
            let edge = y < ssize && open(x - 1, y) != open(x, y);
            match (edge, start) {
                (true, None) => start = Some(y),
                (false, Some(y0)) => {
                    out.push(Edge {
                        x0: x as f32,
                        y0: y0 as f32,
                        x1: x as f32,
                        y1: y as f32,
                    });
                    start = None;
                }
                _ => {}
            }
        }
    }
    out
}

/// `wall_edges` for a loaded world.
pub fn world_edges(world: &World) -> Vec<Edge> {
    wall_edges(world.ssize, |x, y| world.is_solid(x, y))
}

/// Who belongs on the radar this frame.
///
/// Two rules, and the asymmetry between them is the whole thing:
///
/// - **Teammates are unconditional.** That is a radio, not a sensor, and every
///   team shooter works this way. They are deliberately *not* in `spotted` —
///   sending a per-player id list that never changes would be twenty bytes a
///   tick to say something already known.
/// - **An enemy needs to have been seen by somebody on our side**, which is
///   precisely what `spotted` is. Never by us specifically: the point of the
///   mechanic is that a teammate looking down a corridor paints it for the whole
///   team.
///
/// The dead are dropped. A corpse on the radar is a body that is not there, and
/// "an enemy is standing here" is the one thing a radar must not get wrong.
pub fn blips(rows: &[PlayerRow], self_id: &str, my_team: i32, spotted: &[String]) -> Vec<Blip> {
    rows.iter()
        .filter(|row| row.id != self_id && row.alive)
        .filter_map(|row| {
            let friendly = row.team == my_team;
            if !friendly && !spotted.iter().any(|id| id == &row.id) {
                return None;
            }
            Some(Blip {
                x: row.x,
                y: row.y,
                friendly,
            })
        })
        .collect()
}

/// A thing on the radar that is not a live body.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum MarkKind {
    /// Where an enemy was when they stopped being spotted.
    LastKnown,
    /// Where an unsuppressed shot came from.
    Heard,
    /// An enemy footstep, at roughly where it sounded.
    Sound,
    /// The bomb on the ground or planted; `pulse` is 0..1.
    Bomb { pulse: f32 },
    /// A ring round whoever carries the bomb.
    Carrier,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Mark {
    pub x: f32,
    pub y: f32,
    pub kind: MarkKind,
    /// 0..1, how much of its life is left.
    pub life: f32,
    /// Seconds since it appeared, for an entrance.
    pub age: f32,
}

#[derive(Debug, Clone, Copy)]
struct Sound {
    x: f32,
    y: f32,
    at: f32,
}

/// What the radar remembers between frames: last-known positions and the
/// footsteps of the last second and a half. Only things this client was
/// already sent — memory, not information.
#[derive(Debug, Default)]
pub struct Memory {
    /// Enemy id → where they were last spotted, and when they stopped being.
    seen: HashMap<String, (f32, f32, Option<f32>)>,
    sounds: Vec<Sound>,
}

impl Memory {
    /// Update last-known from this frame's roster. `now` is seconds on any
    /// steady clock.
    pub fn track(
        &mut self,
        rows: &[PlayerRow],
        self_id: &str,
        my_team: i32,
        spotted: &[String],
        now: f32,
    ) {
        for row in rows {
            if row.id == self_id || row.team == my_team || !row.alive {
                continue;
            }
            if spotted.iter().any(|id| id == &row.id) {
                self.seen.insert(row.id.clone(), (row.x, row.y, None));
            }
        }
        self.seen.retain(|id, (_, _, lost)| {
            let alive = rows.iter().any(|r| &r.id == id && r.alive);
            if !alive {
                return false;
            }
            match lost {
                Some(t) => now - *t <= LAST_KNOWN_SECONDS,
                None => {
                    if !spotted.iter().any(|s| s == id) {
                        *lost = Some(now);
                    }
                    true
                }
            }
        });
    }

    /// Remember the foe noises just heard, anchored where we stood.
    pub fn hear(&mut self, events: &[NoiseEvent], from: (f32, f32), now: f32) {
        self.sounds.retain(|s| now - s.at < SOUND_PING_SECONDS);
        for e in events {
            let (true, Some(est)) = (e.foe, e.est) else {
                continue;
            };
            self.sounds.push(Sound {
                x: from.0 + e.bearing.cos() * est,
                y: from.1 + e.bearing.sin() * est,
                at: now,
            });
        }
    }

    /// Everything that is not a live body, for this frame.
    pub fn marks(
        &self,
        now: f32,
        heard: &[HeardRow],
        bomb: Option<&ModeBomb>,
        carrier_at: Option<(f32, f32)>,
        sound_pings: bool,
        out: &mut Vec<Mark>,
    ) {
        out.clear();
        for (x, y, lost) in self.seen.values() {
            if let Some(t) = lost {
                let age = now - t;
                out.push(Mark {
                    x: *x,
                    y: *y,
                    kind: MarkKind::LastKnown,
                    life: fade_out(age, LAST_KNOWN_SECONDS),
                    age,
                });
            }
        }
        if sound_pings {
            for s in &self.sounds {
                let age = now - s.at;
                let life = fade_out(age, SOUND_PING_SECONDS);
                if life > 0.0 {
                    out.push(Mark {
                        x: s.x,
                        y: s.y,
                        kind: MarkKind::Sound,
                        life,
                        age,
                    });
                }
            }
        }
        for h in heard {
            let life = fade_out(h.age, HEARD_SECONDS);
            if life > 0.0 {
                out.push(Mark {
                    x: h.x,
                    y: h.y,
                    kind: MarkKind::Heard,
                    life,
                    age: h.age,
                });
            }
        }
        if let Some(b) = bomb {
            match b.state.as_str() {
                "dropped" => out.push(Mark {
                    x: b.x,
                    y: b.y,
                    kind: MarkKind::Bomb { pulse: 1.0 },
                    life: 1.0,
                    age: 0.0,
                }),
                "planted" => {
                    let rate = 2.0 + 6.0 / b.fuse_in.max(1.0);
                    let pulse = 0.6 + 0.4 * (now * std::f32::consts::PI * rate).sin().abs();
                    out.push(Mark {
                        x: b.x,
                        y: b.y,
                        kind: MarkKind::Bomb { pulse },
                        life: 1.0,
                        age: 0.0,
                    });
                }
                _ => {}
            }
        }
        if let Some((x, y)) = carrier_at {
            out.push(Mark {
                x,
                y,
                kind: MarkKind::Carrier,
                life: 1.0,
                age: 0.0,
            });
        }
    }
}

/// Clip a segment to a circle centred on the origin.
///
/// Returns the portion inside, or `None` when the segment misses entirely.
///
/// Needed because there is no clip here. The browser gets a circular radar from
/// `ctx.clip()`; a triangle painter has to cut the geometry itself, and a floor
/// plan drawn without cutting is a **square** minimap with a ring drawn hopefully
/// around it — corners of the map spilling past the edge of the instrument.
pub fn clip_to_circle(
    (x0, y0): (f32, f32),
    (x1, y1): (f32, f32),
    radius: f32,
) -> Option<((f32, f32), (f32, f32))> {
    let (dx, dy) = (x1 - x0, y1 - y0);
    let a = dx * dx + dy * dy;
    if a <= f32::EPSILON {
        // A degenerate segment is a point: inside or not, with no span to cut.
        return (x0 * x0 + y0 * y0 <= radius * radius).then_some(((x0, y0), (x1, y1)));
    }
    let b = 2.0 * (x0 * dx + y0 * dy);
    let c = x0 * x0 + y0 * y0 - radius * radius;
    let disc = b * b - 4.0 * a * c;
    if disc < 0.0 {
        return None;
    }
    let root = disc.sqrt();
    // The parametric span inside the circle, intersected with the segment's own
    // 0..1. Clamping rather than testing is what keeps a segment that starts
    // inside and leaves — the common case — from being discarded.
    let t0 = ((-b - root) / (2.0 * a)).max(0.0);
    let t1 = ((-b + root) / (2.0 * a)).min(1.0);
    if t1 <= t0 {
        return None;
    }
    Some(((x0 + dx * t0, y0 + dy * t0), (x0 + dx * t1, y0 + dy * t1)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(id: &str, team: i32, alive: bool) -> PlayerRow {
        PlayerRow {
            id: id.into(),
            team,
            alive,
            x: 10.0,
            y: 20.0,
            ..Default::default()
        }
    }

    #[test]
    fn a_teammate_is_on_the_radar_without_being_spotted() {
        // The radio rule. Requiring `spotted` for teammates would make the radar
        // useless for the one thing people actually use it for.
        let rows = [row("mate", 0, true)];
        let out = blips(&rows, "me", 0, &[]);
        assert_eq!(out.len(), 1);
        assert!(out[0].friendly);
    }

    #[test]
    fn an_enemy_appears_only_once_the_server_says_they_are_spotted() {
        let rows = [row("enemy", 1, true)];
        assert!(
            blips(&rows, "me", 0, &[]).is_empty(),
            "an unspotted enemy on the radar is a wall hack"
        );
        let out = blips(&rows, "me", 0, &["enemy".to_string()]);
        assert_eq!(out.len(), 1);
        assert!(!out[0].friendly);
    }

    #[test]
    fn spotted_is_the_teams_answer_and_not_ours() {
        // The id list is resolved per *team* on the server. Nothing here may
        // narrow it to "what I personally can see" — a teammate looking down a
        // corridor painting it for everyone is the entire mechanic.
        let rows = [row("enemy", 1, true)];
        let out = blips(&rows, "me", 0, &["enemy".to_string()]);
        assert_eq!(out.len(), 1, "we are not required to see them ourselves");
    }

    #[test]
    fn the_dead_and_ourselves_are_not_drawn() {
        let rows = [row("me", 0, true), row("mate", 0, false)];
        assert!(
            blips(&rows, "me", 0, &[]).is_empty(),
            "our own arrow is drawn at the centre, and a corpse is not a body"
        );
    }

    #[test]
    fn a_spotted_id_for_a_body_that_is_gone_draws_nothing() {
        // The roster and `spotted` are built in the same tick, but a client may
        // hold a snapshot where one has moved on. Iterating the roster and
        // consulting `spotted` — rather than the reverse — means a stale id is
        // simply not found, instead of a blip at a position nobody supplied.
        let rows = [row("mate", 0, true)];
        let out = blips(&rows, "me", 0, &["ghost".to_string()]);
        assert_eq!(out.len(), 1, "only the teammate");
    }

    #[test]
    fn a_lost_contact_leaves_a_last_known_mark_that_expires() {
        let mut m = Memory::default();
        let spotted = ["enemy".to_string()];
        let rows = [row("enemy", 1, true)];
        m.track(&rows, "me", 0, &spotted, 0.0);
        let mut marks = Vec::new();
        m.marks(0.5, &[], None, None, true, &mut marks);
        assert!(
            marks.is_empty(),
            "still spotted: drawn as a blip, not a mark"
        );
        m.track(&rows, "me", 0, &[], 1.0);
        m.marks(1.5, &[], None, None, true, &mut marks);
        assert_eq!(marks.len(), 1);
        assert_eq!(marks[0].kind, MarkKind::LastKnown);
        assert_eq!((marks[0].x, marks[0].y), (10.0, 20.0));
        m.track(&rows, "me", 0, &[], 1.0 + LAST_KNOWN_SECONDS + 0.1);
        m.marks(
            1.0 + LAST_KNOWN_SECONDS + 0.1,
            &[],
            None,
            None,
            true,
            &mut marks,
        );
        assert!(marks.is_empty());
    }

    #[test]
    fn a_dead_contact_leaves_no_question_mark() {
        // "Somebody might be here" about a corpse is the one claim a radar must
        // not make.
        let mut m = Memory::default();
        m.track(
            &[row("enemy", 1, true)],
            "me",
            0,
            &["enemy".to_string()],
            0.0,
        );
        m.track(&[row("enemy", 1, false)], "me", 0, &[], 0.1);
        let mut marks = Vec::new();
        m.marks(0.2, &[], None, None, true, &mut marks);
        assert!(marks.is_empty());
    }

    #[test]
    fn only_foe_noises_with_a_distance_become_blobs() {
        let mut m = Memory::default();
        let mut foe = NoiseEvent {
            kind: "step".into(),
            bearing: 0.0,
            foe: true,
            est: Some(16.0),
            ..Default::default()
        };
        let mate = NoiseEvent {
            kind: "step".into(),
            foe: false,
            est: Some(8.0),
            ..Default::default()
        };
        let old = NoiseEvent {
            kind: "step".into(),
            foe: true,
            est: None,
            ..Default::default()
        };
        m.hear(&[foe.clone(), mate, old], (100.0, 50.0), 0.0);
        let mut marks = Vec::new();
        m.marks(0.1, &[], None, None, true, &mut marks);
        assert_eq!(marks.len(), 1);
        assert!((marks[0].x - 116.0).abs() < 1e-4 && (marks[0].y - 50.0).abs() < 1e-4);
        m.marks(0.1, &[], None, None, false, &mut marks);
        assert!(marks.is_empty(), "the setting turns them off");
        foe.est = Some(0.0);
        m.hear(&[foe], (0.0, 0.0), SOUND_PING_SECONDS + 1.0);
        m.marks(SOUND_PING_SECONDS + 1.0, &[], None, None, true, &mut marks);
        assert_eq!(marks.len(), 1, "the old blob aged out, the new one stays");
    }

    #[test]
    fn the_floor_plan_covers_the_open_cells_and_not_the_walls() {
        let world = World::test_box(8);
        let runs = floor_plan(&world);
        assert!(!runs.is_empty());
        for run in &runs {
            assert!(run.x1 > run.x0, "a run must have width");
            for x in (run.x0 as i32)..(run.x1 as i32) {
                assert!(
                    !world.is_solid(x, run.y as i32),
                    "run at y={} covers solid cell x={x}",
                    run.y
                );
            }
        }
        // Every open cell is inside exactly one run: a floor plan with holes in
        // it reads as a map with rooms that do not exist.
        for y in 0..world.ssize {
            for x in 0..world.ssize {
                if world.is_solid(x, y) {
                    continue;
                }
                let covered = runs
                    .iter()
                    .filter(|r| r.y as i32 == y && (r.x0 as i32..r.x1 as i32).contains(&x))
                    .count();
                assert_eq!(covered, 1, "cell {x},{y} is in {covered} runs");
            }
        }
    }

    #[test]
    fn a_row_open_to_the_edge_still_ends_its_run() {
        // The off-by-one that drops the last run of every row with no closing
        // wall. On an open map that is most of the floor plan.
        let world = World::test_open(4);
        let runs = floor_plan(&world);
        assert_eq!(runs.len(), 4, "one full-width run per row");
        assert_eq!(runs[0].x0, 0.0);
        assert_eq!(runs[0].x1, 4.0);
    }

    #[test]
    fn a_run_breaks_where_the_floor_changes_level() {
        // A raised half: the upper level must read as its own band rather than
        // being merged into the lower one's run.
        let world = World::test_build(4, |planes| {
            for y in 0..4 {
                for x in 2..4 {
                    planes[1][(y * 4 + x) as usize] = 8;
                }
            }
        });
        let runs = floor_plan(&world);
        assert_eq!(runs.len(), 8, "two runs per row");
        assert!(runs[1].band > runs[0].band, "higher floor, brighter band");
    }

    #[test]
    fn a_segment_crossing_the_circle_is_cut_at_the_rim() {
        let (a, b) = clip_to_circle((-10.0, 0.0), (10.0, 0.0), 5.0).unwrap();
        assert!((a.0 + 5.0).abs() < 1e-4, "{a:?}");
        assert!((b.0 - 5.0).abs() < 1e-4, "{b:?}");
    }

    #[test]
    fn a_segment_that_starts_inside_and_leaves_keeps_its_start() {
        // The common case, and the one a naive "both ends outside?" test throws
        // away: most runs begin under the player and run off the edge.
        let (a, b) = clip_to_circle((0.0, 0.0), (20.0, 0.0), 5.0).unwrap();
        assert_eq!(a, (0.0, 0.0));
        assert!((b.0 - 5.0).abs() < 1e-4, "{b:?}");
    }

    #[test]
    fn a_segment_that_misses_entirely_is_dropped() {
        assert!(clip_to_circle((-10.0, 9.0), (10.0, 9.0), 5.0).is_none());
        // Collinear with the centre but entirely beyond it, which is the case a
        // discriminant test alone lets through: the *line* hits the circle, the
        // *segment* does not.
        assert!(clip_to_circle((6.0, 0.0), (20.0, 0.0), 5.0).is_none());
    }

    #[test]
    fn a_segment_wholly_inside_is_returned_untouched() {
        let (a, b) = clip_to_circle((-1.0, 0.0), (1.0, 0.0), 5.0).unwrap();
        assert_eq!(a, (-1.0, 0.0));
        assert_eq!(b, (1.0, 0.0));
    }
}

/// The browser's `radar-view.ts`, replayed.
#[cfg(test)]
mod conformance {
    use super::*;

    /// `include_str!`, not a runtime read, and the *same file* the vitest suite
    /// writes — see the note in `geometry.rs`'s conformance module.
    const VECTORS: &str =
        include_str!("../../../packages/core/src/modules/hassault/__tests__/radar-vectors.json");

    fn vectors() -> Value {
        serde_json::from_str(VECTORS).expect("radar-vectors.json should parse")
    }

    fn f(v: &Value) -> f32 {
        v.as_f64().expect("a number") as f32
    }

    fn close(a: f32, b: f32, what: &str) {
        // f32 here, f64 there: a few ulps of a number in the hundreds.
        let tol = 1e-4_f32.max(b.abs() * 2e-6);
        assert!((a - b).abs() <= tol, "{what}: {a} != {b}");
    }

    fn settings_of(v: &Value) -> RadarSettings {
        RadarSettings {
            scale: f(&v["scale"]),
            hud_scale: f(&v["hudScale"]),
            icon_scale: f(&v["iconScale"]),
            centered: v["centered"].as_bool().unwrap(),
            rotate: v["rotate"].as_bool().unwrap(),
            full_on_scores: v["fullOnScores"].as_bool().unwrap(),
            sound_pings: v["soundPings"].as_bool().unwrap(),
        }
    }

    #[test]
    fn the_constants_are_the_browsers() {
        let c = &vectors()["constants"];
        close(SPAN, f(&c["baseSpan"]), "baseSpan");
        close(DEFAULT_SCALE, f(&c["defaultScale"]), "defaultScale");
        close(ZOOM_SCALE, f(&c["zoomScale"]), "zoomScale");
        close(
            DEFAULT_ICON_SCALE,
            f(&c["defaultIconScale"]),
            "defaultIconScale",
        );
        close(HEARD_SECONDS, f(&c["heardSeconds"]), "heardSeconds");
        assert_eq!(FLOOR_BANDS as f64, c["floorBands"].as_f64().unwrap());
        assert_eq!(
            RadarSettings::default(),
            settings_of(&vectors()["defaults"])
        );
    }

    #[test]
    fn the_settings_reader_is_the_browsers() {
        for case in vectors()["reads"].as_array().unwrap() {
            let got = RadarSettings::from_values(&case["bag"]);
            let want = settings_of(&case["settings"]);
            close(got.scale, want.scale, "scale");
            close(got.hud_scale, want.hud_scale, "hudScale");
            close(got.icon_scale, want.icon_scale, "iconScale");
            assert_eq!(
                (
                    got.centered,
                    got.rotate,
                    got.full_on_scores,
                    got.sound_pings
                ),
                (
                    want.centered,
                    want.rotate,
                    want.full_on_scores,
                    want.sound_pings
                ),
                "{}",
                case["bag"]
            );
        }
    }

    #[test]
    fn every_frame_and_projection_is_the_browsers() {
        for case in vectors()["frames"].as_array().unwrap() {
            let name = case["name"].as_str().unwrap();
            let s = settings_of(&case["settings"]);
            let me = &case["me"];
            let got = frame(
                &s,
                f(&me["x"]),
                f(&me["y"]),
                f(&me["yaw"]),
                f(&case["ssize"]),
                case["scores"].as_bool().unwrap(),
                case["zoom"].as_bool().unwrap(),
            );
            let want = &case["frame"];
            close(got.span, f(&want["span"]), &format!("{name} span"));
            close(got.cx, f(&want["cx"]), &format!("{name} cx"));
            close(got.cy, f(&want["cy"]), &format!("{name} cy"));
            close(got.angle, f(&want["angle"]), &format!("{name} angle"));
            close(
                got.arrow_angle,
                f(&want["arrowAngle"]),
                &format!("{name} arrow"),
            );
            for p in case["projected"].as_array().unwrap() {
                let (px, py) = project(&got, 1.0, f(&p["x"]), f(&p["y"]));
                close(px, f(&p["at"][0]), &format!("{name} project x"));
                close(py, f(&p["at"][1]), &format!("{name} project y"));
            }
        }
    }

    #[test]
    fn blip_sizes_bands_and_colours_are_the_browsers() {
        let v = vectors();
        let kinds = [
            BlipKind::Mate,
            BlipKind::Enemy,
            BlipKind::Heard,
            BlipKind::LastKnown,
            BlipKind::Ping,
            BlipKind::Bomb,
            BlipKind::You,
        ];
        for b in v["blips"].as_array().unwrap() {
            let kind = kinds
                .iter()
                .find(|k| k.name() == b["kind"].as_str().unwrap())
                .expect("a known kind");
            close(
                blip_radius(*kind, f(&b["iconScale"])),
                f(&b["radius"]),
                kind.name(),
            );
        }
        for b in v["bands"].as_array().unwrap() {
            assert_eq!(
                floor_band(f(&b["floor"]), f(&b["lo"]), f(&b["hi"])) as f64,
                b["band"].as_f64().unwrap(),
                "{b}"
            );
        }
        for c in v["floorColors"].as_array().unwrap() {
            let ours = floor_color(c["band"].as_u64().unwrap() as u8);
            for i in 0..4 {
                close(ours[i], f(&c["rgba"][i]), "floor colour");
            }
        }
        let theirs = v["colors"].as_object().unwrap();
        assert_eq!(
            theirs.len(),
            colors::ALL.len(),
            "the palettes name different entries"
        );
        for (name, ours) in colors::ALL {
            let want = &theirs[name];
            for i in 0..4 {
                close(ours[i], f(&want[i]), name);
            }
        }
    }

    #[test]
    fn the_own_noise_radii_are_the_browsers() {
        let v = vectors();
        let radii = v["noise"]["ownRadii"].as_object().unwrap();
        for (kind, want) in radii {
            close(own_noise_radius(kind, None, false).expect(kind), f(want), kind);
        }
        for s in v["noise"]["shots"].as_array().unwrap() {
            close(shot_radius(f(&s["damage"]), false), f(&s["radius"]), "shot");
        }
        close(shot_radius(50.0, true), f(&v["noise"]["knife"]), "knife");
        assert_eq!(own_noise_radius("step_metal", None, false), Some(42.0), "a step on a vent is a step");
    }

    #[test]
    fn the_wall_outline_is_the_browsers() {
        let v = vectors();
        let grid: Vec<Vec<u8>> = v["edges"]["grid"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| r.as_str().unwrap().bytes().collect())
            .collect();
        let n = grid.len() as i32;
        let got = wall_edges(n, |x, y| grid[y as usize][x as usize] != b'.');
        let want: Vec<Edge> = v["edges"]["edges"]
            .as_array()
            .unwrap()
            .iter()
            .map(|e| Edge {
                x0: f(&e["x0"]),
                y0: f(&e["y0"]),
                x1: f(&e["x1"]),
                y1: f(&e["y1"]),
            })
            .collect();
        assert_eq!(got, want);
    }
}
