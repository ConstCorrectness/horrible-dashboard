//! Where a frame's CPU time goes, phase by phase.
//!
//! G0's `cpu_ms` is one number: from the end of the frame-cap wait to the
//! return from `present`. That is the right number for "is this frame
//! CPU-bound", and the wrong one for "what do I fix": it also spans
//! `get_current_texture`, which **blocks** when the GPU is a frame behind, so a
//! GPU-bound frame reads as CPU time there. Splitting it is what tells the two
//! apart.
//!
//! Laps rather than scopes: the frame is one straight line through
//! `about_to_wait` and `RedrawRequested`, so each phase is "the time since the
//! last lap", and the phases sum to the whole by construction. Nothing is
//! measured twice and nothing falls between two timers.

use std::time::Instant;

/// One stretch of a frame, in the order a frame runs them.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    /// Draining the socket, the clipboard and the store.
    Net,
    /// Interpolation, input and prediction, animation, the flash.
    Sim,
    /// Between `about_to_wait` returning and `RedrawRequested` arriving: the
    /// event loop's own time.
    Loop,
    /// The world-space vertex streams: bodies, nades, items, decals, effects,
    /// water, the view model.
    Build,
    /// The HUD, the menu and the cards, and the chat text layer.
    Hud,
    /// Every `set_*` on the renderer, and its per-frame uniform writes.
    Upload,
    /// `get_current_texture`: blocks when the GPU is behind, so a long one is
    /// the GPU's time showing up on the CPU's clock.
    Acquire,
    /// Recording the passes.
    Encode,
    /// `queue.submit`.
    Submit,
    /// `queue.present`.
    Present,
}

pub const PHASE_COUNT: usize = 10;

impl Phase {
    pub const ALL: [Phase; PHASE_COUNT] = [
        Phase::Net,
        Phase::Sim,
        Phase::Loop,
        Phase::Build,
        Phase::Hud,
        Phase::Upload,
        Phase::Acquire,
        Phase::Encode,
        Phase::Submit,
        Phase::Present,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Phase::Net => "net",
            Phase::Sim => "sim",
            Phase::Loop => "loop",
            Phase::Build => "build",
            Phase::Hud => "hud",
            Phase::Upload => "upload",
            Phase::Acquire => "acquire",
            Phase::Encode => "encode",
            Phase::Submit => "submit",
            Phase::Present => "present",
        }
    }
}

/// Milliseconds per phase for one frame.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct PhaseTimes(pub [f64; PHASE_COUNT]);

impl PhaseTimes {
    pub fn get(&self, phase: Phase) -> f64 {
        self.0[phase as usize]
    }

    pub fn add(&mut self, phase: Phase, ms: f64) {
        self.0[phase as usize] += ms;
    }

    pub fn total(&self) -> f64 {
        self.0.iter().sum()
    }
}

/// A stopwatch that hands each lap to the phase that just ended.
#[derive(Clone, Copy, Debug)]
pub struct Laps {
    last: Instant,
    pub times: PhaseTimes,
}

impl Default for Laps {
    fn default() -> Self {
        Self::new()
    }
}

impl Laps {
    pub fn new() -> Self {
        Self {
            last: Instant::now(),
            times: PhaseTimes::default(),
        }
    }

    /// Zero the frame and start its clock.
    pub fn start(&mut self) {
        self.last = Instant::now();
        self.times = PhaseTimes::default();
    }

    /// Charge everything since the last lap to `phase`.
    pub fn lap(&mut self, phase: Phase) {
        let now = Instant::now();
        self.times
            .add(phase, now.duration_since(self.last).as_secs_f64() * 1000.0);
        self.last = now;
    }

    /// Fold in phases timed elsewhere (the renderer's), and restart the clock
    /// so that time is not charged again to the next lap.
    pub fn absorb(&mut self, other: &PhaseTimes) {
        for (mine, theirs) in self.times.0.iter_mut().zip(other.0.iter()) {
            *mine += theirs;
        }
        self.last = Instant::now();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_phase_has_a_distinct_name_in_frame_order() {
        let names: Vec<&str> = Phase::ALL.iter().map(|p| p.name()).collect();
        let mut sorted = names.clone();
        sorted.sort();
        sorted.dedup();
        assert_eq!(sorted.len(), PHASE_COUNT);
        for (i, phase) in Phase::ALL.iter().enumerate() {
            assert_eq!(*phase as usize, i, "ALL is in declaration order");
        }
    }

    #[test]
    fn laps_charge_each_stretch_once_and_absorb_restarts_the_clock() {
        let mut laps = Laps::new();
        laps.start();
        std::thread::sleep(std::time::Duration::from_millis(2));
        laps.lap(Phase::Net);
        assert!(laps.times.get(Phase::Net) >= 1.5);
        let mut outside = PhaseTimes::default();
        outside.add(Phase::Encode, 5.0);
        std::thread::sleep(std::time::Duration::from_millis(2));
        laps.absorb(&outside);
        laps.lap(Phase::Hud);
        // The sleep before `absorb` belongs to whoever timed `outside`, not to
        // the next lap.
        assert!(laps.times.get(Phase::Hud) < 1.0);
        assert_eq!(laps.times.get(Phase::Encode), 5.0);
        let total = laps.times.total();
        assert!((total - laps.times.0.iter().sum::<f64>()).abs() < 1e-9);
    }
}
