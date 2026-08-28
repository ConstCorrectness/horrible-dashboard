//! Where the eye is, and the matrix that follows from it.
//!
//! Kept apart from the renderer because it is pure maths with no device in it,
//! which means it can be tested — and the one thing here that is easy to get
//! silently wrong is a coordinate convention, which no amount of looking at the
//! screen reliably catches. A world drawn with `y` and `z` transposed still looks
//! like a world.
//!
//! **Three conventions meet here and all three are load-bearing:**
//!
//! 1. The cube grid is `(x, y)` with `z` as height, and the wire speaks it.
//! 2. The renderer is **y-up**: `render.x = cube.x`, `render.y = height`,
//!    `render.z = cube.y`. The mesher already emits vertices this way, so the
//!    camera has to agree or the player walks perpendicular to the geometry.
//! 3. `wgpu` clip space is **Y-up with z in `0..1`**. In glam 0.33's vocabulary
//!    that is the `directx` convention, **not** `vulkan` — which is also `0..1`
//!    but *Y-down*, and would render the whole world upside down while compiling
//!    perfectly. `opengl` is the other trap: it is Y-up but `-1..1`, which
//!    renders right way up and quietly throws away half the depth buffer.
//!
//! Yaw and pitch follow the server's convention, since they are sent back to it
//! on every command: yaw is degrees clockwise from +x about the vertical, pitch
//! is degrees up.

use glam::camera::rh::proj::directx::perspective;
use glam::camera::rh::view::look_to_mat4;
use glam::{Mat4, Vec3};

/// How far the near plane sits. Small, because a shooter's own weapon and the
/// wall you are pressed against both live very close to the eye — but not
/// arbitrarily small, since the depth buffer's precision is spent mostly between
/// here and a few multiples of it.
pub const NEAR: f32 = 0.05;

/// The far plane. The largest map is 512 cubes across, so a diagonal is ~725;
/// 2000 covers every map with room to spare and still leaves plenty of depth
/// precision given a 0.05 near plane.
pub const FAR: f32 = 2000.0;

#[derive(Debug, Clone, Copy)]
pub struct Camera {
    /// Eye position, in **cube** coordinates (x, y, height).
    pub x: f32,
    pub y: f32,
    pub z: f32,
    /// Degrees, clockwise from +x. Matches the wire.
    pub yaw: f32,
    /// Degrees, positive looking up. Matches the wire.
    pub pitch: f32,
    /// Vertical field of view in degrees.
    pub fov: f32,
}

impl Default for Camera {
    fn default() -> Camera {
        Camera {
            x: 0.0,
            y: 0.0,
            z: 0.0,
            yaw: 0.0,
            pitch: 0.0,
            // The same default the browser client uses, so switching between them
            // does not silently change how fast the game feels — a wider field of
            // view reads as faster movement at identical speeds.
            fov: 75.0,
        }
    }
}

impl Camera {
    /// The unit vector the eye is looking along, in render space.
    pub fn forward(&self) -> Vec3 {
        let yaw = self.yaw.to_radians();
        let pitch = self.pitch.to_radians();
        let cos_pitch = pitch.cos();
        // x and z from yaw; y from pitch. Cube `y` becomes render `z`, so the
        // sine of the yaw lands on z rather than on y.
        Vec3::new(yaw.cos() * cos_pitch, pitch.sin(), yaw.sin() * cos_pitch)
    }

    /// Eye position in render space (y-up).
    pub fn eye(&self) -> Vec3 {
        Vec3::new(self.x, self.z, self.y)
    }

    /// The view matrix on its own: render space into camera space.
    ///
    /// Split out of `view_projection` because the view model needs its
    /// **inverse**. Its vertices are already in camera space — that is how it is
    /// parented to the eye without a scene graph — but the lighting rig's
    /// directions and the sun's shadow map are both in *world* space, so
    /// something has to carry a camera-space vertex back out to where the lights
    /// are. Shading a camera-space normal against a world-space sun is not a
    /// subtle error: the weapon's lit side stops moving when you turn, which is
    /// the one thing that tells the eye the gun is in the same room as the walls.
    pub fn view(&self) -> Mat4 {
        look_to_mat4(self.eye(), self.forward(), Vec3::Y)
    }

    /// Camera space back into render space, for the view model's lighting.
    pub fn camera_to_world(&self) -> Mat4 {
        // An inverse rather than a transpose-and-negate by hand: a view matrix is
        // a rigid transform so the two agree, and `inverse` cannot be got subtly
        // backwards at four in the morning.
        self.view().inverse()
    }

