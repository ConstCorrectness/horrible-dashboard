//! Our own shots, felt on the frame they are sent.
//!
//! The kick, the shake, the gunshot and the recoil step used to wait for the
//! server's `Fx::Shot` naming us: a round trip plus a tick after the click,
//! which is the difference between a gun and a gun-shaped delay. They now play
//! when the command carrying `fire` goes on the wire, **if** the shot is one the
//! server will take: alive, not reloading, a round left once every shot still in
//! flight has spent its own, the trigger released since the last one on a
//! semi-automatic, and no weapon switch still on its way.
//!
//! What makes it safe is the reconciliation, not the guess. Each snapshot's
//! `ack` says which commands the server has stepped, and its `fx` which of them
//! fired. So per snapshot:
//!
//! - the server's shots for us beyond the predicted ones it just acknowledged
//!   are shots we did not foresee, and get their feedback late rather than
//!   never;
//! - predicted shots it acknowledged without firing were refused, and are
//!   simply dropped (a kick cannot be un-felt, and taking it back would be a
//!   second wrong movement);
//! - the spray index the camera follows is the server's plus the predicted
//!   shots still in flight, so a shot is never kicked twice, once on the click
//!   and again when the snapshot that counts it lands.
//!
//! The browser's `ShotController` makes the same bargain from its own trigger.
//! Hits are never predicted, here or there: those are the server's alone.

/// The facts about the held weapon and our own state a prediction reads.
#[derive(Clone, Copy, Debug)]
pub struct ShotGate {
    pub alive: bool,
    pub reloading: bool,
    /// Rounds in the magazine as of the last snapshot.
    pub ammo: i32,
    /// 0 for a weapon without a magazine (the knife).
    pub mag: i32,
    pub auto: bool,
    /// Seconds without a shot after which the server starts the pattern over.
    pub spray_reset: f32,
}

/// A shot predicted on the command just sent.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Predicted {
    /// The pattern starts again with this shot (`spray_reset` elapsed).
    pub reset: bool,
}

#[derive(Default)]
pub struct OwnShots {
    /// Command sequences predicted to fire and not yet acknowledged.
    pending: Vec<u64>,
    /// When the last predicted shot was sent, ms on `App::clock_ms`.
    last_shot_ms: Option<f64>,
    /// A semi-automatic shot was predicted and the trigger has not come up.
    latched: bool,
    /// A weapon switch sent on this command and not yet acknowledged.
    switch_in_flight: Option<u64>,
}

impl OwnShots {
    /// Record the command `seq` that just went out. `fired` is its `fire` flag,
    /// `trigger` whether the key is down, `switched` whether it carried a weapon
    /// switch. `Some` when it is predicted to be a shot.
    pub fn sent(
        &mut self,
        seq: u64,
        fired: bool,
        trigger: bool,
        switched: bool,
        now_ms: f64,
        gate: ShotGate,
    ) -> Option<Predicted> {
        if !trigger {
            self.latched = false;
        }
        if switched {
            self.switch_in_flight = Some(seq);
        }
        if !fired || self.switch_in_flight.is_some() {
            return None;
        }
        if !gate.alive || gate.reloading {
            return None;
        }
        if gate.mag > 0 && gate.ammo - self.pending.len() as i32 <= 0 {
            return None;
        }
        if !gate.auto && self.latched {
            return None;
        }
        let reset = self
            .last_shot_ms
            .is_none_or(|last| now_ms - last > f64::from(gate.spray_reset) * 1000.0);
        self.pending.push(seq);
        self.last_shot_ms = Some(now_ms);
        self.latched = !gate.auto;
        Some(Predicted { reset })
    }

    /// Reconcile with a snapshot: `ack` is the last command it stepped,
    /// `fired` how many `Fx::Shot`s in it are ours. Returns how many of those
    /// were not predicted, and so still need their feedback.
    pub fn snapshot(&mut self, ack: u64, fired: usize) -> usize {
        let before = self.pending.len();
        self.pending.retain(|&seq| seq > ack);
        let acknowledged = before - self.pending.len();
        if self.switch_in_flight.is_some_and(|seq| seq <= ack) {
            self.switch_in_flight = None;
        }
        fired.saturating_sub(acknowledged)
    }

    /// The spray index the camera should have been kicked to: the server's,
    /// plus every predicted shot it has not stepped yet.
    pub fn spray_target(&self, server_index: i32) -> i32 {
        server_index + self.pending.len() as i32
    }

