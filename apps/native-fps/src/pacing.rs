//! The FPS cap: a fixed cadence of deadlines, slept to and then spun the last
//! fraction of a millisecond.
//!
//! The cap used to sleep "whatever is left of this frame" each frame. The OS
//! wakes a sleeper late — about 0.4 ms on this Windows machine, measured — and
//! because every frame re-measured from when it actually woke, the lateness
//! never came back: a 240 cap ran at 217 and, worse, a 144 cap ran at 137, under
//! the refresh rate of the display it was set to match.
//!
//! Two changes, and both are needed:
//!
//! - **Deadlines advance by the budget**, not from "now", so one late wake is
//!   paid back by the next frame's shorter wait rather than added to the
//!   cadence. A frame that runs *long* (a hitch, a load) does not bank a burst
//!   of catch-up frames: the cadence restarts from now if it fell more than a
//!   frame behind.
//! - **Sleep short, spin the rest.** The sleep aims at the deadline less what
//!   the OS has been overshooting by lately, and the remainder — a fraction of
//!   a millisecond — is a `yield` loop. A cap's job is still to leave the
//!   machine idle between frames, and it does: the spin is the last sliver of
//!   each slice, not the slice.

use std::time::{Duration, Instant};

/// Never trust the sleep closer than this to a deadline, whatever the overshoot
/// estimate says: an estimate learned on a quiet machine is wrong on a busy one.
const MARGIN: Duration = Duration::from_micros(200);

/// The overshoot estimate's starting point, and its ceiling. Above a couple of
/// milliseconds the timer is not late, the machine is busy, and spinning the
/// difference would be the cap burning the core it exists to free.
const START_OVERSHOOT: Duration = Duration::from_micros(500);
const MAX_OVERSHOOT: Duration = Duration::from_millis(2);

#[derive(Debug)]
pub struct FramePacer {
    deadline: Option<Instant>,
    overshoot: Duration,
}

impl Default for FramePacer {
    fn default() -> Self {
        Self {
            deadline: None,
            overshoot: START_OVERSHOOT,
        }
    }
}

/// The next deadline: one budget after the last, unless that is already more
/// than a budget in the past — then the cadence restarts at `now`, so a long
/// frame is not followed by a burst of unpaced ones.
pub fn next_deadline(last: Option<Instant>, now: Instant, budget: Duration) -> Instant {
    match last {
        Some(last) => {
            let next = last + budget;
            if next + budget < now {
                now
            } else {
                next
            }
        }
        None => now + budget,
    }
}

impl FramePacer {
    /// Block until this frame's slice of a `limit` FPS cap is over. `0` is
    /// uncapped, and forgets the cadence so turning a cap on starts clean.
    pub fn wait(&mut self, limit: u32) {
        if limit == 0 {
            self.deadline = None;
            return;
        }
        let budget = Duration::from_secs_f64(1.0 / limit as f64);
        let now = Instant::now();
        let deadline = next_deadline(self.deadline, now, budget);
        self.deadline = Some(deadline);

        let early = self.overshoot + MARGIN;
        if let Some(sleep) = deadline.checked_duration_since(now + early) {
            let asked = Instant::now() + sleep;
            std::thread::sleep(sleep);
            // How late the OS woke us, folded in slowly: one descheduled
            // wake should not make the next hundred frames spin longer.
            let late = Instant::now().saturating_duration_since(asked);
            self.overshoot = ((self.overshoot * 7 + late) / 8).min(MAX_OVERSHOOT);
        }
        while Instant::now() < deadline {
            std::thread::yield_now();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const BUDGET: Duration = Duration::from_micros(4167);

    #[test]
    fn deadlines_advance_by_the_budget_not_from_now() {
        // The whole fix. A frame that woke 0.4 ms late does not push the next
        // deadline 0.4 ms later.
        let t0 = Instant::now();
        let first = next_deadline(None, t0, BUDGET);
        assert_eq!(first, t0 + BUDGET);
        let late_wake = first + Duration::from_micros(400);
        assert_eq!(
            next_deadline(Some(first), late_wake, BUDGET),
            first + BUDGET
        );
    }

    #[test]
    fn a_long_frame_restarts_the_cadence_instead_of_bursting() {
        let t0 = Instant::now();
        let hitch = t0 + Duration::from_millis(50);
        assert_eq!(next_deadline(Some(t0), hitch, BUDGET), hitch);
        // Up to a frame behind is still paid back on the cadence.
        let slightly = t0 + BUDGET + Duration::from_micros(500);
        assert_eq!(next_deadline(Some(t0), slightly, BUDGET), t0 + BUDGET);
    }

    #[test]
    fn a_cap_holds_its_rate() {
        // A real clock, so loose bounds: 30 frames at 240 must take at least
        // 30 budgets (never fast) and not drift by the old 10%.
        let mut pacer = FramePacer::default();
        pacer.wait(240);
        let start = Instant::now();
        for _ in 0..30 {
            pacer.wait(240);
        }
        let took = start.elapsed().as_secs_f64();
        let want = 30.0 / 240.0;
        assert!(took >= want * 0.98, "{took} < {want}");
        assert!(took < want * 1.08, "{took} drifted past {want}");
    }
}
