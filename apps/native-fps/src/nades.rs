//! Thrown utility: the grenade in the air, and what it leaves behind.
//!
//! **Everything here renders something the server already decided.** The
//! projectile's position, the cloud's centre and radius, and how long either has
//! left all arrive in the snapshot. Nothing in this file simulates, predicts or
//! decides — the same contract the browser's `nades.ts` makes, and for the same
//! reason: a client-side arc would be a second implementation of the bounce
//! whose only job is to occasionally disagree with the first.
//!
//! Positions are interpolated rather than drawn raw. At 20 Hz a grenade
//! travelling 34 cubes a second moves nearly two cubes between packets, and
//! drawn straight it strobes across the room. Interpolation is cheap here
//! *because* nothing is predicted: there is no correction to fight with, so
//! easing toward the newest position is exact within a tick.
//!
//! ## Why a cloud is a sphere and not a particle system
//!
//! A smoke cloud is drawn as **the same sphere the server tests against**
//! (`grenades.sight_blocked_by`). A billboard particle cloud looks better in a
//! screenshot and is a lie in a firefight: its visual edge is nowhere near the
//! volume that actually blocks sight, so players learn a shape that is not the
//! rule. Matching the server's own volume is worth more than a prettier edge.
//!
//! That is also why the cloud has no back-face culling: walking into one has to
//! fill the screen, which means seeing it from the inside.
//!
//! ## Coordinates
//!
//! The wire is cube space — `x`, `y` horizontal, `z` up. The renderer is
//! `[x, z, y]`. The mapping happens **once**, at the point a vertex is emitted,
//! exactly as `bodies.rs` does it; carrying two conventions around in the same
//! module is how a grenade ends up drawn inside the floor.

use crate::protocol::{NadeRow, ZoneRow};
use crate::renderer::{Vertex, VolumeVertex, MODE_CLOUD};

/// How fast a drawn grenade converges on the position the server last sent.
/// The browser's `FOLLOW`.
const FOLLOW: f32 = 18.0;

/// The grenade body, in cubes. Deliberately larger than life: a real one is a
/// few centimetres in a world where a cube is ~36cm, so at true scale it is a
/// pixel — and where it landed is the thing you most need to see.
const BODY_R: f32 = 0.32;
/// Squashed into a canister rather than a ball, so the four kinds read as
/// thrown equipment rather than as a dropped item.
const BODY_SCALE: [f32; 3] = [0.75, 0.75, 1.25];
/// The fuse light, and how far above the body it rides.
const LIGHT_R: f32 = 0.12;
const LIGHT_UP: f32 = 0.34;

const LIGHT_COLOR: u32 = 0xff3b30;

/// A hex colour, as the browser writes it, in the renderer's units.
///
/// Kept as hex rather than as three decimals so a tint here and the tint in
/// `nades.ts` can be compared by eye. Transcribing `0x3f5160` into
/// `[0.247, 0.318, 0.376]` by hand is a step nobody can review — and clippy
/// reads one of those decimals as an approximation of 1/π, which is a fair
/// complaint about a magic number that is not one.
pub fn rgb(hex: u32) -> [f32; 3] {
    [
        ((hex >> 16) & 0xff) as f32 / 255.0,
        ((hex >> 8) & 0xff) as f32 / 255.0,
        (hex & 0xff) as f32 / 255.0,
    ]
}

/// Body colour per kind. The browser's `TINT`.
fn tint(kind: &str) -> [f32; 3] {
    rgb(match kind {
        "flash" => 0xb8b8c0,
        "smoke" => 0x3f5160,
        "fire" => 0x7a3a22,
        // `he` and anything a newer server invents. A grenade of an unknown kind
        // is still a grenade in the air, and refusing to draw it would hide the
        // one cue that tells you to leave the room.
        _ => 0x4d5a3f,
    })
}

/// Cloud colour per kind. The browser's `ZONE_TINT`.
fn zone_tint(kind: &str) -> [f32; 3] {
    rgb(match kind {
        "fire" => 0xff6a2a,
        _ => 0xb9c2cc,
    })
}

