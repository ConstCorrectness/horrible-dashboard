//! Where a grenade would land, drawn before you throw it.
//!
//! The browser's `arc.ts`, in this client's terms, and pinned against it and
//! against the server by the `throws` block of `physics-vectors.json`.
//!
//! ## Why this exists at all
//!
//! The server has *always* added the thrower's own velocity to a throw
//! (`grenades.THROW_INHERIT`, 0.6 of it) — running forward sends a grenade
//! further, jumping sends it higher. Nothing on screen said so, so the feature
//! was real and invisible: the only way to learn it was to notice that grenades
//! sometimes went further than expected and guess why.
//!
//! ## Only to first contact, deliberately
//!
//! `step_grenade` bounces, and bouncing is chaotic: a difference of 1e-6 in the
//! floor comparison flips a bounce and puts the marker in the next room. A
//! *learnable* feature that is confidently wrong is worse than one that stops
//! early — so the preview ends where the grenade first touches something, which
//! is both stable and what a player actually aims with.
//!
//! ## Everything here is served
//!
//! The constants come from `GET /api/hassault/throw`. Tabulating them here would
//! be a preview that quietly disagreed with the throw it is previewing, which is
//! the one thing an aiming aid must not do. No `ThrowPhysics` draws nothing at
//! all rather than integrating with zeros — a straight line into the floor.

use crate::api::ThrowPhysics;
use crate::world::World;

/// How far ahead the preview looks, in seconds. Matches `ARC_PREVIEW_SECONDS`.
pub const ARC_PREVIEW_SECONDS: f32 = 2.0;

/// How many points the drawn line has. Matches `ARC_SAMPLES`.
pub const ARC_SAMPLES: usize = 48;

#[derive(Debug, Clone, Default, PartialEq)]
pub struct ThrowArc {
    /// The flight path, in cube coordinates, ending at first contact.
    pub points: Vec<[f32; 3]>,
    /// Where it first touched something, or `None` if it was still in the air
    /// when the preview ran out.
    ///
    /// `None` is a real answer and not a failure: a grenade thrown across a
    /// canyon is genuinely still falling two seconds later, and a marker at the
    /// end of the preview window would claim it landed there.
    pub contact: Option<[f32; 3]>,
    /// Whether that contact was the ground rather than a wall or a ceiling.
    pub landed: bool,
}

/// Whether a point is inside the level's geometry.
///
/// The same three questions `grenades._blocked` asks of a cell, which is
/// deliberate: a preview that stopped on different surfaces than the grenade
/// does would be an aiming aid pointing at somewhere the grenade will not be.
fn blocked(world: &World, x: f32, y: f32, z: f32) -> bool {
    let cx = x.floor() as i32;
    let cy = y.floor() as i32;
    if world.is_solid(cx, cy) {
        return true;
    }
    z < world.floor_at(cx, cy) || z > world.ceil_at(cx, cy)
}

/// Where a grenade appears when it leaves the hand. `grenades.throw_origin`.
///
/// In front of and below the eye rather than at it: a grenade released exactly
/// at the eye clips the thrower's own body on the first substep when they are
/// backed against a wall.
pub fn throw_origin(
    x: f32,
    y: f32,
    eye_z: f32,
    yaw: f32,
    pitch: f32,
    physics: &ThrowPhysics,
) -> [f32; 3] {
    let cp = pitch.cos();
    [
        x + yaw.cos() * cp * physics.throw_forward,
        y + yaw.sin() * cp * physics.throw_forward,
        eye_z - physics.throw_drop + pitch.sin() * physics.throw_forward,
    ]
}

