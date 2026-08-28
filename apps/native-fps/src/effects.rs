//! Tracers, impacts, and the flash of a detonation.
//!
//! **Every shot here was resolved by the server.** A `shot` effect carries the
//! muzzle and one endpoint per pellet, so there is exactly one place that
//! decides where a bullet went and this file only draws what it is told. The
//! tempting alternative — raycasting the local player's own tracer so it appears
//! instantly — would be a second implementation of the world ray whose only job
//! is to disagree with the first one occasionally.
//!
//! The visible cost is that your own tracer appears half a round trip after the
//! trigger. What actually sells a shot is the muzzle flash and the crosshair
//! kick, and both of those are already local and immediate.
//!
//! ## What was being dropped
//!
//! `Fx::Shot` has carried `origin` and `ends` since shots existed. This client
//! declared neither: it matched on the effect, used the `id` to flash the muzzle,
//! and discarded the geometry. `Fx::Detonate` had no variant at all, so every
//! grenade going off arrived and evaporated.
//!
//! Neither was caught by the wire's own divergence check, and that is worth
//! knowing: `#[serde(flatten)]` cannot reach inside an enum variant, so a field
//! missing from `Fx::Shot` looks exactly like a field that does not exist. The
//! catch-all reports unknown *kinds*, not thin variants.
//!
//! ## Why these live in the volume pass
//!
//! They fade, so they blend, so they must not write depth — the same three
//! properties a smoke cloud has, which is the entire reason a pass exists. They
//! carry `MODE_FLAT` so the cloud's noise is not applied: a tracer is a beam a
//! few centimetres across, and noise across it would eat the line rather than
//! texture it.

use crate::nades::rgb;
use crate::renderer::{VolumeVertex, MODE_FLAT};

/// Tracer lifetime. Long enough to register, short enough not to draw a web.
const TRACER_LIFE: f32 = 0.075;
const IMPACT_LIFE: f32 = 0.3;
/// How long a detonation's shell lasts.
const BLAST_LIFE: f32 = 0.5;

/// Beyond this, the oldest effects are dropped rather than queued.
///
/// A cap on what is *drawn*, not on what is received: a shotgun is eight
/// tracers and eight impacts from one trigger pull, and a room of sixteen
/// players firing is a lot of very short-lived geometry.
const MAX_LIVE: usize = 192;

/// Hex, like the browser's, so the two can be compared without arithmetic.
const TRACER_COLOR: u32 = 0xffdb8c;
const IMPACT_COLOR: u32 = 0xffd9a0;

/// The colour a detonation throws.
///
/// Smoke and fire are tinted like what they *become*, so the pop and the cloud
/// that follows it read as one event rather than two.
fn blast_tint(kind: &str) -> [f32; 3] {
    rgb(match kind {
        "flash" => 0xffffff,
        "smoke" => 0xb9c2cc,
        "fire" => 0xff7a2a,
        _ => 0xffa64d,
    })
}

enum Shape {
    /// A beam between two points, in cube coordinates.
    Beam {
        from: [f32; 3],
        to: [f32; 3],
        radius: f32,
    },
    /// A sphere at a point.
    Ball { at: [f32; 3], radius: f32 },
}

struct Live {
    shape: Shape,
    color: [f32; 3],
    /// Opacity at birth. The fade is `base * (1 - t)` rather than a compounding
    /// decay, so an effect is fully gone exactly when its life is up instead of
    /// asymptotically nearly gone forever.
    base: f32,
    age: f32,
    life: f32,
}

#[derive(Default)]
pub struct EffectsPool {
    live: Vec<Live>,
}

impl EffectsPool {
    /// One resolved shot: a tracer and an impact per pellet.
    ///
    /// `mine` draws the tracer faint, because it leaves the camera: at full
    /// brightness your own tracer is a line down the middle of the screen and
    /// nothing else.
    pub fn shot(&mut self, origin: [f32; 3], ends: &[[f32; 3]], mine: bool) {
        for end in ends {
            self.push(Live {
                shape: Shape::Beam {
                    from: origin,
                    to: *end,
                    radius: 0.035,
                },
                color: rgb(TRACER_COLOR),
                base: if mine { 0.35 } else { 0.8 },
                age: 0.0,
                life: TRACER_LIFE,
            });
            self.push(Live {
                shape: Shape::Ball {
                    at: *end,
                    radius: 0.16,
                },
                color: rgb(IMPACT_COLOR),
                base: 0.9,
                age: 0.0,
                life: IMPACT_LIFE,
            });
        }
    }

