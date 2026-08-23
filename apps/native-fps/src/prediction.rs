//! Client-side prediction and reconciliation.
//!
//! Without this the camera sits where the server last said it was, so pressing W
//! moves you a round trip later. With it, you move on the frame you pressed the
//! key, and the server's disagreements are absorbed rather than displayed.
//!
//! The whole mechanism is three rules:
//!
//! 1. **Simulate locally, immediately.** Every command is applied to a local copy
//!    of the physics the instant it is produced.
//! 2. **Keep the commands.** Each is held with its sequence number until the
//!    server acknowledges it.
//! 3. **On a snapshot, rewind and replay.** Take the server's authoritative
//!    state, drop every command it has already applied, and re-run the rest.
//!
//! Rule 3 is the one that is easy to skip and impossible to fake. Without the
//! replay, every snapshot yanks you back to where you were a round trip ago and
//! then you move forward again — the rubber-banding that reads as "lag" even on a
//! perfect connection. `ack` is what makes it possible, which is why the server
//! sends it **per recipient** rather than in the shared rows.
//!
//! **What is predicted and what is not.** Movement is, because it is deterministic
//! given the same inputs and the same world — that is exactly what
//! `physics-vectors.json` pins. Health, ammo, hits and deaths are **not**: they
//! depend on other players, and a client that guessed would show you kills you did
//! not get. Those come from the snapshot and are simply displayed.
//!
//! A note on what "correct" means here: the client will *never* agree with the
//! server exactly, because the server integrates its own `dt` budget and applies
//! damage and impulses the client cannot know about. Reconciliation is not about
//! eliminating the difference, it is about converging on it without the player
//! seeing the correction.

use crate::physics::{step, MoveInput, PlayerState};
use crate::protocol::MoveState;
use crate::world::World;

/// A command we have simulated and the server has not yet acknowledged.
#[derive(Debug, Clone, Copy)]
struct Unacked {
    seq: u64,
    input: MoveInput,
    dt: f32,
    yaw: f32,
    pitch: f32,
}

/// How many unacknowledged commands to keep.
///
/// At 240fps and a 20Hz snapshot rate, a round trip of 100 ms is ~24 commands, so
/// this is generous. It is a **bound, not a target**: an unbounded buffer on a
/// stalled connection grows until the replay itself becomes the stall.
const MAX_UNACKED: usize = 256;

/// How far the prediction may be from the server before it is snapped rather than
/// eased, in cubes.
///
/// Under this, a correction is blended away over a few frames and you never see
/// it. Over it, something happened the client could not have predicted — a
/// teleport, a respawn, an explosion's shove — and easing across it would drag
/// the camera smoothly through a wall. A hard cut is the honest answer there.
const SNAP_DISTANCE: f32 = 2.0;

/// Fraction of the remaining error removed per second while easing.
///
/// Frame-rate independent (`1 - exp(-rate * dt)`), for the same reason the
/// movement blend is: a per-frame fraction would correct twice as fast at 240fps
/// as at 120, which makes the same connection feel different on two machines.
const EASE_RATE: f32 = 12.0;

pub struct Prediction {
    /// Where we believe we are. What the camera reads.
    pub state: PlayerState,
    /// The last authoritative state, with unacked commands replayed onto it.
    /// Kept separate so the visible position can lag it while easing.
    settled: PlayerState,
    /// Smoothed-away error, added to `settled` to produce `state`.
    error: [f32; 3],
    unacked: Vec<Unacked>,
    /// Whether a `welcome`/snapshot has ever arrived. Before that there is
    /// nothing to predict *from*, and simulating would invent a position.
    pub live: bool,
}

impl Default for Prediction {
    fn default() -> Prediction {
        Prediction {
            state: PlayerState::default(),
            settled: PlayerState::default(),
            error: [0.0; 3],
            unacked: Vec::new(),
            live: false,
        }
    }
}

impl Prediction {
    /// Adopt an authoritative position with no replay — a join or a respawn.
    pub fn reset(&mut self, x: f32, y: f32, z: f32, yaw: f32, pitch: f32) {
        self.state = PlayerState {
            x,
            y,
            z,
            yaw,
            pitch,
            on_ground: true,
            ..Default::default()
        };
        self.settled = self.state;
        self.error = [0.0; 3];
        self.unacked.clear();
        self.live = true;
    }

