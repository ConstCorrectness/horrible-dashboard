//! Shot geometry: where a ray stops, and whether it went through a body first.
//!
//! **This is not hit registration.** In a match the server owns that entirely
//! (`backend/modules/hassault/weapons.py`) and nothing here is consulted — the
//! client stamps a shot with its view time and the server rewinds. This exists
//! for the **training range**, which has no server to ask, and for a shot to
//! teach you anything there it has to stop where a real one would.
//!
//! That makes this the *fourth* copy of geometry the backend already has, after
//! `weapons.py`, `trace.ts` and this crate's own `physics.rs`. The repo's answer
//! to that is never care, it is the shared fixture: `physics-vectors.json`
//! carries `traces` and `bodies` alongside its movement cases, and
//! `tests/conformance.rs` replays them here exactly as `trace.test.ts` does in
//! the browser. The fixture pins *agreement*; the unit tests below pin
//! correctness.
//!
//! Free of the renderer, like every other logic file here, so all of it is
//! testable with no GPU.

use crate::api::WeaponSpec;
use crate::world::{World, PLAYER_ABOVE_EYE, PLAYER_EYE_HEIGHT, PLAYER_RADIUS};

/// Total body height — what the collision code reserves and the avatar is drawn to.
pub const BODY_HEIGHT: f32 = PLAYER_EYE_HEIGHT + PLAYER_ABOVE_EYE;

/// The top band of the body that counts as a head.
///
/// A band rather than an absolute height, because crouching shortens the body
/// and a head pinned to a standing figure would sit above a crouched player
/// entirely — every headshot on a crouched target would miss the head and every
/// shot over it would be one.
pub const HEAD_BAND: f32 = 1.0;

pub type Vec3 = [f32; 3];

/// A unit direction from view angles, in cube coordinates. Positive pitch is up.
pub fn aim_vector(yaw: f32, pitch: f32) -> Vec3 {
    let cp = pitch.cos();
    [cp * yaw.cos(), cp * yaw.sin(), pitch.sin()]
}

/// Where a shot leaves from: the eye, which crouching lowers.
///
/// Takes the eye height rather than assuming the standing one, because the
/// muzzle and the camera have to be the same point — aiming from somewhere you
/// are not looking from is a miss you cannot see the cause of.
pub fn eye_position(x: f32, y: f32, z: f32, eye: f32) -> Vec3 {
    [x, y, z + eye]
}

/// Which face of the world a shot stopped against, as an index.
///
/// **The outward normal of the surface hit — the direction pointing back at the
/// shooter.** A ray stepping `+x` that enters a solid has hit that block's `-x`
/// face, so it reports `FACE_NX`. These mirror `weapons.py`'s constants exactly,
/// because the server puts the index on the wire once per pellet; they are
/// re-derived here only for the training range, which has no server to ask.
pub const FACE_PX: i32 = 0;
pub const FACE_NX: i32 = 1;
pub const FACE_PY: i32 = 2;
pub const FACE_NY: i32 = 3;
pub const FACE_PZ: i32 = 4;
pub const FACE_NZ: i32 = 5;
/// No surface: a body, or a shot that reached its range. Negative rather than a
/// sixth value, so a caller that forgets to check cannot index a table with it.
pub const FACE_NONE: i32 = -1;

/// The six faces as unit vectors, in cube coordinates. Mirrors
/// `weapons.FACE_NORMALS` and `decals.ts`'s table; pinned by `browser_parity.rs`.
pub const FACE_NORMALS: [[f32; 3]; 6] = [
    [1.0, 0.0, 0.0],
    [-1.0, 0.0, 0.0],
    [0.0, 1.0, 0.0],
    [0.0, -1.0, 0.0],
    [0.0, 0.0, 1.0],
    [0.0, 0.0, -1.0],
];

/// Distance along `direction` to the first surface, or `max_distance`.
///
/// A grid DDA, because the world *is* a grid: the ray is walked cell by cell,
/// and within each cell only two things stop it — the cell being solid, or the
/// ray leaving the gap between that cell's floor and ceiling.
///
/// The height test uses the cell's flat floor/ceiling, so a heightfield slope is
/// treated as a step. Shots graze slopes they might have clipped by a few
/// hundredths of a cube; the alternative is per-triangle intersection against a
/// mesh this does not have.
pub fn raycast_world(world: &World, origin: Vec3, direction: Vec3, max_distance: f32) -> f32 {
    raycast_world_face(world, origin, direction, max_distance).0
}

