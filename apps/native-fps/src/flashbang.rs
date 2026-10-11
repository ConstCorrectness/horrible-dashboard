//! The flashbang, as this client draws it: how white the screen is, frame by
//! frame, and what it does to your ears.
//!
//! Named `flashbang` because the muzzle flash lives in `viewmodel.rs`.
//!
//! How blind a flash leaves you is the server's answer (`grenades.flash_effect`):
//! a peak whiteness and the **seconds left** of its hold and fade. This file runs
//! the curve on those at frame rate and counts them down between snapshots, the
//! same way `MatchPlayer.step_flash` does on the server — so the fade is smooth
//! at a 20 Hz tick instead of the staircase `you.flash` drawn raw would be.
//!
//! `flash_alpha` exists three times — `grenades.py`, `flashbang.ts` and here —
//! and `flash-vectors.json` (replayed by `tests/conformance.rs`) binds them. The
//! ring and muffle constants are pinned to the browser's by
//! `tests/browser_parity.rs`.

use crate::protocol::{Flashed, SelfState};

/// Above this much white a bot cannot see and an ally paints nothing on the
/// radar (`grenades.FLASH_BLIND`).
pub const FLASH_BLIND: f32 = 0.6;

/// The ring: a narrow band of noise high up, with a sine under it.
pub const FLASH_RING_HZ: f32 = 4100.0;
pub const FLASH_RING_Q: f32 = 12.0;
pub const FLASH_RING_GAIN: f32 = 0.7;
pub const FLASH_RING_BODY: f32 = 3850.0;
/// The world's sound through the white: a low-pass from open down to this.
pub const FLASH_MUFFLE_HZ: f32 = 700.0;
pub const FLASH_OPEN_HZ: f32 = 18000.0;
/// Flashes shorter than this in all do not ring.
pub const FLASH_RING_MIN: f32 = 0.75;

/// How strong the burned-in frame is at the start of the fade, under the white.
pub const AFTERIMAGE_STRENGTH: f32 = 0.85;

/// How white the screen is right now, 0..1. Held at `peak`, then a smoothstep
/// down over the fade. Port of `grenades.flash_alpha`.
pub fn flash_alpha(peak: f32, hold_left: f32, fade_left: f32, fade_total: f32) -> f32 {
    if peak <= 0.0 {
        return 0.0;
    }
    if hold_left > 0.0 {
        return peak.min(1.0);
    }
    if fade_left <= 0.0 || fade_total <= 0.0 {
        return 0.0;
    }
    let t = (fade_left / fade_total).clamp(0.0, 1.0);
    (peak * t * t * (3.0 - 2.0 * t)).min(1.0)
}

/// Whether a flash rings, how loud and for how long: tied to the flash's own
/// length so the ringing and the white clear together.
pub fn flash_ring(peak: f32, hold: f32, fade: f32) -> Option<(f32, f32)> {
    if peak <= 0.0 || hold + fade < FLASH_RING_MIN {
        return None;
    }
    Some((peak.clamp(0.5, 1.0), 0.6 + hold + 0.5 * fade))
}

/// The world's low-pass cutoff at a given whiteness, interpolated in log
/// frequency (how an ear hears a sweep).
pub fn muffle_hz(alpha: f32) -> f32 {
    let a = alpha.clamp(0.0, 1.0);
    FLASH_OPEN_HZ * (FLASH_MUFFLE_HZ / FLASH_OPEN_HZ).powf(a)
}

/// The four numbers the server sends, as seconds left.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct FlashFields {
    pub peak: f32,
    pub hold: f32,
    pub fade: f32,
    pub fade_total: f32,
}

impl FlashFields {
    /// Out of a snapshot. An older server sends only `flash`; that becomes a
    /// plain fade from its value (its linear fade lasted `flash * 6` seconds).
    pub fn from_self(you: &SelfState) -> FlashFields {
        match you.flash_peak {
            Some(peak) => FlashFields {
                peak,
                hold: you.flash_hold,
                fade: you.flash_fade,
                fade_total: if you.flash_fade_total > 0.0 {
                    you.flash_fade_total
                } else {
                    you.flash_fade
                },
            },
            None if you.flash > 0.0 => FlashFields {
                peak: you.flash,
                hold: 0.0,
                fade: you.flash * 6.0,
                fade_total: you.flash * 6.0,
            },
            None => FlashFields::default(),
        }
    }