    /// Simulate one command locally and remember it for replay.
    pub fn predict(
        &mut self,
        world: &World,
        seq: u64,
        input: MoveInput,
        dt: f32,
        yaw: f32,
        pitch: f32,
    ) {
        if !self.live {
            return;
        }
        self.settled.yaw = yaw;
        self.settled.pitch = pitch;
        step(world, &mut self.settled, &input, dt);
        if self.unacked.len() >= MAX_UNACKED {
            // Oldest first: it is the one the server is most likely to have
            // acknowledged already, and dropping the newest would replay a past
            // that the present no longer follows from.
            self.unacked.remove(0);
        }
        self.unacked.push(Unacked {
            seq,
            input,
            dt,
            yaw,
            pitch,
        });
        // `state` is `settled` plus whatever error is still being eased away, and
        // it has to be true after *every* mutation of `settled` — not just after
        // `reconcile` and `ease`. Leaving it stale here happened to look right in
        // the app only because `ease` runs on the next line; anything that
        // predicted without easing saw a position that had not moved.
        self.apply_error();
    }

    /// Take the server's word for where we were, and replay everything it has not
    /// seen yet.
    ///
    /// `ack` is the last sequence number the server applied. Everything at or
    /// below it is history; everything above it is input the server has not
    /// processed, and re-running it is what puts the prediction back at "now".
    // Eight, and every one is a distinct fact off the wire: the ack, three
    // position components, the support state and the momentum block. Bundling
    // them into a struct would only move the list somewhere the compiler checks
    // less — the same call this crate already makes for `App::new`.
    #[allow(clippy::too_many_arguments)]
    pub fn reconcile(
        &mut self,
        world: &World,
        ack: u64,
        x: f32,
        y: f32,
        z: f32,
        on_ground: bool,
        movement: Option<&MoveState>,
    ) {
        if !self.live {
            self.reset(x, y, z, self.state.yaw, self.state.pitch);
            return;
        }

        // Where we currently think we are, so the error can be measured against
        // the replayed result rather than against a stale frame.
        let before = (self.state.x, self.state.y, self.state.z);

        let mut replayed = PlayerState {
            x,
            y,
            z,
            on_ground,
            ..self.settled
        };

        // **Momentum is rebased too, and this is the load-bearing half.**
        //
        // This used to carry the client's own velocity over, on the reasoning
        // that a snapshot sends a position and not a velocity. It sends both —
        // `MatchPlayer.private_view` has carried a `move` block since movement
        // became velocity-based, and the browser client has read it since. The
        // consequence of ignoring it is not a small drift: movement is a velocity
        // integrated against AC's friction constants, so the velocity *is* the
        // state, and replaying on the local one runs the replay on the very
        // number the correction exists to fix. The error compounds rather than
        // settling — the prediction runs away, the next snapshot drags it back,
        // and that is the elastic banding, at the snapshot rate, forever. It only
        // shows in a match, because Train never reconciles.
        //
        // A server too old to send the block leaves the predicted momentum alone,
        // which is the best guess available rather than a lie.
        if let Some(m) = movement {
            replayed.vel_x = m.vel[0];
            replayed.vel_y = m.vel[1];
            replayed.vel_z = m.vel[2];
            replayed.time_in_air = m.air;
            replayed.crouch = m.crouch;
            replayed.crouched_in_air = m.crouched_in_air;
            // `since_landed` is a duration, converted against *our* simulated
            // clock: the two clocks are unrelated, so the server's timestamp
            // would be meaningless here and the chain-jump window would open at
            // an arbitrary moment.
            replayed.landed_at = replayed.t - m.since_landed;
        }

        self.unacked.retain(|c| c.seq > ack);
        for c in &self.unacked {
            replayed.yaw = c.yaw;
            replayed.pitch = c.pitch;
            step(world, &mut replayed, &c.input, c.dt);
        }
        self.settled = replayed;

        // The correction, as an offset the visible position keeps for now.
        let dx = before.0 - replayed.x;
        let dy = before.1 - replayed.y;
        let dz = before.2 - replayed.z;
        if (dx * dx + dy * dy + dz * dz).sqrt() > SNAP_DISTANCE {
            // Too far to hide. Something happened we could not have predicted;
            // easing across it would slide the camera through geometry.
            self.error = [0.0; 3];
        } else {
            self.error = [dx, dy, dz];
        }
        self.apply_error();
    }

    /// Decay the visible error toward zero. Call once per frame.
    pub fn ease(&mut self, dt: f32) {
        if !self.live {
            return;
        }
        let keep = (-EASE_RATE * dt).exp();
        for e in &mut self.error {
            *e *= keep;
            // Below a millimetre of a cube, stop: an error that never quite
            // reaches zero keeps the position permanently, invisibly wrong.
            if e.abs() < 1e-4 {
                *e = 0.0;
            }
        }
        self.apply_error();
    }

