//! The map assembling itself.
//!
//! The loading animation *is* the level: cubes rise into place along a front
//! that sweeps outward from the middle of the map, lit at the frontier and
//! settling into their normal shading behind it. A port of the browser's
//! `reveal.ts`, which is where the look and every constant here comes from.
//!
//! ## It is not driven by load progress, in either client
//!
//! `reveal.ts`'s own header says it is "driven by the real load progress", and
//! the call site in `HorribleAssaultPanel.tsx` does the opposite and explains
//! why: *"Runs on its own clock rather than on load progress: the map is already
//! here by now, and the point of the animation is to show the world arriving,
//! not to stall until it has."* The call site is the one that is true.
//!
//! That contradiction is worth knowing because it is the only thing that made
//! this portable at all. Driven by real progress it would need the native
//! client's startup restructured — the window does not exist until the map is
//! fetched and meshed. On its own clock it is a float and a shader.
//!
//! ## Where the state lives
//!
//! The progress rides in the **camera's** uniform (`CameraUniform::reveal`), not
//! in a uniform of its own, and that is what keeps the weapon in your hands out
//! of it. The world and the view model share one pipeline and one shader and
//! differ only in which camera bind group is bound, so the view model's own
//! uniform simply carries a finished reveal. No branch, nothing to remember.

/// How long the build takes. The browser's `REVEAL_MS`.
const REVEAL_SECONDS: f32 = 2.6;

/// How wide the moving front is, in units of overall progress.
///
/// Each vertex animates over this band, so the build is a travelling wave rather
/// than a line: at any instant roughly this fraction of the map is mid-flight.
/// Shared with the shader, which reads it from the uniform rather than
/// hardcoding a second copy.
pub const BAND: f32 = 0.14;

/// The build-in's clock, and the shape it sweeps over.
#[derive(Debug, Clone, Copy)]
pub struct Reveal {
    elapsed: f32,
    centre: [f32; 2],
    radius: f32,
    height: f32,
    enabled: bool,
}

impl Default for Reveal {
    fn default() -> Reveal {
        Reveal {
            elapsed: 0.0,
            centre: [0.0, 0.0],
            radius: 1.0,
            height: 1.0,
            enabled: true,
        }
    }
}

impl Reveal {
    /// Aim at a map: its centre, its extent, and how tall it stands.
    ///
    /// The browser passes `extent * 1.05` — a radius slightly larger than the
    /// map — so the far corner is not still mid-rise when the clock runs out.
    pub fn fit(&mut self, centre: [f32; 2], radius: f32, height: f32) {
        self.centre = centre;
        self.radius = radius.max(0.001);
        self.height = height.max(0.001);
    }

    /// Skip straight to the finished world.
    ///
    /// The browser does this for `prefers-reduced-motion`. This client cannot
    /// read that preference — see the note in the two-clients doc — so it is
    /// exposed for the view model's uniform and for tests, and is the state a
    /// finished build settles into anyway.
    pub fn complete(&mut self) {
        self.elapsed = REVEAL_SECONDS;
    }

    /// Turn the build-in off entirely, for a client that should not animate.
    pub fn disable(&mut self) {
        self.enabled = false;
        self.complete();
    }

    pub fn advance(&mut self, dt: f32) {
        if self.elapsed < REVEAL_SECONDS {
            self.elapsed = (self.elapsed + dt).min(REVEAL_SECONDS);
        }
    }

    /// Whether there is any animation left to draw.
    ///
    /// The shader asks the same question of the uniform it is handed, and skips
    /// the ordering function entirely once the answer is yes — otherwise a hash,
    /// a length and a floor are evaluated per vertex per frame for the rest of
    /// the match to compute a number that is always 1. A two-second animation
    /// should not be a permanent tax.
    pub fn finished(&self) -> bool {
        !self.enabled || self.elapsed >= REVEAL_SECONDS
    }

    /// `[progress, centre_x, centre_y, radius]`, as the shader reads it.
    ///
    /// Progress is driven **past 1 by one band width** so the last vertices
    /// finish their own animation: at exactly 1.0 the far corner is still
    /// mid-rise, which is how a build-in ends with a visible seam.
    pub fn uniform(&self) -> [f32; 4] {
        let t = (self.elapsed / REVEAL_SECONDS).clamp(0.0, 1.0);
        [
            t * (1.0 + BAND),
            self.centre[0],
            self.centre[1],
            self.radius,
        ]
    }

    /// The map's height, which rides in the camera's spare `params` slot.
    pub fn height(&self) -> f32 {
        self.height
    }

    /// A reveal that is already over. The view model's, and what a caller with
    /// nothing to animate should hand the renderer.
    pub fn done() -> Reveal {
        let mut r = Reveal::default();
        r.complete();
        r
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_fresh_reveal_starts_at_nothing_and_ends_past_one() {
        let mut r = Reveal::default();
        assert_eq!(r.uniform()[0], 0.0);
        assert!(!r.finished());
        r.advance(REVEAL_SECONDS);
        assert!(r.finished());
        // Past 1 by a band, or the far corner never lands.
        assert!(
            (r.uniform()[0] - (1.0 + BAND)).abs() < 1e-6,
            "ended at {}",
            r.uniform()[0]
        );
    }

    #[test]
    fn the_clock_does_not_run_past_the_end() {
        // A long frame at the end must not push progress beyond `1 + BAND` and
        // start discarding geometry from the far side of the comparison.
        let mut r = Reveal::default();
        r.advance(100.0);
        assert!((r.uniform()[0] - (1.0 + BAND)).abs() < 1e-6);
    }

    #[test]
    fn a_disabled_reveal_is_finished_immediately() {
        let mut r = Reveal::default();
        r.disable();
        assert!(r.finished());
        assert!((r.uniform()[0] - (1.0 + BAND)).abs() < 1e-6);
    }

    #[test]
    fn fit_refuses_a_degenerate_shape() {
        // A radius of zero divides by zero in the shader, and the whole map
        // resolves to build order 1 — which is a world that appears all at once
        // at the very end, looking like the effect simply failed.
        let mut r = Reveal::default();
        r.fit([10.0, 10.0], 0.0, 0.0);
        assert!(r.uniform()[3] > 0.0);
        assert!(r.height() > 0.0);
    }

    #[test]
    fn the_centre_is_carried_through_untouched() {
        let mut r = Reveal::default();
        r.fit([32.0, 48.0], 40.0, 12.0);
        let u = r.uniform();
        assert_eq!([u[1], u[2]], [32.0, 48.0]);
        assert_eq!(u[3], 40.0);
    }

    #[test]
    fn a_finished_reveal_is_what_the_view_model_gets() {
        assert!(Reveal::done().finished());
    }
}
