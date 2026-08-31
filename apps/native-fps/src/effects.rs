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
/// Sparks off a surface. Shorter than the impact flash they come out of, so the
/// bright point outlives the debris rather than the other way round.
const SPARK_LIFE: f32 = 0.14;
/// The dust a round knocks out of a wall. Much longer than everything else here,
/// and the only slow thing in the file: dust is what tells you a shot has
/// *already* happened, which is the cue that makes a corridor feel fought over
/// rather than freshly lit.
const DUST_LIFE: f32 = 0.6;
/// How long a detonation's shell lasts.
const BLAST_LIFE: f32 = 0.5;

/// Beyond this, the oldest effects are dropped rather than queued.
///
/// A cap on what is *drawn*, not on what is received: a shotgun is eight
/// tracers and eight impacts from one trigger pull, and a room of sixteen
/// players firing is a lot of very short-lived geometry.
/// A cap on what is *drawn*, and the reason it is safe to add effects here: the
/// volume buffer holds `MAX_VOLUME_VERTS` (65536) and the most expensive shape
/// in this file is a ball at `8 x 6 x 6 = 288` vertices, so 192 of the worst
/// case is 55k and still fits. Sparks and dust are both cheaper than a ball —
/// sparks are five thin beams and dust is a deliberately coarse sphere — so
/// adding them raises how *fast* the cap is reached without raising the cap's
/// own worst case at all. That arithmetic is the thing to redo before raising
/// this number.
const MAX_LIVE: usize = 192;

/// Hex, like the browser's, so the two can be compared without arithmetic.
const TRACER_COLOR: u32 = 0xffdb8c;
const IMPACT_COLOR: u32 = 0xffd9a0;
/// Hotter than the impact flash, because a spark is burning metal rather than
/// the flash of the strike.
const SPARK_COLOR: u32 = 0xffb347;
/// Pale grey-brown. Deliberately **not** tinted toward the map: surfaces here
/// are shaded by texture id and there is no material to ask, so a dust that
/// tried to match the wall would be wrong on most of them. A neutral puff reads
/// as dust everywhere.
const DUST_COLOR: u32 = 0x9c948a;