    /// Forget everything: a join, a respawn, a new match.
    pub fn reset(&mut self) {
        *self = OwnShots::default();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rifle() -> ShotGate {
        ShotGate {
            alive: true,
            reloading: false,
            ammo: 30,
            mag: 30,
            auto: true,
            spray_reset: 0.4,
        }
    }

    #[test]
    fn a_shot_the_server_will_take_is_felt_when_it_is_sent() {
        let mut shots = OwnShots::default();
        let p = shots.sent(1, true, true, false, 1000.0, rifle());
        assert_eq!(p, Some(Predicted { reset: true }));
        // Inside the reset window: the pattern carries on.
        let p = shots.sent(2, true, true, false, 1100.0, rifle());
        assert_eq!(p, Some(Predicted { reset: false }));
    }

    #[test]
    fn the_servers_copy_of_a_predicted_shot_is_not_played_again() {
        let mut shots = OwnShots::default();
        shots.sent(5, true, true, false, 0.0, rifle());
        assert_eq!(shots.snapshot(5, 1), 0);
    }

    #[test]
    fn a_shot_the_server_fired_that_we_did_not_predict_still_plays() {
        let mut shots = OwnShots::default();
        assert_eq!(shots.snapshot(3, 1), 1);
    }

    #[test]
    fn a_refused_shot_is_dropped_without_playing_anything() {
        let mut shots = OwnShots::default();
        shots.sent(5, true, true, false, 0.0, rifle());
        assert_eq!(shots.snapshot(5, 0), 0);
        assert_eq!(shots.spray_target(0), 0);
    }

    #[test]
    fn a_shot_in_flight_is_counted_into_the_spray_index_once() {
        let mut shots = OwnShots::default();
        shots.sent(1, true, true, false, 0.0, rifle());
        shots.sent(2, true, true, false, 100.0, rifle());
        // A snapshot that has stepped neither: the server is still at 0, and
        // the camera has already been kicked to 2.
        shots.snapshot(0, 0);
        assert_eq!(shots.spray_target(0), 2);
        // One stepped: the server says 1, one still in flight.
        shots.snapshot(1, 1);
        assert_eq!(shots.spray_target(1), 2);
        shots.snapshot(2, 1);
        assert_eq!(shots.spray_target(2), 2);
    }

    #[test]
    fn nothing_is_predicted_dead_reloading_or_on_an_empty_magazine() {
        let mut shots = OwnShots::default();
        let dead = ShotGate {
            alive: false,
            ..rifle()
        };
        let reloading = ShotGate {
            reloading: true,
            ..rifle()
        };
        assert_eq!(shots.sent(1, true, true, false, 0.0, dead), None);
        assert_eq!(shots.sent(2, true, true, false, 0.0, reloading), None);
        // One round left and one shot already in flight: the next is a click.
        let last = ShotGate { ammo: 1, ..rifle() };
        assert!(shots.sent(3, true, true, false, 0.0, last).is_some());
        assert_eq!(shots.sent(4, true, true, false, 100.0, last), None);
    }

    #[test]
    fn a_knife_has_no_magazine_to_run_out_of() {
        let mut shots = OwnShots::default();
        let knife = ShotGate {
            ammo: 0,
            mag: 0,
            ..rifle()
        };
        assert!(shots.sent(1, true, true, false, 0.0, knife).is_some());
    }

    #[test]
    fn a_semi_automatic_needs_the_trigger_released_between_shots() {
        let mut shots = OwnShots::default();
        let pistol = ShotGate {
            auto: false,
            ..rifle()
        };
        assert!(shots.sent(1, true, true, false, 0.0, pistol).is_some());
        // Held: the server refuses it, so it is not felt.
        assert_eq!(shots.sent(2, true, true, false, 500.0, pistol), None);
        // Released, then pressed again.
        assert_eq!(shots.sent(3, false, false, false, 600.0, pistol), None);
        assert!(shots.sent(4, true, true, false, 700.0, pistol).is_some());
    }

    #[test]
    fn nothing_is_predicted_across_a_weapon_switch_in_flight() {
        let mut shots = OwnShots::default();
        // The switch goes out; until it is stepped, the weapon in hand is not
        // the one the server will fire.
        assert_eq!(shots.sent(1, true, true, true, 0.0, rifle()), None);
        assert_eq!(shots.sent(2, true, true, false, 10.0, rifle()), None);
        shots.snapshot(1, 0);
        assert!(shots.sent(3, true, true, false, 20.0, rifle()).is_some());
    }

    #[test]
    fn no_fire_flag_is_no_shot() {
        let mut shots = OwnShots::default();
        assert_eq!(shots.sent(1, false, true, false, 0.0, rifle()), None);
    }
}
