//! First-person movement and collision against the cube grid.
//!
//! **The third implementation of one set of rules.** `player.ts` is the browser
//! client's, `backend/modules/hassault/physics.py` is the server's (an
//! authoritative server has to simulate), and this is the native client's. That
//! is not a design anyone would choose; it is what client-side prediction costs
//! when the client and server are different languages.
//!
//! The repo already has the answer to the danger, and this file is bound by it:
//! `packages/core/src/modules/hassault/__tests__/physics-vectors.json` is a
//! fixture that **all three** replay, and `tests/conformance.rs` is this side's
//! seat at that table. The fixture pins *agreement*; each side's own unit tests
//! pin correctness. A drifted implementation does not throw — it just puts each
//! player somewhere slightly different from where everyone else thinks they are,
//! and the symptom is shots that miss things you are looking at.
//!
//! Axes follow `world.rs`: `x`/`y` are grid coordinates and `z` is height.
//!
//! ### The movement model
//!
//! Movement carries **momentum**: velocity is integrated against the grid rather
//! than the position being stepped by a direction. Three mechanics have nowhere
//! to live otherwise — weapon recoil pushing the shooter (AssaultCube's
//! shoot-jump) is an impulse, the chained-jump boost multiplies a speed that has
//! to already exist, and the difference between ground control and air momentum
//! *is* the difference between two friction constants.
//!
//! Constants come from AC's `physics.cpp`/`entity.h`, converted out of its
//! per-millisecond units into cubes and seconds. `physics.py` carries the full
//! derivation and the two deliberate deviations from AC (an exponential rather
//! than linear blend, so the rules are frame-rate independent; and a chain-boost
//! window measured from landing rather than from the previous jump).

use crate::world::{World, PLAYER_ABOVE_EYE, PLAYER_EYE_HEIGHT, PLAYER_RADIUS};

/// Cubes per second at a walk. Tuned to feel like AC rather than derived from it.
pub const MOVE_SPEED: f32 = 22.0;
pub const GRAVITY: f32 = 55.0;
pub const JUMP_SPEED: f32 = 19.0;

/// How high a step the player walks up without jumping.
///
/// Without this, every heightfield cell is a wall: sloped terrain is stored as a
/// series of small floor changes, and a collision test that rejects any rise at
/// all leaves the player stuck on flat-looking ground.
pub const STEP_HEIGHT: f32 = 1.6;

/// Total body height standing — what headroom is reserved for and what a shot hits.
pub const STANDING_HEIGHT: f32 = PLAYER_EYE_HEIGHT + PLAYER_ABOVE_EYE;

/// Crouching: the eye drops to 3/4 (AC's `updatecrouch`), `aboveeye` does not, so
/// the body is ~1.1 cubes shorter and fits under gaps a standing one cannot.
pub const CROUCH_EYE_SCALE: f32 = 0.75;
pub const CROUCH_EYE_HEIGHT: f32 = PLAYER_EYE_HEIGHT * CROUCH_EYE_SCALE;
pub const CROUCH_HEIGHT: f32 = CROUCH_EYE_HEIGHT + PLAYER_ABOVE_EYE;
/// AC's `chspeed`. The cost that makes moving silently a trade, not an upgrade.
pub const CROUCH_SPEED_SCALE: f32 = 0.4;
/// Seconds for a full stand↔crouch transition.
pub const CROUCH_TRANSITION: f32 = 0.15;

/// Velocity convergence per second: AC's friction 6 on the floor, 30 in the air,
/// expressed as `50/friction`. Ground settles in ~0.12 s, air in ~0.6 s.
pub const GROUND_RESPONSE: f32 = 50.0 / 6.0;
pub const AIR_RESPONSE: f32 = 50.0 / 30.0;

/// Gravity ramps with time in air (AC's `dropf`), capped so a long drop is a fall
/// rather than a teleport.
pub const GRAVITY_RAMP: f32 = 1.0;
pub const MAX_GRAVITY_SCALE: f32 = 2.5;

/// The chained-jump boost: jump again within the window while strafing for 25%
/// more speed, capped at 125% of run speed. Both numbers are AC's.
pub const JUMP_CHAIN_WINDOW: f32 = 0.25;
pub const JUMP_CHAIN_BOOST: f32 = 1.25;

