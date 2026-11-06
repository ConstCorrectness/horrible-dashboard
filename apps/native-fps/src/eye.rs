//! Where the camera sits between two input commands.
//!
//! Input is produced at 250 Hz on its own clock (`INPUT_HZ` in `app.rs`), and
//! the predicted body only moves when a command steps it. Drawn straight from
//! that body, the camera stood still on every frame without a command: above
//! 250 fps half the frames repeated the last position, and at 300 fps a strafe
//! juddered in a 2:1 cadence that reads as stutter, not as speed.
//!
//! So each step records the eye, and each frame draws it between the last two
//! steps, by how far through the next interval it is. That is the classic
//! fixed-step render interpolation: the view trails the newest step by at most
//! one command interval (4–7 ms), in exchange for moving on every frame.
//! **Position only**: the angles are the mouse's, applied the instant it moves,
//! and nothing here touches them.
//!
//! A step longer than `SNAP_DISTANCE` is a teleport, a respawn or a correction
//! too big to ease, and is taken at once rather than drawn as one frame of the
//! camera sliding through whatever was in between.

/// Cubes. Matches `prediction.rs`'s snap: anything farther did not happen by
/// moving there.
pub const SNAP_DISTANCE: f32 = 2.0;

#[derive(Default, Clone, Copy, Debug)]
pub struct EyeLerp {
    prev: [f32; 3],
    curr: [f32; 3],
    /// The last step's length, seconds: how long the move from `prev` to
    /// `curr` took, so drawing it takes the same.
    span: f32,
    primed: bool,
}

impl EyeLerp {
    /// The eye after a step that covered `dt` seconds.
    pub fn push(&mut self, eye: [f32; 3], dt: f32) {
        let far = dist(eye, self.curr) > SNAP_DISTANCE;
        self.prev = if !self.primed || far { eye } else { self.curr };
        self.curr = eye;
        self.span = dt.max(1e-6);
        self.primed = true;
    }

    /// Stand at `eye` with nothing to interpolate from: a spawn, a reset.
    pub fn snap(&mut self, eye: [f32; 3]) {
        self.prev = eye;
        self.curr = eye;
        self.primed = true;
    }

    /// The eye `since` seconds after the last step; `None` before any.
    pub fn at(&self, since: f32) -> Option<[f32; 3]> {
        if !self.primed {
            return None;
        }
        let t = (since / self.span).clamp(0.0, 1.0);
        Some([
            self.prev[0] + (self.curr[0] - self.prev[0]) * t,
            self.prev[1] + (self.curr[1] - self.prev[1]) * t,
            self.prev[2] + (self.curr[2] - self.prev[2]) * t,
        ])
    }

    /// The newest step's eye, un-interpolated.
    pub fn latest(&self) -> Option<[f32; 3]> {
        self.primed.then_some(self.curr)
    }
}

fn dist(a: [f32; 3], b: [f32; 3]) -> f32 {
    ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2)).sqrt()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nothing_to_draw_before_the_first_step() {
        assert_eq!(EyeLerp::default().at(0.0), None);
    }

    #[test]
    fn the_eye_moves_on_every_frame_between_two_steps() {
        let mut eye = EyeLerp::default();
        eye.push([0.0, 0.0, 5.0], 0.004);
        eye.push([0.4, 0.0, 5.0], 0.004);
        // Frames at 0, 1/3 and 2/3 of the way to the next step.
        let xs: Vec<f32> = [0.0, 0.004 / 3.0, 0.008 / 3.0]
            .iter()
            .map(|&s| eye.at(s).unwrap()[0])
            .collect();
        assert!((xs[0] - 0.0).abs() < 1e-6);
        assert!((xs[1] - 0.4 / 3.0).abs() < 1e-5);
        assert!((xs[2] - 0.8 / 3.0).abs() < 1e-5);
    }

    #[test]
    fn a_late_frame_never_draws_past_the_newest_step() {
        let mut eye = EyeLerp::default();
        eye.push([0.0, 0.0, 0.0], 0.004);
        eye.push([1.0, 0.0, 0.0], 0.004);
        assert_eq!(eye.at(0.05), Some([1.0, 0.0, 0.0]));
    }

    #[test]
    fn a_teleport_is_taken_at_once_rather_than_slid_through() {
        let mut eye = EyeLerp::default();
        eye.push([0.0, 0.0, 0.0], 0.004);
        eye.push([50.0, 0.0, 0.0], 0.004);
        assert_eq!(eye.at(0.0), Some([50.0, 0.0, 0.0]));
    }

    #[test]
    fn a_snap_leaves_nothing_to_interpolate_from() {
        let mut eye = EyeLerp::default();
        eye.push([0.0, 0.0, 0.0], 0.004);
        eye.push([1.0, 0.0, 0.0], 0.004);
        eye.snap([1.5, 0.0, 0.0]);
        assert_eq!(eye.at(0.0), Some([1.5, 0.0, 0.0]));
        assert_eq!(eye.latest(), Some([1.5, 0.0, 0.0]));
    }
}