    /// The combined view-projection matrix the shader multiplies by.
    ///
    /// `aspect` guards against zero: a window minimised to zero width produces a
    /// division by zero here, and NaNs in a uniform buffer take the whole frame
    /// with them rather than failing anywhere near the cause.
    pub fn view_projection(&self, width: u32, height: u32) -> Mat4 {
        let aspect = if height == 0 {
            1.0
        } else {
            (width.max(1) as f32) / (height as f32)
        };
        // `directx`, not `vulkan` or `opengl` — see the module header.
        let proj = perspective(self.fov.to_radians(), aspect, NEAR, FAR);
        proj * self.view()
    }

    /// Apply a mouse movement, in raw device units.
    ///
    /// Pitch is **clamped just short of vertical** rather than at it: exactly
    /// ±90° makes the forward vector parallel to the up vector, and `look_to_rh`
    /// has no basis to build from that — the view matrix fills with NaN and the
    /// screen goes blank at the moment you look straight up.
    pub fn apply_look(&mut self, dx: f32, dy: f32, sensitivity: f32) {
        self.yaw = (self.yaw + dx * sensitivity).rem_euclid(360.0);
        self.pitch = (self.pitch - dy * sensitivity).clamp(-89.9, 89.9);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zero_yaw_looks_along_positive_x() {
        let f = Camera::default().forward();
        assert!((f.x - 1.0).abs() < 1e-6, "{f:?}");
        assert!(f.y.abs() < 1e-6);
        assert!(f.z.abs() < 1e-6);
    }

    #[test]
    fn ninety_degrees_of_yaw_looks_along_cube_y() {
        // Cube `y` is render `z`. If this came out on render `y` the player would
        // be looking at the ceiling while walking north, which is the transposed
        // -axis bug that a screenshot does not reveal.
        let cam = Camera {
            yaw: 90.0,
            ..Default::default()
        };
        let f = cam.forward();
        assert!(f.x.abs() < 1e-6, "{f:?}");
        assert!(f.y.abs() < 1e-6, "{f:?}");
        assert!((f.z - 1.0).abs() < 1e-6, "{f:?}");
    }

    #[test]
    fn positive_pitch_looks_up() {
        let cam = Camera {
            pitch: 45.0,
            ..Default::default()
        };
        assert!(cam.forward().y > 0.5);
    }

    #[test]
    fn the_eye_maps_height_onto_render_y() {
        let cam = Camera {
            x: 3.0,
            y: 5.0,
            z: 11.0,
            ..Default::default()
        };
        assert_eq!(cam.eye(), Vec3::new(3.0, 11.0, 5.0));
    }

    #[test]
    fn looking_straight_up_does_not_produce_a_nan_matrix() {
        // The clamp is the whole point: at exactly 90 the forward vector is
        // parallel to up, `look_to_rh` divides by a zero-length cross product,
        // and every vertex this frame becomes NaN.
        let mut cam = Camera::default();
        cam.apply_look(0.0, -100000.0, 1.0);
        assert!(cam.pitch < 90.0);
        let m = cam.view_projection(800, 600);
        assert!(
            m.to_cols_array().iter().all(|v| v.is_finite()),
            "view-projection went non-finite at pitch {}",
            cam.pitch
        );
    }

    #[test]
    fn a_zero_height_window_does_not_divide_by_zero() {
        let m = Camera::default().view_projection(0, 0);
        assert!(m.to_cols_array().iter().all(|v| v.is_finite()));
    }

    #[test]
    fn yaw_wraps_rather_than_growing_without_bound() {
        let mut cam = Camera::default();
        cam.apply_look(1000.0, 0.0, 1.0);
        assert!((0.0..360.0).contains(&cam.yaw), "{}", cam.yaw);
    }

    #[test]
    fn the_projection_puts_the_near_plane_at_zero_depth_not_minus_one() {
        // wgpu clip space is 0..1. `perspective_rh_gl` would put the near plane
        // at -1, which renders — it just throws away half the depth buffer and
        // makes distant geometry fight.
        let cam = Camera::default();
        let m = cam.view_projection(800, 600);
        // A point one near-plane ahead of the eye, in render space.
        let p = m * glam::Vec4::new(NEAR, 0.0, 0.0, 1.0);
        let ndc_z = p.z / p.w;
        assert!(ndc_z.abs() < 1e-3, "near plane at ndc z {ndc_z}");
    }
}