/// Landing harder than this costs health. A flat jump lands at `JUMP_SPEED`, so
/// ordinary movement is free and a recoil-launched climb is not.
pub const FALL_SAFE_SPEED: f32 = 34.0;
pub const FALL_DAMAGE_PER_SPEED: f32 = 3.0;

/// The largest timestep ever integrated in one go. A stalled client returns a
/// huge `dt`, and integrating it whole teleports the player through walls.
pub const MAX_STEP: f32 = 0.1;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PlayerState {
    pub x: f32,
    pub y: f32,
    /// Height of the player's **feet**, not the eye.
    pub z: f32,
    pub vel_x: f32,
    pub vel_y: f32,
    pub vel_z: f32,
    /// Radians, 0 = +x.
    pub yaw: f32,
    /// Radians, clamped to just under ±90°.
    pub pitch: f32,
    pub on_ground: bool,
    /// Crouch animation, 0 standing to 1 fully crouched.
    pub crouch: f32,
    /// What the last input asked for, so the *transition* into a crouch is
    /// detectable — which is what `crouched_in_air` keys off.
    pub crouch_held: bool,
    /// Crouch began airborne: AC leaves such a player at full speed, so a
    /// crouch-jump clears a gap without paying the crouch penalty.
    pub crouched_in_air: bool,
    pub time_in_air: f32,
    /// Simulated seconds advanced. A clock local to the simulation, so the
    /// jump-chain window means the same on both sides without a wall clock.
    pub t: f32,
    /// `t` of the last landing — where the chain-boost window is measured from.
    pub landed_at: f32,
    /// Impact speed of a landing that happened *this step*, else 0. An output:
    /// the server turns it into damage and the client only flinches.
    pub fall_speed: f32,
}

impl Default for PlayerState {
    fn default() -> PlayerState {
        PlayerState {
            x: 0.0,
            y: 0.0,
            z: 0.0,
            vel_x: 0.0,
            vel_y: 0.0,
            vel_z: 0.0,
            yaw: 0.0,
            pitch: 0.0,
            on_ground: false,
            crouch: 0.0,
            crouch_held: false,
            crouched_in_air: false,
            time_in_air: 0.0,
            t: 0.0,
            // Far enough in the past that the first jump is never a chain.
            landed_at: -999.0,
            fall_speed: 0.0,
        }
    }
}

#[derive(Debug, Clone, Copy, Default)]
pub struct MoveInput {
    /// -1..1
    pub forward: f32,
    /// -1..1
    pub strafe: f32,
    pub jump: bool,
    pub crouch: bool,
}

pub fn create_player(x: f32, y: f32, z: f32, yaw: f32) -> PlayerState {
    PlayerState {
        x,
        y,
        z,
        yaw,
        ..Default::default()
    }
}

/// Total height of the body right now, mid-crouch included.
pub fn body_height(player: &PlayerState) -> f32 {
    STANDING_HEIGHT + (CROUCH_HEIGHT - STANDING_HEIGHT) * player.crouch
}

/// The eye's height *above the feet*: where the camera sits and where a shot
/// leaves from, so it must be the same number in both places.
pub fn eye_offset(player: &PlayerState) -> f32 {
    PLAYER_EYE_HEIGHT + (CROUCH_EYE_HEIGHT - PLAYER_EYE_HEIGHT) * player.crouch
}

/// The absolute eye position, which is what the camera actually uses.
pub fn eye_height(player: &PlayerState) -> f32 {
    player.z + eye_offset(player)
}

struct Support {
    floor: f32,
    ceil: f32,
    enclosed: bool,
}

/// Cells a body of `PLAYER_RADIUS` at `(x, y)` overlaps, as inclusive bounds.
///
/// The circle's AABB, exactly as AC's `rectcollide` does it — which is why the
/// player is effectively 2.2 cubes wide and needs three cells of clearance.
fn cells_in_radius(x: f32, y: f32, radius: f32) -> (i32, i32, i32, i32) {
    (
        (x - radius).floor() as i32,
        (x + radius).floor() as i32,
        (y - radius).floor() as i32,
        (y + radius).floor() as i32,
    )
}