/// Distance along `direction` to the first surface, and **which face**.
///
/// The walk itself. `raycast_world` is the wrapper most callers want, exactly as
/// `raycast_world` wraps `raycast_world_face` on the server.
pub fn raycast_world_face(
    world: &World,
    origin: Vec3,
    direction: Vec3,
    max_distance: f32,
) -> (f32, i32) {
    let [ox, oy, oz] = origin;
    let [dx, dy, dz] = direction;
    let mut cx = ox.floor() as i32;
    let mut cy = oy.floor() as i32;

    if world.is_solid(cx, cy) {
        // The muzzle is inside geometry. There is no surface and no direction
        // the ray arrived from, so there is nothing to orient a mark against.
        return (0.0, FACE_NONE);
    }

    let step_x = if dx > 0.0 { 1 } else { -1 };
    let step_y = if dy > 0.0 { 1 } else { -1 };
    let t_delta_x = if dx != 0.0 {
        (1.0 / dx).abs()
    } else {
        f32::INFINITY
    };
    let t_delta_y = if dy != 0.0 {
        (1.0 / dy).abs()
    } else {
        f32::INFINITY
    };
    let mut t_max_x = if dx != 0.0 {
        if dx > 0.0 {
            (cx as f32 + 1.0 - ox) / dx
        } else {
            (cx as f32 - ox) / dx
        }
    } else {
        f32::INFINITY
    };
    let mut t_max_y = if dy != 0.0 {
        if dy > 0.0 {
            (cy as f32 + 1.0 - oy) / dy
        } else {
            (cy as f32 - oy) / dy
        }
    } else {
        f32::INFINITY
    };

    let mut t = 0.0f32;
    // Which face the ray came through to reach the cell being examined. There is
    // none for the cell the muzzle is in, which is why it starts at `FACE_NONE`.
    let mut entered = FACE_NONE;
    // Bounded rather than a `loop`: a direction of (0, 0, ±1) never leaves its
    // cell, and an iteration that only ends on a boundary crossing would never
    // end at all.
    let limit = 4 * world.ssize + 8;
    for _ in 0..limit {
        let t_exit = t_max_x.min(t_max_y).min(max_distance);
        let floor = world.floor_at(cx, cy);
        let ceil = world.ceil_at(cx, cy);
        // The ray is linear in z, so the crossing solves directly rather than
        // being marched for.
        if dz < 0.0 {
            let t_hit = (floor - oz) / dz;
            if t <= t_hit && t_hit <= t_exit {
                // Descending onto a floor: the surface faces up.
                return (t_hit, FACE_PZ);
            }
        } else if dz > 0.0 {
            let t_hit = (ceil - oz) / dz;
            if t <= t_hit && t_hit <= t_exit {
                return (t_hit, FACE_NZ);
            }
        } else if oz < floor || oz > ceil {
            // Dead level, and this cell's gap does not contain the ray — a step,
            // or a low ceiling. There is no floor or ceiling *crossing* to name,
            // but the ray did stop against the side of that step, through the
            // face it came in by.
            return (t, entered);
        }
        if t_exit >= max_distance {
            return (max_distance, FACE_NONE);
        }
        if t_max_x < t_max_y {
            cx += step_x;
            t = t_max_x;
            t_max_x += t_delta_x;
            entered = if step_x > 0 { FACE_NX } else { FACE_PX };
        } else {
            cy += step_y;
            t = t_max_y;
            t_max_y += t_delta_y;
            entered = if step_y > 0 { FACE_NY } else { FACE_PY };
        }
        if world.is_solid(cx, cy) {
            // Stepping in +x means arriving at the block's -x face — the one
            // pointing back at the shooter.
            return (t, entered);
        }
    }
    (max_distance, FACE_NONE)
}

/// Distance at which the ray enters a body's cylinder, or `None`.
///
/// Solved as the intersection of two intervals — inside the infinite cylinder,
/// inside the height slab — so a shot straight up or straight down is not a
/// special case needing its own branch.
pub fn ray_hits_body(origin: Vec3, direction: Vec3, feet: Vec3) -> Option<f32> {
    ray_hits_body_sized(origin, direction, feet, PLAYER_RADIUS, BODY_HEIGHT)
}