/// The velocity a grenade leaves the hand with. `grenades.throw_velocity`.
///
/// The thrower's own velocity is added at `throw_inherit` rather than in full:
/// at 1.0 a player running backwards can drop a grenade that never leaves them,
/// which reads as the throw having failed.
pub fn throw_velocity(
    yaw: f32,
    pitch: f32,
    lob: bool,
    inherit: [f32; 3],
    physics: &ThrowPhysics,
) -> [f32; 3] {
    let speed = physics.throw_speed * if lob { physics.lob_scale } else { 1.0 };
    let cp = pitch.cos();
    [
        yaw.cos() * cp * speed + inherit[0] * physics.throw_inherit,
        yaw.sin() * cp * speed + inherit[1] * physics.throw_inherit,
        pitch.sin() * speed + inherit[2] * physics.throw_inherit,
    ]
}

/// Integrate a throw forward until it touches something.
///
/// Substepped and **axis-separated** like `step_grenade`, and for its reason:
/// resolving a diagonal contact as one event has to pick an axis anyway, and
/// picking the wrong one reports a contact on a surface the grenade would have
/// slid along.
pub fn simulate_throw(
    world: &World,
    origin: [f32; 3],
    velocity: [f32; 3],
    physics: &ThrowPhysics,
    seconds: f32,
) -> ThrowArc {
    let mut points = Vec::with_capacity(ARC_SAMPLES + 2);
    points.push(origin);
    let [mut x, mut y, mut z] = origin;
    // Only the vertical velocity changes: this preview stops at first contact,
    // so there is no bounce to reflect the horizontal ones.
    let [vx, vy] = [velocity[0], velocity[1]];
    let mut vz = velocity[2];
    let substeps = ((seconds / physics.substep).ceil() as usize).max(1);
    // One sample every so many substeps, so the drawn line has `ARC_SAMPLES`
    // points however long the preview window is.
    let every = (substeps / ARC_SAMPLES).max(1);

    for step in 0..substeps {
        let h = physics.substep;
        vz -= physics.gravity * h;

        let mut contact: Option<[f32; 3]> = None;
        let mut landed = false;
        // x, then y, then z — the order `step_grenade` resolves them in.
        let nx = x + vx * h;
        if blocked(world, nx, y, z) {
            contact = Some([x, y, z]);
        } else {
            x = nx;
        }
        if contact.is_none() {
            let ny = y + vy * h;
            if blocked(world, x, ny, z) {
                contact = Some([x, y, z]);
            } else {
                y = ny;
            }
        }
        if contact.is_none() {
            let nz = z + vz * h;
            if blocked(world, x, y, nz) {
                contact = Some([x, y, z]);
                // Falling when it stopped: the thing it met was the ground. Only
                // then is a landing marker honest — a grenade that clipped a
                // wall carries on somewhere this preview does not follow.
                landed = vz < 0.0;
            } else {
                z = nz;
            }
        }

        if let Some(at) = contact {
            points.push(at);
            return ThrowArc {
                points,
                contact: Some(at),
                landed,
            };
        }
        if step % every == 0 {
            points.push([x, y, z]);
        }
    }

    points.push([x, y, z]);
    // Still in the air. Not a failure — see `ThrowArc::contact`.
    ThrowArc {
        points,
        contact: None,
        landed: false,
    }
}

/// The line's colour. Warm, so it reads against grey geometry and blue water.
pub const ARC_COLOR: [f32; 3] = [1.0, 0.812, 0.478];
/// The landing marker's, brighter — it is the answer rather than the workings.
pub const MARK_COLOR: [f32; 3] = [1.0, 0.941, 0.784];
/// The marker's radius, in cube units. About a body's width.
pub const MARK_RADIUS: f32 = 0.55;
/// How thick the drawn line is.
pub const ARC_THICKNESS: f32 = 0.045;