/// The highest floor under the player's circle, and the lowest ceiling over it.
///
/// Extremes rather than the centre cell: standing at the lip of a ledge, the
/// centre may be over thin air while the body is supported, and the extremes are
/// what stop the player sinking or clipping into a low ceiling.
fn support(world: &World, x: f32, y: f32) -> Support {
    let (x0, x1, y0, y1) = cells_in_radius(x, y, PLAYER_RADIUS);
    let mut highest_floor = f32::NEG_INFINITY;
    let mut lowest_ceil = f32::INFINITY;
    for cy in y0..=y1 {
        for cx in x0..=x1 {
            if world.is_solid(cx, cy) {
                continue;
            }
            highest_floor = highest_floor.max(world.floor_at(cx, cy));
            lowest_ceil = lowest_ceil.min(world.ceil_at(cx, cy));
        }
    }
    if highest_floor == f32::NEG_INFINITY {
        // Entirely inside solid geometry — report a floor at the player's feet so
        // they are pushed out rather than dropped through the world.
        return Support {
            floor: 0.0,
            ceil: f32::INFINITY,
            enclosed: true,
        };
    }
    Support {
        floor: highest_floor,
        ceil: lowest_ceil,
        enclosed: false,
    }
}

/// Whether a body of `height` fits at `(x, y)` with its feet at `z`.
///
/// Three ways to fail: overlapping a solid cell, a floor more than one step above
/// the feet, or a ceiling too low. `height` is a parameter rather than the
/// standing constant because that is exactly what crouching changes — and it is
/// also how "you cannot stand up in here" is decided.
pub fn can_stand(world: &World, x: f32, y: f32, z: f32, height: f32) -> bool {
    let (x0, x1, y0, y1) = cells_in_radius(x, y, PLAYER_RADIUS);
    for cy in y0..=y1 {
        for cx in x0..=x1 {
            if world.is_solid(cx, cy) {
                return false;
            }
            if world.floor_at(cx, cy) > z + STEP_HEIGHT {
                return false;
            }
            if world.ceil_at(cx, cy) < z + height {
                return false;
            }
        }
    }
    true
}

/// Add an external kick to a body's velocity.
///
/// The one way anything outside this file moves a player, and it exists for
/// exactly one caller: weapon recoil. Clearing `on_ground` on an upward kick is
/// what makes a shoot-jump work at all — otherwise the vertical resolve at the
/// end of the next step lands the player again immediately, before the velocity
/// has moved them anywhere.
pub fn apply_impulse(player: &mut PlayerState, dx: f32, dy: f32, dz: f32) {
    player.vel_x += dx;
    player.vel_y += dy;
    player.vel_z += dz;
    if dz > 0.0 {
        player.on_ground = false;
    }
}

/// Advance the crouch animation, and refuse to stand up under a low ceiling.
///
/// Reads `on_ground` from the previous step, as AC's `updatecrouch` reads
/// `onfloor` — the alternative is resolving crouch after movement, which would
/// let a body change height *after* the collision test that admitted it.
fn update_crouch(world: &World, player: &mut PlayerState, input: &MoveInput, dt: f32) {
    if input.crouch && !player.crouch_held && !player.on_ground {
        player.crouched_in_air = true;
    }
    player.crouch_held = input.crouch;

    let target = if input.crouch {
        1.0
    } else if can_stand(world, player.x, player.y, player.z, STANDING_HEIGHT) {
        0.0
    } else {
        // Nowhere to stand up into. Holding the current crouch beats popping the
        // body through a ceiling.
        player.crouch
    };

    let rate = if CROUCH_TRANSITION > 0.0 {
        dt / CROUCH_TRANSITION
    } else {
        1.0
    };
    player.crouch = if target > player.crouch {
        target.min(player.crouch + rate)
    } else {
        target.max(player.crouch - rate)
    };
}