/// Beams in one spark burst.
///
/// One `Live` for the whole burst rather than one per spark, and that is the
/// difference between this being affordable and not: eight shotgun pellets each
/// throwing five sparks would be forty entries out of a budget of 192, which
/// would evict the tracers of the shot that threw them.
const SPARKS: usize = 5;

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
    /// A burst of short beams thrown back out of a surface.
    ///
    /// `back` is the unit vector the burst is sprayed about. It is **the
    /// surface's own normal** when the server said which face was hit, and the
    /// reverse of the incoming ray otherwise.
    ///
    /// The fallback used to be the only option: the wire carried where a bullet
    /// stopped and never what stopped it, so spraying back the way the round
    /// came was the one direction that could not be wrong. It is still not
    /// wrong, just blunter — a round arriving at a grazing angle throws its
    /// sparks along the wall rather than off it. `ShotFx.faces` now carries the
    /// face per pellet, so the better answer is available whenever the shooter's
    /// backend is new enough to send it.
    Sparks {
        at: [f32; 3],
        back: [f32; 3],
        seed: u32,
    },
    /// A slow, coarse puff of dust.
    Puff { at: [f32; 3], radius: f32 },
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
    ///
    /// `faces` is which surface each pellet stopped against, parallel to `ends`
    /// — an index into [`trace::FACE_NORMALS`], or [`trace::FACE_NONE`] for a
    /// pellet that found a body or simply ran out of range. It decides two
    /// things per pellet: whether masonry dust is thrown, and which way the
    /// sparks go.
    ///
    /// `hit` is the server's older, per-*shot* account of whether the shot found
    /// a body, and is now only the **fallback** for a shooter whose backend
    /// predates `faces`. It was never good enough: a shotgun with one pellet in
    /// a body and seven in a wall is `hit: true`, so all seven lost their dust.
    /// An empty `faces` means "this shooter's server does not say", never "none
    /// of them hit anything" — which is why the two cases are distinguished
    /// rather than collapsed into `unwrap_or(FACE_NONE)`.
    pub fn shot(
        &mut self,
        origin: [f32; 3],
        ends: &[[f32; 3]],
        faces: &[i32],
        mine: bool,
        hit: bool,
    ) {
        for (i, end) in ends.iter().enumerate() {
            let face = faces.get(i).copied().unwrap_or(crate::trace::FACE_NONE);
            let on_a_surface = if faces.is_empty() {
                !hit
            } else {
                face >= 0
            };
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
            // The surface's own normal where the server named one, and the
            // reverse of the incoming ray where it did not.
            let back = if face >= 0 {
                crate::trace::FACE_NORMALS[face as usize]
            } else {
                normalize([
                    origin[0] - end[0],
                    origin[1] - end[1],
                    origin[2] - end[2],
                ])
            };
            self.push(Live {
                shape: Shape::Sparks {
                    at: *end,
                    back,
                    // The pellet index is in the seed, so the eight impacts of
                    // one shotgun blast are eight different bursts rather than
                    // the same fan drawn eight times a few centimetres apart.
                    seed: 0x9e37_79b9 ^ (i as u32).wrapping_mul(0x85eb_ca6b),
                },
                color: rgb(SPARK_COLOR),
                base: 0.85,
                age: 0.0,
                life: SPARK_LIFE,
            });
            if on_a_surface {
                self.push(Live {
                    shape: Shape::Puff {
                        at: *end,
                        radius: 0.22,
                    },
                    color: rgb(DUST_COLOR),
                    base: 0.3,
                    age: 0.0,
                    life: DUST_LIFE,
                });
            }
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
                    push_ball(out, *at, grown, e.color, alpha, 8, 6);
                }
                Shape::Sparks { at, back, seed } => {
                    // Sparks *decelerate*: the length is `sqrt(t)`, not `t`, so
                    // they leap out of the surface and then stall. Grown
                    // linearly they drift outward at a constant rate, which
                    // reads as an expanding wire model rather than as debris.
                    push_sparks(out, *at, *back, *seed, t.sqrt(), e.color, alpha);
                }
                Shape::Puff { at, radius } => {
                    // Dust expands a long way and thins as it goes — the
                    // opposite budget to everything else here, which is why it
                    // is drawn at less than half a ball's detail. Nothing about
                    // a cloud rewards more triangles.
                    push_ball(out, *at, radius * (0.5 + 2.0 * t), e.color, alpha, 5, 3);
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
/// A capsule-ish beam between two points. `pub(crate)` because the map
/// editor draws its wireframes out of the same primitive — a second beam
/// builder would be two things that have to keep looking alike.
pub(crate) fn push_beam(
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

/// One burst of sparks: short beams in a cone around `back`.
///
/// The directions come from a hash of the seed rather than from any stored
/// state, so `vertices` stays `&self` and a burst looks identical on every frame
/// of its life apart from its length — which is what makes it read as one object
/// moving rather than as a new burst each frame.
fn push_sparks(
    out: &mut Vec<VolumeVertex>,
    at: [f32; 3],
    back: [f32; 3],
    seed: u32,
    reach: f32,
    color: [f32; 3],
    alpha: f32,
) {
    // A basis around the spray axis, chosen the same way `push_beam` chooses
    // one, and for the same reason: a helper parallel to the axis gives a
    // zero-length cross product and collapses every spark to a point.
    let helper = if back[2].abs() < 0.9 {
        [0.0, 0.0, 1.0]
    } else {
        [1.0, 0.0, 0.0]
    };
    let u = normalize(cross(back, helper));
    let v = normalize(cross(back, u));
    let mut rng = seed | 1;
    let mut next = || {
        rng ^= rng << 13;
        rng ^= rng >> 17;
        rng ^= rng << 5;
        (rng >> 8) as f32 / (1u32 << 23) as f32
    };
    for _ in 0..SPARKS {
        let theta = next() * std::f32::consts::TAU;
        // Spread over the cone's *area*, like `trace::spread_direction` — in
        // angle alone every spark clusters on the axis and the burst reads as
        // one thick spike.
        let spread = next().sqrt() * 0.75;
        let len = (0.18 + next() * 0.34) * reach;
        let (st, ct) = theta.sin_cos();
        let dir = normalize([
            back[0] + (u[0] * ct + v[0] * st) * spread,
            back[1] + (u[1] * ct + v[1] * st) * spread,
            back[2] + (u[2] * ct + v[2] * st) * spread,
        ]);
        // Started a little off the surface, so a spark is not half-buried in
        // the wall it came out of.
        let from = [
            at[0] + dir[0] * 0.02,
            at[1] + dir[1] * 0.02,
            at[2] + dir[2] * 0.02,
        ];
        let to = [
            from[0] + dir[0] * len,
            from[1] + dir[1] * len,
            from[2] + dir[2] * len,
        ];
        push_beam(out, from, to, 0.012, color, alpha);
    }
}

/// A low-poly sphere for an impact or a blast shell.
///
/// Built through `nades::push_sphere` and widened, rather than a second sphere
/// generator: two of those is two conventions about winding and about the
/// cube → render mapping, and the second one is always the one that is wrong.
fn push_ball(
    out: &mut Vec<VolumeVertex>,
    at: [f32; 3],
    radius: f32,
    color: [f32; 3],
    alpha: f32,
    segments: usize,
    rings: usize,
) {
    let mut scratch = Vec::new();
    crate::nades::push_sphere(&mut scratch, at, [radius; 3], segments, rings, color);
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
        //
        // Four entries per pellet against the world: tracer, impact flash,
        // sparks, dust. The spark burst is **one** entry for all five of its
        // beams — see `SPARKS`, and the budget arithmetic on `MAX_LIVE`.
        let mut fx = EffectsPool::default();
        fx.shot([0.0; 3], &[[10.0, 0.0, 0.0], [10.0, 2.0, 0.0]], &[], false, false);
        assert_eq!(fx.count(), 8);
    }

    #[test]
    fn a_shot_that_hit_a_body_throws_sparks_but_no_dust() {
        // Masonry dust off a player is the one part of an impact that reads as
        // plainly wrong; sparks read as a strike on anything.
        let mut wall = EffectsPool::default();
        wall.shot([0.0; 3], &[[10.0, 0.0, 0.0]], &[], false, false);
        let mut body = EffectsPool::default();
        body.shot([0.0; 3], &[[10.0, 0.0, 0.0]], &[], false, true);
        assert_eq!(wall.count(), 4);
        assert_eq!(body.count(), 3);
    }

    #[test]
    fn sparks_leap_out_and_then_stall_rather_than_drifting() {
        // `sqrt(t)`, not `t`. Grown linearly a burst reads as an expanding wire
        // model; the deceleration is what makes it debris.
        let reach = |t: f32| {
            let mut fx = EffectsPool::default();
            fx.shot([0.0; 3], &[[10.0, 0.0, 0.0]], &[], false, true);
            fx.update(SPARK_LIFE * t);
            let mut out = Vec::new();
            fx.vertices(&mut out);
            // Sparks spray *back* toward the muzzle, so the near edge of what is
            // drawn around the endpoint is how far they have got.
            10.0 - out
                .iter()
                .filter(|v| v.position[0] > 5.0)
                .map(|v| v.position[0])
                .fold(f32::MAX, f32::min)
        };
        let early = reach(0.25);
        let late = reach(0.95);
        assert!(early > 0.0, "no sparks at all");
        assert!(late > early, "sparks did not travel: {early} then {late}");
        // Half the life should already have covered more than half the distance.
        assert!(
            reach(0.5) > late * 0.55,
            "sparks drifted at a constant rate rather than stalling"
        );
    }

    #[test]
    fn dust_outlives_every_other_part_of_an_impact() {
        // It is the cue that says a shot has *already* happened. A puff that
        // expired with the flash would say nothing the flash had not.
        let mut fx = EffectsPool::default();
        fx.shot([0.0; 3], &[[10.0, 0.0, 0.0]], &[], false, false);
        fx.update(IMPACT_LIFE + SPARK_LIFE);
        assert_eq!(fx.count(), 1, "only the dust should be left");
        fx.update(DUST_LIFE);
        assert_eq!(fx.count(), 0);
    }

    #[test]
    fn a_shot_with_no_endpoints_draws_nothing() {
        // The server sends an empty `ends` for a shot that resolved to nothing.
        let mut fx = EffectsPool::default();
        fx.shot([0.0; 3], &[], &[], false, false);
        assert_eq!(fx.count(), 0);
    }

    #[test]
    fn your_own_tracer_is_drawn_faint() {
        // It leaves the camera, so at full brightness it is a line down the
        // middle of the screen and nothing else.
        let mut mine = EffectsPool::default();
        mine.shot([0.0; 3], &[[10.0, 0.0, 0.0]], &[], true, false);
        let mut theirs = EffectsPool::default();
        theirs.shot([0.0; 3], &[[10.0, 0.0, 0.0]], &[], false, false);
        assert!(tracer_alpha(&mine) < tracer_alpha(&theirs));
    }

    #[test]
    fn effects_fade_to_nothing_and_are_retired() {
        let mut fx = EffectsPool::default();
        fx.shot([0.0; 3], &[[10.0, 0.0, 0.0]], &[], false, false);
        fx.update(TRACER_LIFE + 0.001);
        // The tracer is the shortest-lived thing a shot makes; sparks, the
        // impact flash and the dust all outlive it, in that order.
        assert_eq!(fx.count(), 3);
        fx.update(DUST_LIFE);
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
        fx.shot([0.0; 3], &[[10.0, 0.0, 0.0]], &[], false, false);
        fx.update(TRACER_LIFE * 0.999);
        let max = tracer_alpha(&fx);
        assert!(max < 0.02, "still {max} bright at the end of its life");
    }

    #[test]
    fn the_pool_drops_the_oldest_rather_than_refusing_the_newest() {
        // The shot that just happened is the one worth seeing.
        let mut fx = EffectsPool::default();
        for _ in 0..MAX_LIVE {
            fx.shot([0.0; 3], &[[1.0, 0.0, 0.0]], &[], false, false);
        }
        assert_eq!(fx.count(), MAX_LIVE);
        fx.shot([0.0; 3], &[[99.0, 0.0, 0.0]], &[], false, false);
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
        fx.shot([10.0, 10.0, 0.0], &[[10.0, 10.0, 20.0]], &[], false, false);
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
        fx.shot([0.0, 0.0, 0.0], &[[0.0, 30.0, 0.0]], &[], false, false);
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
        fx.shot([0.0; 3], &[[10.0, 0.0, 0.0]], &[], false, false);
        fx.detonate("he", [0.0; 3], 8.0);
        let mut out = Vec::new();
        fx.vertices(&mut out);
        assert!(!out.is_empty());
        assert!(out.iter().all(|v| v.mode == MODE_FLAT));
    }
}