/// One grenade being drawn, and where it is being drawn *toward*.
struct LiveNade {
    kind: String,
    /// Where it is drawn, chasing `target`.
    pos: [f32; 3],
    target: [f32; 3],
    fuse: f32,
    /// Tumble, in radians. Purely cosmetic — the server has no idea which way up
    /// a grenade is, and does not need to.
    spin: f32,
}

/// One cloud being drawn, with the opacity curve it is partway through.
pub struct LiveZone {
    pub kind: String,
    pub pos: [f32; 3],
    pub radius: f32,
    pub opacity: f32,
}

/// Every grenade and cloud currently on screen.
///
/// Keyed by the **server's id**, never by array position. That is what makes
/// interpolation possible at all: a grenade that changed index between snapshots
/// would otherwise be drawn flying to another grenade's position — a bug that
/// looks like erratic physics rather than like a bookkeeping error.
#[derive(Default)]
pub struct NadePool {
    nades: Vec<(String, LiveNade)>,
    zones: Vec<(String, LiveZone)>,
    elapsed: f32,
}

impl NadePool {
    /// Reconcile with the snapshot: add what is new, drop what is gone.
    pub fn sync(&mut self, nades: &[NadeRow], zones: &[ZoneRow]) {
        for row in nades {
            let target = [row.x, row.y, row.z];
            match self.nades.iter_mut().find(|(id, _)| id == &row.id) {
                Some((_, live)) => {
                    live.target = target;
                    live.fuse = row.fuse;
                }
                None => self.nades.push((
                    row.id.clone(),
                    LiveNade {
                        kind: row.kind.clone(),
                        // A new grenade starts **at** its position rather than
                        // easing in from wherever the list happened to be: the
                        // alternative is every throw beginning with a streak in
                        // from the last grenade's grave.
                        pos: target,
                        target,
                        fuse: row.fuse,
                        spin: 0.0,
                    },
                )),
            }
        }
        self.nades
            .retain(|(id, _)| nades.iter().any(|r| &r.id == id));

        for row in zones {
            let age = (row.duration - row.left).max(0.0);
            // Clouds bloom in over their first moment and thin at the end rather
            // than appearing and vanishing at full density — the two instants
            // where a hard cut reads as the effect glitching rather than
            // expiring.
            let bloom = (age / 0.65).min(1.0);
            let fade = (row.left / 1.6).min(1.0);
            let opacity = (bloom * fade).clamp(0.0, 1.0);
            match self.zones.iter_mut().find(|(id, _)| id == &row.id) {
                Some((_, live)) => {
                    live.pos = [row.x, row.y, row.z];
                    live.radius = row.r;
                    live.opacity = opacity;
                }
                None => self.zones.push((
                    row.id.clone(),
                    LiveZone {
                        kind: row.kind.clone(),
                        pos: [row.x, row.y, row.z],
                        radius: row.r,
                        opacity,
                    },
                )),
            }
        }
        self.zones
            .retain(|(id, _)| zones.iter().any(|r| &r.id == id));
    }

    /// Advance the drawing. `dt` in seconds.
    pub fn update(&mut self, dt: f32) {
        self.elapsed += dt;
        let follow = (dt * FOLLOW).min(1.0);
        for (_, live) in &mut self.nades {
            for axis in 0..3 {
                live.pos[axis] += (live.target[axis] - live.pos[axis]) * follow;
            }
            live.spin += dt * 7.0;
        }
    }

    pub fn zones(&self) -> impl Iterator<Item = &LiveZone> {
        self.zones.iter().map(|(_, z)| z)
    }

    #[cfg(test)]
    fn nade_position(&self, id: &str) -> Option<[f32; 3]> {
        self.nades
            .iter()
            .find(|(k, _)| k == id)
            .map(|(_, live)| live.pos)
    }