/// Unit direction the player is asking to move in, in grid coordinates.
///
/// Normalised, so forward-plus-strafe is not 1.41× faster than forward alone.
/// Diagonal overspeed is the accidental version of a movement tech; this game has
/// a deliberate one (the chain boost) and does not need both.
fn wish_direction(player: &PlayerState, input: &MoveInput) -> (f32, f32) {
    let sin = player.yaw.sin();
    let cos = player.yaw.cos();
    let dx = cos * input.forward - sin * input.strafe;
    let dy = sin * input.forward + cos * input.strafe;
    let length = dx.hypot(dy);
    if length < 1e-9 {
        return (0.0, 0.0);
    }
    (dx / length, dy / length)
}

/// Advance the player by `dt` seconds.
///
/// Horizontal movement is resolved **one axis at a time** so a blocked direction
/// slides along the wall instead of stopping dead — testing the combined vector
/// once would make every corner sticky.
pub fn step(world: &World, player: &mut PlayerState, input: &MoveInput, dt: f32) {
    let dt = dt.min(MAX_STEP);
    if dt <= 0.0 {
        player.fall_speed = 0.0;
        return;
    }

    player.t += dt;
    // An output of this step only. Cleared first so a step with no landing in it
    // cannot report the previous one's impact a second time.
    player.fall_speed = 0.0;

    update_crouch(world, player, input, dt);

    // -- horizontal: converge on the wish velocity ----------------------------
    //
    // Crouched speed is AC's `chspeed`: 0.4 on the floor, and 0.4 in the air too
    // *unless* the crouch began airborne, which is the crouch-jump exemption.
    let scale = if player.crouch > 0.5 && (player.on_ground || !player.crouched_in_air) {
        CROUCH_SPEED_SCALE
    } else {
        1.0
    };
    let speed_cap = MOVE_SPEED * scale;

    let (wx, wy) = wish_direction(player, input);
    let response = if player.on_ground {
        GROUND_RESPONSE
    } else {
        AIR_RESPONSE
    };
    let blend = 1.0 - (-response * dt).exp();
    player.vel_x += (wx * speed_cap - player.vel_x) * blend;
    player.vel_y += (wy * speed_cap - player.vel_y) * blend;

    // -- jump, and the chained-jump boost -------------------------------------
    if input.jump && player.on_ground {
        if input.strafe != 0.0 && player.t - player.landed_at <= JUMP_CHAIN_WINDOW {
            let speed = player.vel_x.hypot(player.vel_y);
            if speed > 0.1 {
                // 25% faster, but never past 125% of run speed: AC's
                // `1.25/max(speed/fullspeed, 1)` — a boost below the cap and a
                // clamp above it, not a compounding multiplier.
                let factor = JUMP_CHAIN_BOOST / (speed / MOVE_SPEED).max(1.0);
                player.vel_x *= factor;
                player.vel_y *= factor;
            }
        }
        player.vel_z = JUMP_SPEED;
        player.on_ground = false;
        player.time_in_air = 0.0;
    }

    // -- horizontal: move, one axis at a time ---------------------------------
    //
    // A refused axis loses its velocity: keeping it would store up a shove that
    // fires the instant the body clears the wall.
    let height = body_height(player);
    let dx = player.vel_x * dt;
    let dy = player.vel_y * dt;
    if dx != 0.0 {
        if can_stand(world, player.x + dx, player.y, player.z, height) {
            player.x += dx;
        } else {
            player.vel_x = 0.0;
        }
    }
    if dy != 0.0 {
        if can_stand(world, player.x, player.y + dy, player.z, height) {
            player.y += dy;
        } else {
            player.vel_y = 0.0;
        }
    }

    // Resolved before gravity, not after: `support` reads only x and y, and
    // checking afterwards means a wedged player has already been moved down by
    // one frame of falling — which does not look like falling, it looks like
    // sinking half a cube a second forever.
    let s = support(world, player.x, player.y);
    if s.enclosed {
        // Wedged in solid geometry: hold still so the player can walk back out.
        player.vel_x = 0.0;
        player.vel_y = 0.0;
        player.vel_z = 0.0;
        player.on_ground = true;
        return;
    }

    // -- vertical -------------------------------------------------------------
    //
    // Whether the body was already resting on the floor when this step began —
    // read *after* the jump, which clears it. Both branches below need it, and
    // for the same reason: "arrived on the ground" and "was already on the
    // ground" are different events, and conflating them costs two mechanics. A
    // resting body dips below the floor under gravity every single frame, so
    // treating that as a landing would reset the chain-boost window continuously
    // (making the timing free) and charge fall damage for standing still; and a
    // body genuinely falling passes through the snap-down band on its way in, so
    // treating that as a snap would mean nothing ever lands.
    let was_grounded = player.on_ground;

    player.time_in_air = if was_grounded {
        0.0
    } else {
        player.time_in_air + dt
    };
    // Gravity ramps with time in air, as AC's `dropf` does, so a fall comes down
    // harder than the jump went up.
    let gravity = GRAVITY * MAX_GRAVITY_SCALE.min(1.0 + player.time_in_air / GRAVITY_RAMP);
    player.vel_z -= gravity * dt;
    player.z += player.vel_z * dt;

    if player.z <= s.floor {
        player.z = s.floor;
        if !was_grounded {
            // A real landing. Reported for this step only; the server turns the
            // impact into damage, and the window this opens is what a chained
            // jump has to be timed against.
            player.fall_speed = if player.vel_z < 0.0 {
                -player.vel_z
            } else {
                0.0
            };
            player.landed_at = player.t;
        }
        player.vel_z = 0.0;
        player.on_ground = true;
        player.time_in_air = 0.0;
        // On the floor, so the crouch-jump exemption is spent.
        player.crouched_in_air = false;
    } else if was_grounded && player.vel_z <= 0.0 && player.z - s.floor <= STEP_HEIGHT * 0.5 {
        // Walking off a small lip shouldn't launch the player into a fall: snap
        // down. Not a landing — nothing was fallen, so it costs no health and
        // opens no chain-boost window that was never earned.
        player.z = s.floor;
        player.vel_z = 0.0;
        player.on_ground = true;
        player.time_in_air = 0.0;
        player.crouched_in_air = false;
    } else {
        player.on_ground = false;
    }
    if player.z + height > s.ceil {
        player.z = s.floor.max(s.ceil - height);
        if player.vel_z > 0.0 {
            player.vel_z = 0.0;
        }
    }
}