pub fn ray_hits_body_sized(
    origin: Vec3,
    direction: Vec3,
    feet: Vec3,
    radius: f32,
    height: f32,
) -> Option<f32> {
    let [ox, oy, oz] = origin;
    let [dx, dy, dz] = direction;
    let [fx, fy, fz] = feet;
    let px = ox - fx;
    let py = oy - fy;

    let a = dx * dx + dy * dy;
    let c = px * px + py * py - radius * radius;
    let mut enter: f32;
    let mut exit: f32;
    if a > 1e-9 {
        let b = 2.0 * (px * dx + py * dy);
        let disc = b * b - 4.0 * a * c;
        if disc < 0.0 {
            return None;
        }
        let root = disc.sqrt();
        enter = (-b - root) / (2.0 * a);
        exit = (-b + root) / (2.0 * a);
    } else if c > 0.0 {
        // Travelling vertically and outside the cylinder: never enters it.
        return None;
    } else {
        enter = f32::NEG_INFINITY;
        exit = f32::INFINITY;
    }

    let z0 = fz;
    let z1 = fz + height;
    if dz.abs() > 1e-9 {
        let mut tz0 = (z0 - oz) / dz;
        let mut tz1 = (z1 - oz) / dz;
        if tz0 > tz1 {
            std::mem::swap(&mut tz0, &mut tz1);
        }
        enter = enter.max(tz0);
        exit = exit.min(tz1);
    } else if oz < z0 || oz > z1 {
        return None;
    }

    if enter > exit || exit < 0.0 {
        return None;
    }
    // A negative entry with a positive exit means the muzzle is already inside
    // them — point blank, which is a hit at zero distance and not a miss.
    Some(enter.max(0.0))
}

/// Where a weapon's damage starts dropping off.
///
/// **Not served.** The wire has never carried it because in a match the server
/// does this arithmetic, and widening the API for a number only the training
/// range reads would be the tail wagging the dog. Approximated from what *is*
/// served — full damage out to a third of the weapon's range — exactly as
/// `training.ts` approximates it, so the two ranges agree with each other even
/// though neither agrees exactly with the server's table.
///
/// The sniper and the knife are flat in the real table and stay flat here,
/// because their falloff begins at their range.
pub fn falloff_start(weapon: &WeaponSpec) -> f32 {
    if weapon.id == "sniper" || weapon.id == "knife" {
        weapon.range
    } else {
        weapon.range / 3.0
    }
}

/// Damage after falloff: full out to `falloff_start`, tapering to half at range.
pub fn damage_at(weapon: &WeaponSpec, distance: f32, falloff_start: f32) -> f32 {
    if distance <= falloff_start || weapon.range <= falloff_start {
        return weapon.damage;
    }
    let span = weapon.range - falloff_start;
    let t = ((distance - falloff_start) / span).min(1.0);
    weapon.damage * (1.0 - 0.5 * t)
}

/// Recoil push while crouched, mirroring `CROUCH_KICK_SCALE` in `weapons.py`.
///
/// A braced shot moves you less, which makes crouching the accurate option *and*
/// the stable one — two incentives pointing the same way rather than a dial to
/// balance.
pub const CROUCH_KICK_SCALE: f32 = 0.75;

/// The impulse a shot applies to the **shooter**, in cubes per second.
///
/// Opposite the aim, which is the entire mechanic: aim at the floor and the push
/// is upward, so a jump plus a well-timed shotgun blast reaches ledges a jump
/// cannot. In a match the *server* applies this and the client feels it through
/// reconciliation; on the range there is no server, so the range applies it
/// itself — otherwise Train is the one mode where the shoot-jump, the thing it
/// exists to teach, does not work.
///
/// Computed from the **served** `kickback` rather than a local table, so this
/// client cannot disagree with the server about how far it just got shoved.
pub fn kick_vector(weapon: &WeaponSpec, yaw: f32, pitch: f32, crouching: bool) -> Vec3 {
    if weapon.kickback <= 0.0 {
        return [0.0, 0.0, 0.0];
    }
    let push = weapon.kickback * if crouching { CROUCH_KICK_SCALE } else { 1.0 };
    let cp = pitch.cos();
    [
        -cp * yaw.cos() * push,
        -cp * yaw.sin() * push,
        -pitch.sin() * push,
    ]
}

