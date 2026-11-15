//! The main menu's live backdrop: where the camera is in the map behind it.
//!
//! The shots are the map's benchmark framings (`bench-paths.json`), which
//! somebody already chose as the views worth looking at, each held for a while
//! with a slow pan across it. Between them the picture dips to dark and cuts
//! rather than gliding: framings are rooms apart, and a straight line between
//! two of them goes through whatever walls are in the way.
//!
//! Free of the GPU, so the timing is tested headless; `front_gpu.rs` draws it.

use crate::bench::Framing;
use crate::camera::Camera;

/// Seconds on one framing, the dips at either end included.
pub const HOLD: f32 = 14.0;
/// Seconds the picture takes to go dark, and again to come back, at a cut.
pub const DIP: f32 = 0.9;
/// How far the camera pans across a framing, degrees, end to end.
pub const PAN: f32 = 24.0;
/// How far the camera drifts sideways along a framing, cubes, end to end.
pub const DRIFT: f32 = 3.0;

/// The camera flying a map's framings, and how dark the cut makes it.
#[derive(Debug, Clone)]
pub struct Flight {
    path: Vec<Framing>,
}

/// One instant of a flight.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Shot {
    /// Cube space, as `Camera` keeps it: `z` is height.
    pub eye: [f32; 3],
    /// Degrees, 0 = +x.
    pub yaw: f32,
    pub pitch: f32,
    /// 0 clear to 1 black: the dip at a cut.
    pub dark: f32,
}

impl Flight {
    /// `None` for a map with no framings to fly.
    pub fn new(path: Vec<Framing>) -> Option<Flight> {
        (!path.is_empty()).then_some(Flight { path })
    }

    /// Where the camera is `t` seconds in. Loops through the framings.
    pub fn at(&self, t: f32) -> Shot {
        let t = t.max(0.0);
        let index = (t / HOLD) as usize % self.path.len();
        let local = t % HOLD;
        let f = &self.path[index];
        // -1 at the start of the hold to +1 at its end.
        let along = local / HOLD * 2.0 - 1.0;
        let yaw_rad = f.yaw.to_radians();
        // Sideways, across the view: what makes a still frame read as a shot.
        let (side_x, side_y) = (-yaw_rad.sin(), yaw_rad.cos());
        let drift = along * DRIFT * 0.5;
        let dark = if local < DIP {
            1.0 - local / DIP
        } else if local > HOLD - DIP {
            (local - (HOLD - DIP)) / DIP
        } else {
            0.0
        };
        Shot {
            eye: [
                f.eye[0] + side_x * drift,
                f.eye[1] + side_y * drift,
                f.eye[2],
            ],
            yaw: f.yaw + along * PAN * 0.5,
            pitch: f.pitch,
            dark: smooth(dark),
        }
    }

    /// `at`, as the camera the renderer takes.
    pub fn camera(&self, t: f32, base: Camera) -> (Camera, f32) {
        let shot = self.at(t);
        let mut camera = base;
        (camera.x, camera.y, camera.z) = (shot.eye[0], shot.eye[1], shot.eye[2]);
        camera.yaw = shot.yaw;
        camera.pitch = shot.pitch;
        (camera, shot.dark)
    }
}

fn smooth(t: f32) -> f32 {
    let t = t.clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn framing(x: f32, yaw: f32) -> Framing {
        Framing {
            label: format!("at {x}"),
            eye: [x, 10.0, 4.0],
            yaw,
            pitch: -5.0,
            seconds: 4.0,
            spin: 0.0,
        }
    }

    fn flight() -> Flight {
        Flight::new(vec![framing(0.0, 0.0), framing(100.0, 90.0)]).unwrap()
    }

    #[test]
    fn a_map_with_no_framings_has_no_flight() {
        assert!(Flight::new(Vec::new()).is_none());
    }

    #[test]
    fn a_framing_is_held_and_panned_then_the_next_follows_and_it_loops() {
        let f = flight();
        let mid = f.at(HOLD * 0.5);
        assert_eq!(
            (mid.eye[0], mid.yaw, mid.dark),
            (0.0, 0.0, 0.0),
            "the framing itself, mid-hold"
        );
        let later = f.at(HOLD * 0.75);
        assert!(
            later.yaw > mid.yaw && later.eye[1] > mid.eye[1],
            "panning and drifting"
        );
        assert_eq!(f.at(HOLD * 1.5).eye[0], 100.0);
        assert_eq!(f.at(HOLD * 2.5).eye[0], 0.0, "round again");
    }

    #[test]
    fn a_cut_happens_in_the_dark() {
        let f = flight();
        assert!(f.at(HOLD - 0.001).dark > 0.99, "dark just before the cut");
        assert!(f.at(HOLD + 0.001).dark > 0.99, "and just after it");
        assert_eq!(f.at(HOLD * 0.5).dark, 0.0);
        // Never a jump while anything can be seen.
        let mut t = 0.0;
        while t < HOLD * 2.0 {
            let (a, b) = (f.at(t), f.at(t + 0.01));
            let moved = (a.eye[0] - b.eye[0]).abs() + (a.eye[1] - b.eye[1]).abs();
            if moved > 1.0 {
                assert!(
                    a.dark > 0.99 && b.dark > 0.99,
                    "a jump at {t} with the picture up"
                );
            }
            t += 0.01;
        }
    }
}