/// Health cost of landing at `impact` cubes per second.
///
/// Zero for anything a jump can produce, then linear.
pub fn fall_damage(impact: f32) -> f32 {
    if impact <= FALL_SAFE_SPEED {
        0.0
    } else {
        (impact - FALL_SAFE_SPEED) * FALL_DAMAGE_PER_SPEED
    }
}

/// A `playerstart` entity, as `spawn_at` reads it.
pub struct Spawn {
    pub x: f32,
    pub y: f32,
    #[allow(dead_code)] // Read by the fixture, deliberately ignored — see below.
    pub z: f32,
    pub yaw: f32,
}

/// Place a player on a spawn point, standing on the ground beneath it.
///
/// **A `playerstart`'s `z` is not the ground.** It is the mapper's own origin at
/// the moment they typed `/newent playerstart`, and in Cube 1 that origin is the
/// *eye*, not the feet — which is why the most common value across the 1741
/// official spawns is exactly four above the floor the body rests on. Nor is it
/// reliable even read that way: AC's editor flies, so the rest are scattered from
/// one to twenty-two cubes up with no relation to anything. The engine gets away
/// with it because `entinmap` and gravity resolve the spawn on arrival.
///
/// So the height comes from the world instead. `support` is the same query `step`
/// resolves against, which makes this its fixed point: a player spawned here is
/// already exactly where their first frame would put them.
pub fn spawn_at(world: &World, spawn: &Spawn) -> PlayerState {
    let x = spawn.x + 0.5;
    let y = spawn.y + 0.5;
    let s = support(world, x, y);
    // Every cell under the body solid — which no official map manages, but a
    // community one might. The centre cell's floor is the best guess left.
    let z = if s.enclosed {
        world.floor_at(spawn.x.floor() as i32, spawn.y.floor() as i32)
    } else {
        s.floor
    };
    // Entity yaw is degrees; the simulation uses radians about +x.
    let mut player = create_player(x, y, z, spawn.yaw.to_radians());
    // Resting on the floor, so say so: otherwise the very first frame refuses a
    // jump the player is standing in a perfectly good position to make.
    player.on_ground = true;
    player
}