    /// A grenade going off: a shell at the blast's real radius, and a brighter
    /// core inside it.
    ///
    /// The shell is drawn at **the radius the server used**, for the same reason
    /// a smoke is drawn at the radius it tests against: it is the one chance a
    /// player gets to learn how far an HE reaches, and a shell scaled for looks
    /// would teach the wrong number.
    pub fn detonate(&mut self, kind: &str, at: [f32; 3], radius: f32) {
        let tint = blast_tint(kind);
        self.push(Live {
            shape: Shape::Ball { at, radius },
            color: tint,
            base: 0.28,
            age: 0.0,
            life: BLAST_LIFE,
        });
        self.push(Live {
            shape: Shape::Ball {
                at,
                radius: (radius * 0.4).max(1.2),
            },
            color: tint,
            base: 0.75,
            age: 0.0,
            life: BLAST_LIFE * 0.45,
        });
    }

    fn push(&mut self, effect: Live) {
        if self.live.len() >= MAX_LIVE {
            // The oldest goes, not the newest: the shot that just happened is
            // the one worth seeing, and dropping it to preserve a tracer that is
            // three frames from expiry would be exactly backwards.
            self.live.remove(0);
        }
        self.live.push(effect);
    }

    /// Age everything and retire what is done.
    pub fn update(&mut self, dt: f32) {
        for e in &mut self.live {
            e.age += dt;
        }
        self.live.retain(|e| e.age < e.life);
    }

    #[cfg(test)]
    pub fn count(&self) -> usize {
        self.live.len()
    }

    /// This frame's effect geometry.
    pub fn vertices(&self, out: &mut Vec<VolumeVertex>) {
        for e in &self.live {
            let t = (e.age / e.life).clamp(0.0, 1.0);
            let alpha = e.base * (1.0 - t);
            if alpha <= 0.001 {
                continue;
            }
            match &e.shape {
                Shape::Beam { from, to, radius } => {
                    push_beam(out, *from, *to, *radius, e.color, alpha);
                }
                Shape::Ball { at, radius } => {
                    // A blast grows as it fades, which is what makes a pop read
                    // as an expansion rather than as a light being turned off.
                    // Tracer impacts have the short life that makes this a
                    // negligible growth for them.
                    let grown = radius * (0.55 + 0.45 * t);
                    push_ball(out, *at, grown, e.color, alpha);
                }
            }
        }
    }
}

/// A square prism between two points, in cube coordinates, emitted in render
/// coordinates.
///
/// Four sides and no caps: a tracer is seen from the side, and a cap on a beam
/// 7cm across is geometry nobody will ever see. Both faces are drawn by the
/// volume pipeline (`cull_mode: None`), so the winding does not matter here —
/// which it would if this ever moved to the opaque pass.
fn push_beam(
    out: &mut Vec<VolumeVertex>,
    from: [f32; 3],
    to: [f32; 3],
    radius: f32,
    color: [f32; 3],
    alpha: f32,
) {
    let dir = [to[0] - from[0], to[1] - from[1], to[2] - from[2]];
    let len = (dir[0] * dir[0] + dir[1] * dir[1] + dir[2] * dir[2]).sqrt();
    if len < 1e-4 {
        return;
    }
    let d = [dir[0] / len, dir[1] / len, dir[2] / len];
    // Any vector not parallel to the beam will do for the cross product. Picking
    // by the *smallest* component of the direction is what keeps a vertical
    // tracer — straight up at a player on a gantry — from choosing an axis it is
    // parallel to and producing a zero-length cross product, which collapses the
    // beam to nothing.
    let helper = if d[2].abs() < 0.9 {
        [0.0, 0.0, 1.0]
    } else {
        [1.0, 0.0, 0.0]
    };
    let u = normalize(cross(d, helper));
    let v = normalize(cross(d, u));
    let place = |p: [f32; 3]| [p[0], p[2], p[1]];
    let corner = |base: [f32; 3], a: f32, b: f32| {
        place([
            base[0] + u[0] * a * radius + v[0] * b * radius,
            base[1] + u[1] * a * radius + v[1] * b * radius,
            base[2] + u[2] * a * radius + v[2] * b * radius,
        ])
    };
    let quad = [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)];
    for i in 0..4 {
        let (a0, b0) = quad[i];
        let (a1, b1) = quad[(i + 1) % 4];
        let p0 = corner(from, a0, b0);
        let p1 = corner(from, a1, b1);
        let p2 = corner(to, a1, b1);
        let p3 = corner(to, a0, b0);
        let normal = place(normalize([
            u[0] * a0 + v[0] * b0,
            u[1] * a0 + v[1] * b0,
            u[2] * a0 + v[2] * b0,
        ]));
        for position in [p0, p1, p2, p0, p2, p3] {
            out.push(VolumeVertex {
                position,
                normal,
                color: [color[0], color[1], color[2], alpha],
                mode: MODE_FLAT,
            });
        }
    }
}