    /// The grenades, as opaque triangles for the body pass.
    ///
    /// Opaque and in the same buffer as the players, which is why this returns
    /// `Vertex` rather than needing a pass of its own: a grenade is a small
    /// solid object and behaves like every other one.
    pub fn vertices(&self, out: &mut Vec<Vertex>) {
        for (_, live) in &self.nades {
            push_sphere(
                out,
                live.pos,
                [
                    BODY_R * BODY_SCALE[0],
                    BODY_R * BODY_SCALE[1],
                    BODY_R * BODY_SCALE[2],
                ],
                8,
                6,
                tint(&live.kind),
            );
            // The fuse light blinks faster as the time runs out, which is the
            // one cue that says whether to run. Cheap, and it works in
            // peripheral vision.
            let rate = if live.fuse > 0.0 {
                2.0 + 10.0 / live.fuse.max(0.25)
            } else {
                24.0
            };
            if (self.elapsed * rate).sin() <= 0.0 {
                continue;
            }
            // Carried around by the tumble rather than pinned to the top: on a
            // body this round the light *is* the rotation, and a light that
            // never moved would make a spinning grenade look like a floating
            // one.
            let (sin, cos) = live.spin.sin_cos();
            let centre = [
                live.pos[0] + LIGHT_UP * sin * 0.5,
                live.pos[1],
                live.pos[2] + LIGHT_UP * cos,
            ];
            push_sphere(out, centre, [LIGHT_R; 3], 6, 5, rgb(LIGHT_COLOR));
        }
    }
}

/// A UV sphere in **cube** coordinates, emitted in render coordinates.
///
/// Per-axis radii rather than one, because the grenade body is a canister and
/// the cloud is a ball, and two primitives for that would be one too many.
///
/// Wound counter-clockwise seen from outside, matching the world mesher — the
/// body pass culls back faces, so a sphere wound the other way is invisible
/// rather than inside-out, which is a much more confusing symptom.
pub fn push_sphere(
    out: &mut Vec<Vertex>,
    centre: [f32; 3],
    radii: [f32; 3],
    segments: usize,
    rings: usize,
    color: [f32; 3],
) {
    let segments = segments.max(3);
    let rings = rings.max(2);
    // Cube (x, y, z-up) → render (x, height, y). Done here, once, so no caller
    // has to hold both conventions in mind.
    let place = |x: f32, y: f32, z: f32| [x, z, y];
    let point = |ring: usize, seg: usize| -> ([f32; 3], [f32; 3]) {
        let phi = std::f32::consts::PI * ring as f32 / rings as f32;
        let theta = std::f32::consts::TAU * seg as f32 / segments as f32;
        let (sp, cp) = phi.sin_cos();
        let (st, ct) = theta.sin_cos();
        let unit = [sp * ct, sp * st, cp];
        let pos = place(
            centre[0] + unit[0] * radii[0],
            centre[1] + unit[1] * radii[1],
            centre[2] + unit[2] * radii[2],
        );
        // The normal of an ellipsoid is the unit vector divided by the squared
        // radii, not the unit vector itself. On a sphere the two agree; on the
        // squashed canister they do not, and using the unit vector lights it as
        // though it were round.
        let raw = [
            unit[0] / (radii[0] * radii[0]).max(1e-6),
            unit[1] / (radii[1] * radii[1]).max(1e-6),
            unit[2] / (radii[2] * radii[2]).max(1e-6),
        ];
        let len = (raw[0] * raw[0] + raw[1] * raw[1] + raw[2] * raw[2])
            .sqrt()
            .max(1e-6);
        (pos, place(raw[0] / len, raw[1] / len, raw[2] / len))
    };

    for ring in 0..rings {
        for seg in 0..segments {
            let next = (seg + 1) % segments;
            let (a, na) = point(ring, seg);
            let (b, nb) = point(ring + 1, seg);
            let (c, nc) = point(ring + 1, next);
            let (d, nd) = point(ring, next);
            for (position, normal) in [(a, na), (b, nb), (c, nc), (a, na), (c, nc), (d, nd)] {
                out.push(Vertex {
                    position,
                    normal,
                    color,
                });
            }
        }
    }
}