/// The arc as geometry for the translucent volume pass.
///
/// That pass rather than a line pipeline of its own, for the reasons
/// `decals.rs` gives: alpha-blended, depth-*tested* so a throw around a corner
/// is hidden by the corner (an arc drawn through walls would be a wall hack
/// rather than an aiming aid), and no depth *write* so it does not fight the
/// floor it grazes.
///
/// A camera-agnostic **cross** of two quads per segment rather than a screen-
/// facing ribbon: this module knows nothing about the camera by design, and a
/// ribbon built against a stale view direction is a line that vanishes when you
/// look along it. Two perpendicular strips are always visible from somewhere.
pub fn arc_vertices(arc: &ThrowArc, push: &mut impl FnMut([f32; 3], [f32; 3], f32)) {
    let r = ARC_THICKNESS;
    for pair in arc.points.windows(2) {
        let (a, b) = (pair[0], pair[1]);
        let d = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
        let len = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt();
        if len < 1e-5 {
            continue;
        }
        let dir = [d[0] / len, d[1] / len, d[2] / len];
        // Any vector not parallel to the segment gives a usable first basis
        // vector — the same trick `spread_vector` uses to build a cone's basis.
        let up = if dir[2].abs() < 0.9 {
            [0.0, 0.0, 1.0]
        } else {
            [1.0, 0.0, 0.0]
        };
        let u = normalize(cross(dir, up));
        let v = normalize(cross(dir, u));
        for axis in [u, v] {
            let quad = [
                offset(a, axis, -r),
                offset(a, axis, r),
                offset(b, axis, r),
                offset(b, axis, -r),
            ];
            for i in [0, 1, 2, 0, 2, 3] {
                push(quad[i], ARC_COLOR, 0.75);
            }
        }
    }

    // The marker is only drawn for a contact that was actually the **ground**. A
    // grenade that clipped a wall carries on somewhere this preview does not
    // follow, and a ring on the wall would claim otherwise.
    if let (Some(at), true) = (arc.contact, arc.landed) {
        let segments = 24;
        let inner = MARK_RADIUS * 0.72;
        // Lifted a little, or it z-fights the floor it lies on.
        let z = at[2] + 0.02;
        for i in 0..segments {
            let a0 = (i as f32 / segments as f32) * std::f32::consts::TAU;
            let a1 = ((i + 1) as f32 / segments as f32) * std::f32::consts::TAU;
            let ring = |angle: f32, radius: f32| {
                [
                    at[0] + angle.cos() * radius,
                    at[1] + angle.sin() * radius,
                    z,
                ]
            };
            let quad = [
                ring(a0, inner),
                ring(a0, MARK_RADIUS),
                ring(a1, MARK_RADIUS),
                ring(a1, inner),
            ];
            for i in [0, 1, 2, 0, 2, 3] {
                push(quad[i], MARK_COLOR, 0.55);
            }
        }
    }
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
        [1.0, 0.0, 0.0]
    } else {
        [v[0] / len, v[1] / len, v[2] / len]
    }
}