/// Perturb an aim direction inside a cone of half-angle `spread`.
///
/// Sampled uniformly over the cone's *area* (hence the square root) rather than
/// uniformly in angle, which would cluster every pellet near the centre and make
/// a shotgun behave like a rifle at range.
///
/// `rand` is injected rather than called globally so a test can pin the cone
/// with a known sequence; the server's equivalent takes a seeded `random.Random`
/// for the same reason.
pub fn spread_vector(direction: Vec3, spread: f32, rand: &mut dyn FnMut() -> f32) -> Vec3 {
    if spread <= 0.0 {
        return direction;
    }
    let [dx, dy, dz] = direction;
    // Any vector not parallel to the aim gives a usable first basis vector.
    let [ax, ay, az] = if dz.abs() < 0.9 {
        [0.0, 0.0, 1.0]
    } else {
        [1.0, 0.0, 0.0]
    };
    let mut ux = dy * az - dz * ay;
    let mut uy = dz * ax - dx * az;
    let mut uz = dx * ay - dy * ax;
    let ul = (ux * ux + uy * uy + uz * uz).sqrt();
    let ul = if ul == 0.0 { 1.0 } else { ul };
    ux /= ul;
    uy /= ul;
    uz /= ul;
    let vx = dy * uz - dz * uy;
    let vy = dz * ux - dx * uz;
    let vz = dx * uy - dy * ux;

    let angle = spread * rand().max(0.0).sqrt();
    let phi = rand() * std::f32::consts::TAU;
    let (sa, ca) = (angle.sin(), angle.cos());
    let (cphi, sphi) = (phi.cos(), phi.sin());
    let ox = ca * dx + sa * (cphi * ux + sphi * vx);
    let oy = ca * dy + sa * (cphi * uy + sphi * vy);
    let oz = ca * dz + sa * (cphi * uz + sphi * vz);
    let length = (ox * ox + oy * oy + oz * oz).sqrt();
    let length = if length == 0.0 { 1.0 } else { length };
    [ox / length, oy / length, oz / length]
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::{Entity, MapInfo};

    /// A room with a floor at 0, a ceiling at 16, and solid rock outside it.
    fn room(ssize: i32, open: i32) -> World {
        let n = (ssize * ssize) as usize;
        let mut bytes = Vec::with_capacity(n * 9);
        let mut types = Vec::with_capacity(n);
        for y in 0..ssize {
            for x in 0..ssize {
                let inside = x > 0 && y > 0 && x < open && y < open;
                types.push(if inside { 2u8 } else { 0u8 });
            }
        }
        bytes.extend(types);
        bytes.extend(std::iter::repeat_n(0u8, n)); // floor
        bytes.extend(std::iter::repeat_n(16u8, n)); // ceil
        bytes.extend(std::iter::repeat_n(0u8, n * 6));
        let info = MapInfo {
            ssize,
            cubic_size: n,
            plane_order: [
                "type", "floor", "ceil", "wtex", "ftex", "ctex", "vdelta", "utex", "tag",
            ]
            .iter()
            .map(|s| s.to_string())
            .collect(),
            entities: Vec::<Entity>::new(),
            ..Default::default()
        };
        World::new(info, &bytes).expect("world")
    }

    fn rifle() -> WeaponSpec {
        WeaponSpec {
            id: "assault".into(),
            damage: 60.0,
            range: 90.0,
            ..Default::default()
        }
    }

    #[test]
    fn a_level_shot_stops_on_the_wall() {
        let world = room(32, 12);
        let hit = raycast_world(&world, [3.0, 6.0, 4.5], aim_vector(0.0, 0.0), 100.0);
        // The first solid column is at x = 12, and the ray starts at 3.
        assert!((hit - 9.0).abs() < 1e-3, "stopped at {hit}");
    }

    #[test]
    fn a_shot_at_the_floor_stops_on_it() {
        let world = room(32, 12);
        // Straight down from the eye: 4.5 cubes to the floor.
        let hit = raycast_world(
            &world,
            [4.0, 4.0, 4.5],
            aim_vector(0.0, -std::f32::consts::FRAC_PI_2),
            100.0,
        );
        assert!((hit - 4.5).abs() < 1e-3, "stopped at {hit}");
    }

    #[test]
    fn a_vertical_shot_terminates() {
        // The bug this pins: a direction with no horizontal component never
        // crosses a cell boundary, so a DDA that only exits on one runs forever.
        let world = room(32, 12);
        let up = raycast_world(&world, [4.0, 4.0, 4.5], [0.0, 0.0, 1.0], 100.0);
        assert!((up - 11.5).abs() < 1e-3, "stopped at {up}");
    }

    #[test]
    fn a_body_behind_a_wall_is_still_a_hit_here_and_the_caller_compares() {
        // `ray_hits_body` knows nothing about walls on purpose: the range asks
        // both and takes the nearer. Splitting it the other way — teaching the
        // body test about geometry — is how cover stops working when a body is
        // in the same cell as the wall.
        let origin = [4.0, 4.0, 4.5];
        let dir = aim_vector(0.0, 0.0);
        let hit = ray_hits_body(origin, dir, [20.0, 4.0, 0.0]).expect("hit");
        assert!(
            (hit - (16.0 - PLAYER_RADIUS)).abs() < 1e-3,
            "entered at {hit}"
        );
    }

    #[test]
    fn point_blank_is_a_hit_at_zero_not_a_miss() {
        // A negative entry distance with a positive exit means the muzzle is
        // already inside them. Reading that as a miss is the classic bug: the
        // shotgun does nothing at exactly the range it should do the most.
        let origin = [8.0, 8.0, 4.5];
        let hit = ray_hits_body(origin, aim_vector(0.0, 0.0), [8.0, 8.0, 0.0]);
        assert_eq!(hit, Some(0.0));
    }

    #[test]
    fn a_shot_over_a_body_misses_it() {
        let origin = [4.0, 4.0, 4.5];
        // Straight up, past a body standing beside us.
        assert!(ray_hits_body(origin, [0.0, 0.0, 1.0], [20.0, 4.0, 0.0]).is_none());
    }

    #[test]
    fn damage_is_flat_inside_the_falloff_and_halves_at_range() {
        let w = rifle();
        let start = falloff_start(&w);
        assert_eq!(damage_at(&w, 0.0, start), 60.0);
        assert_eq!(damage_at(&w, start, start), 60.0);
        assert!((damage_at(&w, w.range, start) - 30.0).abs() < 1e-4);
        // And never below half, however far past the range it is asked.
        assert!((damage_at(&w, w.range * 4.0, start) - 30.0).abs() < 1e-4);
    }

    #[test]
    fn a_sniper_does_not_fall_off_at_all() {
        let w = WeaponSpec {
            id: "sniper".into(),
            damage: 90.0,
            range: 200.0,
            ..Default::default()
        };
        let start = falloff_start(&w);
        assert_eq!(damage_at(&w, 199.0, start), 90.0);
    }

    #[test]
    fn the_kick_pushes_opposite_the_aim() {
        // The shoot-jump in one assertion: aiming *down* pushes you *up*.
        let w = WeaponSpec {
            kickback: 10.0,
            ..rifle()
        };
        let down = kick_vector(&w, 0.0, -std::f32::consts::FRAC_PI_2, false);
        assert!(
            down[2] > 9.9,
            "aiming at the floor did not push up: {down:?}"
        );
        // And a level shot pushes straight backwards, not up at all.
        let level = kick_vector(&w, 0.0, 0.0, false);
        assert!((level[0] + 10.0).abs() < 1e-4 && level[2].abs() < 1e-6);
        // Crouching braces it.
        let braced = kick_vector(&w, 0.0, 0.0, true);
        assert!((braced[0] + 7.5).abs() < 1e-4);
    }

    #[test]
    fn a_weapon_with_no_kickback_pushes_nothing() {
        // The knife. A zero here must be a zero vector and not a NaN from
        // normalising nothing.
        let w = WeaponSpec {
            kickback: 0.0,
            ..rifle()
        };
        assert_eq!(kick_vector(&w, 1.0, 0.5, false), [0.0, 0.0, 0.0]);
    }

    #[test]
    fn spread_stays_inside_its_cone_and_stays_a_unit_vector() {
        let dir = aim_vector(0.4, 0.2);
        let cone = 0.05f32;
        let mut seq = [0.99f32, 0.3, 0.7, 0.8, 1.0, 0.1].into_iter().cycle();
        let mut rand = move || seq.next().unwrap();
        for _ in 0..3 {
            let out = spread_vector(dir, cone, &mut rand);
            let len = (out[0] * out[0] + out[1] * out[1] + out[2] * out[2]).sqrt();
            assert!((len - 1.0).abs() < 1e-5, "length {len}");
            let dot = (out[0] * dir[0] + out[1] * dir[1] + out[2] * dir[2]).clamp(-1.0, 1.0);
            assert!(dot.acos() <= cone + 1e-5, "outside the cone");
        }
    }

    #[test]
    fn no_spread_is_the_aim_itself() {
        // Not "a cone of zero width", which would still burn two random numbers
        // and, with a shared generator, shift every later pellet's sample.
        let dir = aim_vector(1.0, -0.2);
        let mut rand = || panic!("a zero cone must not sample");
        assert_eq!(spread_vector(dir, 0.0, &mut rand), dir);
    }
}