/// The clouds, as translucent triangles for the volume pass.
///
/// Emitted at **the radius the server sent**, which is the radius
/// `grenades.sight_blocked_by` tests against. Scaling it to look better would
/// teach players an edge that is not the rule — the single most important thing
/// about drawing a smoke.
pub fn volume_vertices(pool: &NadePool, out: &mut Vec<VolumeVertex>) {
    // Built through the same sphere generator as the grenade body and then
    // widened to carry alpha, rather than a second generator: two sphere
    // builders is two windings, and the volume pass draws both faces so a
    // reversed one would not even look wrong until it lit wrong.
    let mut scratch = Vec::new();
    for zone in pool.zones() {
        if zone.opacity <= 0.0 {
            continue;
        }
        scratch.clear();
        push_sphere(
            &mut scratch,
            zone.pos,
            [zone.radius; 3],
            16,
            12,
            zone_tint(&zone.kind),
        );
        // Fire is thinner than smoke: it is meant to be walked *around*, not
        // hidden in, so it must never become a place to hide.
        let density = if zone.kind == "fire" { 0.45 } else { 0.9 };
        out.extend(scratch.iter().map(|v| VolumeVertex {
            position: v.position,
            normal: v.normal,
            color: [v.color[0], v.color[1], v.color[2], zone.opacity * density],
            mode: MODE_CLOUD,
        }));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn nade(id: &str, x: f32, fuse: f32) -> NadeRow {
        NadeRow {
            id: id.into(),
            kind: "he".into(),
            x,
            y: 0.0,
            z: 0.0,
            fuse,
            ..Default::default()
        }
    }

    fn zone(id: &str, left: f32, duration: f32) -> ZoneRow {
        ZoneRow {
            id: id.into(),
            kind: "smoke".into(),
            r: 6.0,
            left,
            duration,
            ..Default::default()
        }
    }

    #[test]
    fn a_new_grenade_starts_where_it_is_rather_than_flying_in() {
        // Easing in from the default would begin every throw with a streak
        // across the room from wherever the previous grenade died.
        let mut pool = NadePool::default();
        pool.sync(&[nade("g1", 40.0, 3.0)], &[]);
        assert_eq!(pool.nade_position("g1"), Some([40.0, 0.0, 0.0]));
    }

    #[test]
    fn a_moved_grenade_eases_toward_the_new_position() {
        let mut pool = NadePool::default();
        pool.sync(&[nade("g1", 0.0, 3.0)], &[]);
        pool.sync(&[nade("g1", 10.0, 3.0)], &[]);
        pool.update(1.0 / 60.0);
        let x = pool.nade_position("g1").unwrap()[0];
        assert!(x > 0.0 && x < 10.0, "eased to {x}, not snapped or stuck");
    }

    #[test]
    fn interpolation_cannot_overshoot_on_a_long_frame() {
        // `dt * FOLLOW` exceeds 1 past ~55ms, and an unclamped blend would send
        // the grenade past its own target and oscillate — which reads as the
        // physics being wrong rather than the drawing.
        let mut pool = NadePool::default();
        pool.sync(&[nade("g1", 0.0, 3.0)], &[]);
        pool.sync(&[nade("g1", 10.0, 3.0)], &[]);
        pool.update(0.5);
        assert_eq!(pool.nade_position("g1"), Some([10.0, 0.0, 0.0]));
    }

    #[test]
    fn a_grenade_is_tracked_by_id_and_not_by_position_in_the_list() {
        // The bug this prevents looks like erratic physics: two grenades that
        // swap places in the array are drawn flying to each other's positions.
        let mut pool = NadePool::default();
        pool.sync(&[nade("a", 0.0, 3.0), nade("b", 50.0, 3.0)], &[]);
        pool.sync(&[nade("b", 50.0, 2.0), nade("a", 0.0, 2.0)], &[]);
        pool.update(0.1);
        assert_eq!(pool.nade_position("a").unwrap()[0], 0.0);
        assert_eq!(pool.nade_position("b").unwrap()[0], 50.0);
    }

    #[test]
    fn a_grenade_that_is_gone_stops_being_drawn() {
        let mut pool = NadePool::default();
        pool.sync(&[nade("g1", 0.0, 3.0)], &[]);
        pool.sync(&[], &[]);
        assert!(pool.nade_position("g1").is_none());
        let mut out = Vec::new();
        pool.vertices(&mut out);
        assert!(out.is_empty(), "a detonated grenade left geometry behind");
    }

    #[test]
    fn a_cloud_blooms_in_and_thins_out_rather_than_cutting() {
        let mut pool = NadePool::default();
        // Just born: no age, so no opacity yet.
        pool.sync(&[], &[zone("z", 12.0, 12.0)]);
        assert_eq!(pool.zones().next().unwrap().opacity, 0.0);
        // Mid-life: fully dense.
        pool.sync(&[], &[zone("z", 6.0, 12.0)]);
        assert_eq!(pool.zones().next().unwrap().opacity, 1.0);
        // Dying: thinning, not gone.
        pool.sync(&[], &[zone("z", 0.4, 12.0)]);
        let o = pool.zones().next().unwrap().opacity;
        assert!(o > 0.0 && o < 1.0, "opacity {o} should be part way out");
    }

    #[test]
    fn a_cloud_is_the_radius_the_server_sent() {
        // The volume the server tests sight against. Scaling it for looks would
        // teach players an edge that is not the rule.
        let mut pool = NadePool::default();
        pool.sync(&[], &[zone("z", 6.0, 12.0)]);
        assert_eq!(pool.zones().next().unwrap().radius, 6.0);
    }

    #[test]
    fn an_unknown_kind_is_still_drawn() {
        // A grenade of a kind this build has never heard of is still a grenade
        // in the air. `divergence` reports the gap; the renderer does not get to
        // hide the object.
        let mut pool = NadePool::default();
        let mut row = nade("g1", 0.0, 1.0);
        row.kind = "inventedForTest".into();
        pool.sync(&[row], &[]);
        let mut out = Vec::new();
        pool.vertices(&mut out);
        assert!(!out.is_empty());
    }

    #[test]
    fn a_sphere_is_wound_outward_and_sits_where_it_was_asked_to() {
        let mut out = Vec::new();
        // Cube (10, 20, 30) must land at render (10, 30, 20).
        push_sphere(
            &mut out,
            [10.0, 20.0, 30.0],
            [2.0; 3],
            12,
            8,
            [1.0, 0.0, 0.0],
        );
        assert!(!out.is_empty());
        let centre = [10.0f32, 30.0, 20.0];
        for v in &out {
            let d = ((v.position[0] - centre[0]).powi(2)
                + (v.position[1] - centre[1]).powi(2)
                + (v.position[2] - centre[2]).powi(2))
            .sqrt();
            assert!((d - 2.0).abs() < 1e-3, "vertex {d} from the centre, want 2");
            // Outward: the normal agrees with the direction from the centre.
            let dot = (v.position[0] - centre[0]) * v.normal[0]
                + (v.position[1] - centre[1]) * v.normal[1]
                + (v.position[2] - centre[2]) * v.normal[2];
            assert!(dot > 0.0, "an inward normal lights the sphere as a hole");
        }
    }

    #[test]
    fn the_canister_is_taller_than_it_is_wide() {
        // In render space the up axis is y. A canister that came out round would
        // mean the cube→render mapping was applied to the radii but not the
        // centre, or the other way about.
        let mut pool = NadePool::default();
        pool.sync(&[nade("g1", 0.0, 3.0)], &[]);
        let mut out = Vec::new();
        pool.vertices(&mut out);
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
        assert!(
            span(1) > span(0),
            "height {} should exceed width {}",
            span(1),
            span(0)
        );
    }
}