    /// Run the hold, then the fade, down by `dt`. Port of
    /// `MatchPlayer.step_flash`, including the spill from hold into fade.
    pub fn step(self, dt: f32) -> FlashFields {
        if self.peak <= 0.0 || dt <= 0.0 {
            return self;
        }
        let mut next = self;
        let mut rest = dt;
        if next.hold > 0.0 {
            let spill = rest - next.hold;
            next.hold = (next.hold - rest).max(0.0);
            if spill <= 0.0 {
                return next;
            }
            rest = spill;
        }
        next.fade = (next.fade - rest).max(0.0);
        if next.fade <= 0.0 {
            return FlashFields::default();
        }
        next
    }

    pub fn alpha(&self) -> f32 {
        flash_alpha(self.peak, self.hold, self.fade, self.fade_total)
    }

    /// How far through the fade, 1 → 0; 1 throughout the hold.
    pub fn remaining(&self) -> f32 {
        if self.peak <= 0.0 {
            0.0
        } else if self.hold > 0.0 {
            1.0
        } else if self.fade_total > 0.0 {
            (self.fade / self.fade_total).clamp(0.0, 1.0)
        } else {
            0.0
        }
    }
}

/// The flash this client is drawing: re-seeded by each snapshot, stepped by each
/// frame, plus the afterimage that a landed flash asks for.
#[derive(Debug, Clone, Copy, Default)]
pub struct FlashState {
    pub fields: FlashFields,
    /// How strong the current afterimage is, from the flash that took it.
    pub after_peak: f32,
    /// A flash landed and the next rendered frame should be kept as its ghost.
    pub capture_wanted: bool,
}

impl FlashState {
    /// Adopt a snapshot. Returns the rings owed for flashes that just landed,
    /// as `(volume, seconds)`.
    pub fn seed(&mut self, you: &SelfState) -> Vec<(f32, f32)> {
        self.fields = FlashFields::from_self(you);
        self.landed(&you.flashed)
    }

    fn landed(&mut self, flashed: &[Flashed]) -> Vec<(f32, f32)> {
        let mut rings = Vec::new();
        for f in flashed {
            self.capture_wanted = true;
            self.after_peak = self.after_peak.max(f.peak.min(1.0));
            if let Some(ring) = flash_ring(f.peak, f.hold, f.fade) {
                rings.push(ring);
            }
        }
        rings
    }

    pub fn step(&mut self, dt: f32) {
        self.fields = self.fields.step(dt);
        if self.fields.peak <= 0.0 {
            self.after_peak = 0.0;
        }
    }

    pub fn alpha(&self) -> f32 {
        self.fields.alpha()
    }

    /// How strong the afterimage is right now, 0..1.
    pub fn afterimage(&self) -> f32 {
        AFTERIMAGE_STRENGTH * self.after_peak * self.fields.remaining()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn holds_white_then_clears() {
        let f = FlashFields {
            peak: 1.0,
            hold: 0.5,
            fade: 1.0,
            fade_total: 1.0,
        };
        assert_eq!(f.step(0.25).alpha(), 1.0);
        let mid = f.step(1.0).alpha();
        assert!(mid > 0.0 && mid < 1.0);
        assert_eq!(f.step(3.0), FlashFields::default());
    }

    #[test]
    fn a_landed_flash_wants_an_afterimage_and_a_ring() {
        let mut state = FlashState::default();
        let rings = state.landed(&[Flashed {
            peak: 1.0,
            hold: 1.0,
            fade: 2.0,
        }]);
        assert!(state.capture_wanted);
        assert_eq!(rings.len(), 1);
        // A flicker behind you does not ring.
        assert!(flash_ring(0.5, 0.0, 0.3).is_none());
    }

    #[test]
    fn the_muffle_runs_from_open_to_closed() {
        assert!((muffle_hz(0.0) - FLASH_OPEN_HZ).abs() < 1.0);
        assert!((muffle_hz(1.0) - FLASH_MUFFLE_HZ).abs() < 1.0);
    }
}