    fn apply_error(&mut self) {
        self.state = self.settled;
        self.state.x += self.error[0];
        self.state.y += self.error[1];
        self.state.z += self.error[2];
    }

    /// How many commands are waiting to be acknowledged — a direct read on the
    /// round trip, and worth showing.
    pub fn pending(&self) -> usize {
        self.unacked.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::MapInfo;
    use crate::world::{SOLID, SPACE};

    const PLANES: [&str; 9] = [
        "type", "floor", "ceil", "wtex", "ftex", "ctex", "vdelta", "utex", "tag",
    ];

    /// An open room with a floor at 0 and a ceiling at 16.
    fn room(ssize: i32) -> World {
        let n = (ssize * ssize) as usize;
        let mut types = vec![SOLID; n];
        for y in 2..ssize - 2 {
            for x in 2..ssize - 2 {
                types[(y * ssize + x) as usize] = SPACE;
            }
        }
        let mut bytes = Vec::with_capacity(n * 9);
        bytes.extend_from_slice(&types);
        bytes.extend(std::iter::repeat_n(0u8, n)); // floor
        bytes.extend(std::iter::repeat_n(16u8, n)); // ceil
        bytes.extend(std::iter::repeat_n(0u8, n * 6)); // the rest
        let info = MapInfo {
            ssize,
            cubic_size: n,
            plane_order: PLANES.iter().map(|s| s.to_string()).collect(),
            ..Default::default()
        };
        World::new(info, &bytes).unwrap()
    }

    fn forward() -> MoveInput {
        MoveInput {
            forward: 1.0,
            ..Default::default()
        }
    }

    #[test]
    fn nothing_is_predicted_before_the_server_has_spoken() {
        // Otherwise the client invents a position at (0,0,0) — inside the solid
        // border — and the first snapshot yanks the camera across the whole map.
        let world = room(16);
        let mut p = Prediction::default();
        p.predict(&world, 1, forward(), 1.0 / 60.0, 0.0, 0.0);
        assert!(!p.live);
        assert_eq!(p.pending(), 0);
        assert_eq!(p.state.x, 0.0);
    }

    #[test]
    fn input_moves_you_on_the_frame_you_pressed_it() {
        let world = room(16);
        let mut p = Prediction::default();
        p.reset(8.0, 8.0, 0.0, 0.0, 0.0);
        let before = p.state.x;
        p.predict(&world, 1, forward(), 1.0 / 60.0, 0.0, 0.0);
        assert!(p.state.x > before, "the whole point of predicting");
        assert_eq!(p.pending(), 1);
    }

    #[test]
    fn acknowledged_commands_are_dropped_and_the_rest_replayed() {
        let world = room(16);
        let mut p = Prediction::default();
        p.reset(8.0, 8.0, 0.0, 0.0, 0.0);
        for seq in 1..=10 {
            p.predict(&world, seq, forward(), 1.0 / 60.0, 0.0, 0.0);
        }
        assert_eq!(p.pending(), 10);
        let predicted = p.state.x;

        // The server has applied the first six and reports where that put us.
        let mut server = PlayerState {
            x: 8.0,
            y: 8.0,
            on_ground: true,
            ..Default::default()
        };
        for _ in 0..6 {
            step(&world, &mut server, &forward(), 1.0 / 60.0);
        }
        p.reconcile(
            &world,
            6,
            server.x,
            server.y,
            server.z,
            server.on_ground,
            None,
        );

        assert_eq!(p.pending(), 4, "four commands are still in flight");
        // Replaying the four should land within a whisker of where we already
        // were. If the replay were skipped, the position would jump back to the
        // server's — six frames behind — which is the rubber-band.
        assert!(
            (p.state.x - predicted).abs() < 0.01,
            "replayed to {}, was predicting {predicted}",
            p.state.x
        );
    }

    #[test]
    fn a_small_correction_is_eased_rather_than_snapped() {
        let world = room(16);
        let mut p = Prediction::default();
        p.reset(8.0, 8.0, 0.0, 0.0, 0.0);
        p.predict(&world, 1, forward(), 1.0 / 60.0, 0.0, 0.0);
        let predicted = p.state.x;

        // The server puts us slightly behind where we thought.
        p.reconcile(&world, 1, predicted - 0.3, 8.0, 0.0, true, None);
        // Visibly, we have barely moved: the error is carried, not shown.
        assert!(
            (p.state.x - predicted).abs() < 0.01,
            "a 0.3-cube correction should not be visible in one frame"
        );
        // And it decays.
        for _ in 0..120 {
            p.ease(1.0 / 60.0);
        }
        assert!(
            (p.state.x - (predicted - 0.3)).abs() < 0.01,
            "the error should have been absorbed by now"
        );
    }

    #[test]
    fn a_large_correction_is_taken_immediately() {
        // A respawn, a teleport, an explosion. Easing over two cubes would slide
        // the camera through whatever is between here and there.
        let world = room(16);
        let mut p = Prediction::default();
        p.reset(8.0, 8.0, 0.0, 0.0, 0.0);
        p.predict(&world, 1, forward(), 1.0 / 60.0, 0.0, 0.0);
        p.reconcile(&world, 1, 4.0, 4.0, 0.0, true, None);
        assert!((p.state.x - 4.0).abs() < 1e-5, "{}", p.state.x);
        assert!((p.state.y - 4.0).abs() < 1e-5);
    }

    #[test]
    fn the_servers_momentum_replaces_ours_rather_than_being_replayed_over() {
        // The elastic banding, isolated. The server says we are standing still —
        // walked into a wall, throttled, shoved — and the replay has to start
        // from *its* velocity. Starting from ours re-runs the exact number the
        // correction exists to fix.
        // Roomy on purpose: half a second at full sprint covers eight cubes, and
        // a body that reaches a wall has had its velocity zeroed by the collision
        // rather than by the thing under test.
        let world = room(64);
        let mut p = Prediction::default();
        p.reset(8.0, 8.0, 0.0, 0.0, 0.0);

        let forward = MoveInput {
            forward: 1.0,
            ..Default::default()
        };
        for seq in 1..=30 {
            p.predict(&world, seq, forward, 1.0 / 60.0, 0.0, 0.0);
        }
        assert!(p.settled.vel_x > 15.0, "not moving: {}", p.settled.vel_x);

        // The server acknowledges everything and reports a body at rest.
        let stopped = MoveState::default();
        p.reconcile(&world, 30, p.settled.x, 8.0, 0.0, true, Some(&stopped));
        assert!(
            p.settled.vel_x.abs() < 1e-6,
            "kept {} of its own velocity",
            p.settled.vel_x
        );
    }

    #[test]
    fn without_the_momentum_block_the_replay_runs_away_from_the_server() {
        // Why the field is not optional in practice. Two identical clients
        // reconcile against the same authoritative "you are here, at rest"; the
        // one that ignores the momentum keeps sprinting away from it, and the
        // gap it opens is the distance the next snapshot has to yank back.
        let world = room(64);
        let run = |movement: Option<&MoveState>| {
            let mut p = Prediction::default();
            p.reset(8.0, 8.0, 0.0, 0.0, 0.0);
            let forward = MoveInput {
                forward: 1.0,
                ..Default::default()
            };
            for seq in 1..=30 {
                p.predict(&world, seq, forward, 1.0 / 60.0, 0.0, 0.0);
            }
            let acked_at = p.settled.x;
            // Ten unacknowledged commands: the replay tail a round trip leaves.
            for seq in 31..=40 {
                p.predict(&world, seq, forward, 1.0 / 60.0, 0.0, 0.0);
            }
            // The server's word: you are where command 30 left you, at rest.
            p.reconcile(&world, 30, acked_at, 8.0, 0.0, true, movement);
            p.settled.x - acked_at
        };

        let stopped = MoveState::default();
        let rebased = run(Some(&stopped));
        let carried = run(None);
        // Ten commands from a standstill barely move a body that has to
        // accelerate; ten replayed at full sprint cover several times as much.
        assert!(
            carried > rebased * 2.0,
            "rebased replay travelled {rebased:.3}, carried-over {carried:.3} —              the block is supposed to make a difference"
        );
    }

    #[test]
    fn the_unacked_buffer_is_bounded() {
        // A stalled connection must not grow the replay until the replay is the
        // stall.
        let world = room(16);
        let mut p = Prediction::default();
        p.reset(8.0, 8.0, 0.0, 0.0, 0.0);
        for seq in 1..=(MAX_UNACKED as u64 + 50) {
            p.predict(&world, seq, MoveInput::default(), 1.0 / 240.0, 0.0, 0.0);
        }
        assert_eq!(p.pending(), MAX_UNACKED);
    }

    #[test]
    fn reconciling_before_a_reset_adopts_the_server_position() {
        // The first snapshot after joining: there is nothing to replay, so this
        // must behave as a reset rather than replaying onto a default state at
        // the origin.
        let world = room(16);
        let mut p = Prediction::default();
        p.reconcile(&world, 0, 5.0, 6.0, 1.0, true, None);
        assert!(p.live);
        assert_eq!((p.state.x, p.state.y, p.state.z), (5.0, 6.0, 1.0));
    }
}
