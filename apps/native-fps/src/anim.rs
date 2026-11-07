//! Small motion helpers for the native UI: rolling numbers and staggered
//! entrances, driven by the frame clock.
//!
//! The browser has the same two (`RollingNumber` in `DataList.tsx`, the kill
//! feed's slide-in), so the native versions use the browser's timings.
//!
//! **A rolling number is seeded at its value.** It only rolls on a *change*:
//! one that starts at zero and counts up to the real value on first sight
//! reports a wrong number for half a second every time a panel appears.

/// How long a number takes to roll to a new value — the browser's 420 ms.
pub const ROLL_SECS: f32 = 0.42;
/// How long one item takes to slide and fade in.
pub const ENTER_SECS: f32 = 0.2;
/// The delay between consecutive items of a list arriving together.
pub const STAGGER_SECS: f32 = 0.035;
/// The longest any item waits for its turn, however long the list: a board of
/// twenty rows must not take most of a second to finish arriving.
pub const STAGGER_CAP_SECS: f32 = 0.18;

/// Cubic ease-out: fast at first, settling at the end.
pub fn ease_out(t: f32) -> f32 {
    let t = t.clamp(0.0, 1.0);
    1.0 - (1.0 - t).powi(3)
}

/// A number that eases to each new value instead of jumping.
#[derive(Clone, Copy, Debug)]
pub struct Rolling {
    from: f32,
    to: f32,
    age: f32,
}

impl Rolling {
    /// Showing `value` already, with nothing to roll.
    pub fn new(value: f32) -> Rolling {
        Rolling {
            from: value,
            to: value,
            age: ROLL_SECS,
        }
    }

    /// Roll towards `value` if it is a new one. From wherever the display is
    /// now, so a second change mid-roll carries on rather than jumping back.
    pub fn set(&mut self, value: f32) {
        if value != self.to {
            self.from = self.value();
            self.to = value;
            self.age = 0.0;
        }
    }

    /// Jump straight to `value` — a respawn, a new round: a reset, not news.
    pub fn snap(&mut self, value: f32) {
        *self = Rolling::new(value);
    }

    pub fn tick(&mut self, dt: f32) {
        self.age = (self.age + dt.max(0.0)).min(ROLL_SECS);
    }

    /// What to show now.
    pub fn value(&self) -> f32 {
        let k = ease_out(self.age / ROLL_SECS);
        self.from + (self.to - self.from) * k
    }

    /// `value`, rounded for display.
    pub fn shown(&self) -> i64 {
        self.value().round() as i64
    }

    pub fn target(&self) -> f32 {
        self.to
    }

    pub fn rolling(&self) -> bool {
        self.age < ROLL_SECS && self.from != self.to
    }
}

impl Default for Rolling {
    fn default() -> Rolling {
        Rolling::new(0.0)
    }
}

/// How far item `index` of a list that appeared `age` seconds ago has arrived:
/// `(opacity, slide)` where `slide` is the fraction of its entrance offset still
/// to travel (1 = at the start, 0 = in place).
pub fn entrance(age: f32, index: usize) -> (f32, f32) {
    let delay = (index as f32 * STAGGER_SECS).min(STAGGER_CAP_SECS);
    let t = ease_out((age - delay) / ENTER_SECS);
    (t, 1.0 - t)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_rolling_number_starts_at_its_value() {
        let r = Rolling::new(62.0);
        assert_eq!(r.shown(), 62);
        assert!(!r.rolling());
    }

    #[test]
    fn it_rolls_to_a_change_and_lands_exactly() {
        let mut r = Rolling::new(100.0);
        r.set(38.0);
        assert_eq!(r.shown(), 100);
        r.tick(ROLL_SECS * 0.5);
        let mid = r.value();
        assert!(mid < 100.0 && mid > 38.0, "{mid}");
        // Ease-out: more than half way at half time.
        assert!(mid < 69.0, "{mid}");
        r.tick(10.0);
        assert_eq!(r.value(), 38.0);
        assert!(!r.rolling());
    }

    #[test]
    fn a_change_mid_roll_continues_from_what_is_shown() {
        let mut r = Rolling::new(0.0);
        r.set(100.0);
        r.tick(ROLL_SECS * 0.5);
        let shown = r.value();
        r.set(50.0);
        assert!((r.value() - shown).abs() < 1e-4, "jumped on retarget");
    }

    #[test]
    fn entrances_stagger_and_the_stagger_is_capped() {
        let (a0, s0) = entrance(0.0, 0);
        assert_eq!((a0, s0), (0.0, 1.0));
        assert_eq!(entrance(ENTER_SECS, 0), (1.0, 0.0));
        // The second row is behind the first at the same moment.
        assert!(entrance(0.1, 1).0 < entrance(0.1, 0).0);
        // Row 50 arrives no later than the cap plus one entrance.
        assert_eq!(entrance(STAGGER_CAP_SECS + ENTER_SECS, 50).0, 1.0);
    }
}