/// A low-poly sphere for an impact or a blast shell.
///
/// Built through `nades::push_sphere` and widened, rather than a second sphere
/// generator: two of those is two conventions about winding and about the
/// cube → render mapping, and the second one is always the one that is wrong.
fn push_ball(out: &mut Vec<VolumeVertex>, at: [f32; 3], radius: f32, color: [f32; 3], alpha: f32) {
    let mut scratch = Vec::new();
    crate::nades::push_sphere(&mut scratch, at, [radius; 3], 8, 6, color);
    out.extend(scratch.into_iter().map(|v| VolumeVertex {
        position: v.position,
        normal: v.normal,
        color: [color[0], color[1], color[2], alpha],
        mode: MODE_FLAT,
    }));
}

fn cross(a: [f32; 3], b: [f32; 3]) -> [f32; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

fn normalize(v: [f32; 3]) -> [f32; 3] {
    let len = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
    if len < 1e-6 {
        [0.0, 0.0, 1.0]
    } else {
        [v[0] / len, v[1] / len, v[2] / len]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The brightest thing drawn **near the muzzle**.
    ///
    /// A shot produces a tracer and an impact, and the impact is far brighter
    /// and far longer-lived — so the obvious `max` over every vertex measures
    /// the impact and reports the tracer as whatever the impact happens to be.
    /// Both of the tests below failed on exactly that before this existed. Only
    /// the beam reaches back to the origin, so position is what separates them.
    fn tracer_alpha(fx: &EffectsPool) -> f32 {
        let mut out = Vec::new();
        fx.vertices(&mut out);
        out.iter()
            .filter(|v| v.position[0].abs() < 1.0)
            .map(|v| v.color[3])
            .fold(0.0f32, f32::max)
    }

    #[test]
    fn a_shot_leaves_a_tracer_and_an_impact_per_pellet() {
        // A shotgun is one trigger pull and eight rays. Drawing one tracer for
        // the shot would hide the pattern, which is the whole character of the
        // weapon.
        let mut fx = EffectsPool::default();
        fx.shot([0.0; 3], &[[10.0, 0.0, 0.0], [10.0, 2.0, 0.0]], false);
        assert_eq!(fx.count(), 4);
    }

    #[test]
    fn a_shot_with_no_endpoints_draws_nothing() {
        // The server sends an empty `ends` for a shot that resolved to nothing.
        let mut fx = EffectsPool::default();
        fx.shot([0.0; 3], &[], false);
        assert_eq!(fx.count(), 0);
    }

    #[test]
    fn your_own_tracer_is_drawn_faint() {
        // It leaves the camera, so at full brightness it is a line down the
        // middle of the screen and nothing else.
        let mut mine = EffectsPool::default();
        mine.shot([0.0; 3], &[[10.0, 0.0, 0.0]], true);
        let mut theirs = EffectsPool::default();
        theirs.shot([0.0; 3], &[[10.0, 0.0, 0.0]], false);
        assert!(tracer_alpha(&mine) < tracer_alpha(&theirs));
    }

    #[test]
    fn effects_fade_to_nothing_and_are_retired() {
        let mut fx = EffectsPool::default();
        fx.shot([0.0; 3], &[[10.0, 0.0, 0.0]], false);
        fx.update(TRACER_LIFE + 0.001);
        // The tracer is gone; the impact outlives it.
        assert_eq!(fx.count(), 1);
        fx.update(IMPACT_LIFE);
        assert_eq!(fx.count(), 0);
        let mut out = Vec::new();
        fx.vertices(&mut out);
        assert!(out.is_empty());
    }

    #[test]
    fn the_fade_reaches_zero_exactly_when_the_life_is_up() {
        // `base * (1 - t)` rather than a compounding decay, which would leave an
        // effect asymptotically nearly gone forever and eat the cap.
        let mut fx = EffectsPool::default();
        fx.shot([0.0; 3], &[[10.0, 0.0, 0.0]], false);
        fx.update(TRACER_LIFE * 0.999);
        let max = tracer_alpha(&fx);
        assert!(max < 0.02, "still {max} bright at the end of its life");
    }

    #[test]
    fn the_pool_drops_the_oldest_rather_than_refusing_the_newest() {
        // The shot that just happened is the one worth seeing.
        let mut fx = EffectsPool::default();
        for _ in 0..MAX_LIVE {
            fx.shot([0.0; 3], &[[1.0, 0.0, 0.0]], false);
        }
        assert_eq!(fx.count(), MAX_LIVE);
        fx.shot([0.0; 3], &[[99.0, 0.0, 0.0]], false);
        assert_eq!(fx.count(), MAX_LIVE, "capped, not grown");
        let mut out = Vec::new();
        fx.vertices(&mut out);
        let far = out
            .iter()
            .any(|v| v.position[0] > 50.0 || v.position[2] > 50.0);
        assert!(far, "the newest shot was dropped instead of the oldest");
    }

    #[test]
    fn a_vertical_tracer_still_has_width() {
        // The cross-product trap: a beam parallel to the helper axis produces a
        // zero-length cross product and collapses to nothing. Straight up is the
        // shot at somebody on a gantry — rare enough to ship broken, common
        // enough to be noticed.
        let mut fx = EffectsPool::default();
        fx.shot([10.0, 10.0, 0.0], &[[10.0, 10.0, 20.0]], false);
        let mut out = Vec::new();
        fx.vertices(&mut out);
        let span = |axis: usize| {
            let lo = out
                .iter()
                .map(|v| v.position[axis])
                .fold(f32::MAX, f32::min);
            let hi = out
                .iter()
                .map(|v| v.position[axis])
                .fold(f32::MIN, f32::max);
            hi - lo
        };
        assert!(span(0) > 1e-3, "the beam has no width in x: {}", span(0));
        assert!(span(2) > 1e-3, "the beam has no width in z: {}", span(2));
    }

    #[test]
    fn a_tracer_spans_from_the_muzzle_to_the_endpoint() {
        // In render space the cube's y is z. A beam that came out short, or
        // along the wrong axis, means the cube → render mapping was applied to
        // one end and not the other.
        let mut fx = EffectsPool::default();
        fx.shot([0.0, 0.0, 0.0], &[[0.0, 30.0, 0.0]], false);
        let mut out = Vec::new();
        fx.vertices(&mut out);
        let hi = out.iter().map(|v| v.position[2]).fold(f32::MIN, f32::max);
        let lo = out.iter().map(|v| v.position[2]).fold(f32::MAX, f32::min);
        assert!((hi - 30.0).abs() < 0.2, "far end at {hi}, want 30");
        assert!(lo.abs() < 0.2, "near end at {lo}, want 0");
    }

    #[test]
    fn a_blast_is_drawn_at_the_radius_the_server_used() {
        // The one chance a player gets to learn how far an HE reaches. A shell
        // scaled for looks teaches the wrong number.
        let mut fx = EffectsPool::default();
        fx.detonate("he", [0.0; 3], 12.0);
        // Sampled at the end of its life, where the growth curve reaches 1.
        fx.update(BLAST_LIFE * 0.99);
        let mut out = Vec::new();
        fx.vertices(&mut out);
        let reach = out
            .iter()
            .map(|v| (v.position[0].powi(2) + v.position[1].powi(2) + v.position[2].powi(2)).sqrt())
            .fold(0.0f32, f32::max);
        assert!((reach - 12.0).abs() < 0.2, "shell reached {reach}, want 12");
    }

    #[test]
    fn every_effect_vertex_asks_for_flat_shading() {
        // Sharing the cloud's pass is deliberate; sharing its noise is not. A
        // tracer 7cm across would be eaten by it rather than textured.
        let mut fx = EffectsPool::default();
        fx.shot([0.0; 3], &[[10.0, 0.0, 0.0]], false);
        fx.detonate("he", [0.0; 3], 8.0);
        let mut out = Vec::new();
        fx.vertices(&mut out);
        assert!(!out.is_empty());
        assert!(out.iter().all(|v| v.mode == MODE_FLAT));
    }
}