fn offset(p: [f32; 3], axis: [f32; 3], by: f32) -> [f32; 3] {
    [p[0] + axis[0] * by, p[1] + axis[1] * by, p[2] + axis[2] * by]
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::world::World;

    fn physics() -> ThrowPhysics {
        // The server's own numbers. A test that invented its own would be
        // testing arithmetic rather than agreement.
        ThrowPhysics {
            gravity: 55.0,
            throw_speed: 34.0,
            lob_scale: 0.42,
            throw_inherit: 0.6,
            throw_forward: 1.3,
            throw_drop: 0.35,
            rest_speed: 1.2,
            substep: 1.0 / 120.0,
            max_substeps: 64,
        }
    }

    /// An open room with a solid border, big enough that a flat throw arcs and
    /// falls rather than clipping a wall six cubes away. Built the way
    /// `mapsource` would, mirroring `editor.rs`'s own `flat_world`.
    fn field() -> World {
        let ssize = 64i32;
        let n = (ssize * ssize) as usize;
        let mut planes = vec![0u8; n * 9];
        let (type_p, floor_p, ceil_p) = (0, n, 2 * n);
        for y in 0..ssize {
            for x in 0..ssize {
                let index = (y * ssize + x) as usize;
                let open = x > 1 && y > 1 && x < ssize - 2 && y < ssize - 2;
                planes[type_p + index] = if open { 4 } else { 0 };
                planes[floor_p + index] = 0;
                planes[ceil_p + index] = 16;
            }
        }
        let info = crate::api::MapInfo {
            ssize,
            cubic_size: n,
            plane_order: vec![
                "type", "floor", "ceil", "wtex", "ftex", "ctex", "vdelta", "utex", "tag",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
            ..crate::api::MapInfo::default()
        };
        World::new(info, &planes).expect("test world")
    }

    #[test]
    fn running_at_the_throw_sends_it_further() {
        // `THROW_INHERIT` — the thing this whole preview exists to make visible.
        let p = physics();
        let w = field();
        let origin = throw_origin(8.0, 32.0, 4.5, 0.0, 0.0, &p);
        let still = simulate_throw(
            &w,
            origin,
            throw_velocity(0.0, 0.0, false, [0.0; 3], &p),
            &p,
            ARC_PREVIEW_SECONDS,
        );
        let running = simulate_throw(
            &w,
            origin,
            throw_velocity(0.0, 0.0, false, [20.0, 0.0, 0.0], &p),
            &p,
            ARC_PREVIEW_SECONDS,
        );
        assert!(running.contact.unwrap()[0] > still.contact.unwrap()[0]);
    }

    #[test]
    fn an_underhand_lob_lands_much_shorter() {
        let p = physics();
        let w = field();
        let origin = throw_origin(8.0, 32.0, 4.5, 0.0, 0.0, &p);
        let full = simulate_throw(
            &w,
            origin,
            throw_velocity(0.0, 0.0, false, [0.0; 3], &p),
            &p,
            ARC_PREVIEW_SECONDS,
        );
        let lob = simulate_throw(
            &w,
            origin,
            throw_velocity(0.0, 0.0, true, [0.0; 3], &p),
            &p,
            ARC_PREVIEW_SECONDS,
        );
        assert!(lob.contact.unwrap()[0] < full.contact.unwrap()[0]);
    }

    #[test]
    fn the_preview_never_draws_more_points_than_it_promises() {
        let p = physics();
        let w = field();
        let arc = simulate_throw(
            &w,
            throw_origin(8.0, 32.0, 4.5, 0.0, 0.6, &p),
            throw_velocity(0.0, 0.6, false, [0.0; 3], &p),
            &p,
            ARC_PREVIEW_SECONDS,
        );
        assert!(arc.points.len() <= ARC_SAMPLES + 2);
        assert!(arc.points.len() >= 2);
    }

    #[test]
    fn a_wall_contact_is_not_a_landing() {
        // A grenade that clipped a wall carries on somewhere this preview does
        // not follow, so a ring on the floor would be claiming something false.
        let p = physics();
        let w = field();
        // Flat into the border, from close enough that it cannot fall first.
        let arc = simulate_throw(
            &w,
            throw_origin(58.0, 32.0, 4.5, 0.0, 0.0, &p),
            throw_velocity(0.0, 0.0, false, [0.0; 3], &p),
            &p,
            ARC_PREVIEW_SECONDS,
        );
        assert!(arc.contact.is_some());
        assert!(!arc.landed, "a wall was reported as a landing");
    }

    #[test]
    fn a_landing_gets_a_marker_and_a_wall_does_not() {
        let p = physics();
        let w = field();
        let landed = simulate_throw(
            &w,
            throw_origin(8.0, 32.0, 4.5, 0.0, 0.0, &p),
            throw_velocity(0.0, 0.0, false, [0.0; 3], &p),
            &p,
            ARC_PREVIEW_SECONDS,
        );
        assert!(landed.landed);
        let mut with_mark = 0usize;
        arc_vertices(&landed, &mut |_, _, _| with_mark += 1);

        let mut wall = landed.clone();
        wall.landed = false;
        let mut without_mark = 0usize;
        arc_vertices(&wall, &mut |_, _, _| without_mark += 1);
        assert!(with_mark > without_mark);
    }
}
