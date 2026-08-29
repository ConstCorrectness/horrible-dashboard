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
use glam::{Mat4, Quat, Vec3};

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
    /// Degrees, clockwise about the view axis.
    ///
    /// **Not on the wire, and there is no key that sets it.** Yaw and pitch are
    /// sent to the server on every command; roll is a purely local tilt that
    /// only `Shake` ever writes, and only into the *copy* handed to the
    /// renderer. It lives on `Camera` rather than in the shake because the view
    /// matrix is the only thing that can express it.
    pub roll: f32,
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
            roll: 0.0,
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

    /// Which way is up for the view matrix.
    ///
    /// `Vec3::Y` until something rolls the camera. Rotating up *about the
    /// forward axis* is what makes a roll a roll rather than a yaw: any other
    /// axis moves where the eye is looking, which would make a cosmetic tilt
    /// into an aim modifier.
    fn up(&self) -> Vec3 {
        if self.roll == 0.0 {
            return Vec3::Y;
        }
        Quat::from_axis_angle(self.forward(), self.roll.to_radians()) * Vec3::Y
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
        look_to_mat4(self.eye(), self.forward(), self.up())
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

    /// This camera with a shake applied — **a copy, never a mutation**.
    ///
    /// The whole safety argument for screen shake lives in that word. See
    /// `Shake`: the camera the game runs on is the one whose angles go on the
    /// wire and out of the barrel, and the one the renderer draws is this one.
    ///
    /// Pitch is **re-clamped**, and it has to be: the camera's own clamp stops
    /// at 89.9° precisely because 90° makes forward parallel to up and fills the
    /// view matrix with NaN. Adding an unclamped shake to a player already
    /// looking straight up walks past it, and the screen goes blank on the frame
    /// a grenade lands while you are looking at the sky.
    pub fn shaken(&self, shake: &Shake) -> Camera {
        let (yaw, pitch, roll) = shake.offsets();
        Camera {
            yaw: self.yaw + yaw,
            pitch: (self.pitch + pitch).clamp(-89.9, 89.9),
            roll: self.roll + roll,
            ..*self
        }
    }
}

/// How fast trauma bleeds away, in units per second.
///
/// **Linear, not exponential.** An exponential decay is asymptotic: the camera
/// would never actually stop, it would only shake by amounts too small to see —
/// and `Shake::active` would then answer "yes" forever, keeping a rolled camera
/// copy alive for the rest of the match. Linear reaches exactly zero, which is
/// the state a camera at rest is supposed to be in. `effects.rs` fades its
/// tracers the same way and for the same reason.
const TRAUMA_DECAY: f32 = 1.8;

/// Where the noise clock folds back, in seconds. See `Shake::update`.
const CLOCK_WRAP: f32 = 600.0;

/// The largest angles full trauma can reach, in degrees.
///
/// Roll is the biggest of the three because it is the one the eye reads as
/// *impact* rather than as bad mouse control: yaw and pitch look like the view
/// being nudged off aim, which in a shooter is the one sensation a cosmetic
/// effect must not counterfeit. These are small on purpose — the shake is felt,
/// not aimed at.
const SHAKE_YAW: f32 = 2.0;
const SHAKE_PITCH: f32 = 1.6;
const SHAKE_ROLL: f32 = 3.5;

/// Trauma one of our own shots is worth, from the **served** `kickback`.
///
/// Served rather than tabulated, for the same reason `kick_vector` and
/// `weapon_voice` read it: a balance change to the gun then moves how it feels
/// with it, and a client cannot come to disagree with the server about which
/// weapon is the heavy one.
///
/// **A weapon with no kickback shakes nothing.** `kickback <= 0` is how this
/// client already tells a knife from a gun in two other places (`trace::
/// kick_vector`, `audio::weapon_voice`), and a swing that jolted the camera
/// would advertise a weapon whose entire value is that it is quiet.
///
/// The `1.5` power is what lets one curve serve both ends: an automatic adds a
/// small amount many times a second and settles into a rumble, while a sniper
/// adds most of a full jolt once. A linear ramp made the rifle shake as hard as
/// the sniper within three rounds of holding the trigger.
pub fn fire_trauma(kickback: f32) -> f32 {
    if kickback <= 0.0 {
        return 0.0;
    }
    0.045 + 0.5 * (kickback / 10.0).clamp(0.0, 1.0).powf(1.5)
}

/// Trauma for a hit **taken**, from what it cost in health and armour together.
///
/// A square root, so the first few points of damage carry most of the jolt: the
/// information the shake conveys is *that you are being shot*, which a scratch
/// and a near-fatal hit both mean, and a linear ramp leaves the scratch silent.
pub fn damage_trauma(amount: f32) -> f32 {
    if amount <= 0.0 {
        return 0.0;
    }
    (amount / 100.0).clamp(0.0, 1.0).sqrt() * 0.8
}

/// Trauma from a detonation, by how far away it went off.
///
/// Reaching **twice** the blast radius rather than exactly the radius, and
/// deliberately: the shake is not a damage indicator, and stopping it precisely
/// where the damage stops would teach the radius by accident — the one number
/// `effects::detonate` is careful to draw honestly. Past 2r it is zero, so a
/// grenade across the map costs nothing.
///
/// Squared falloff keeps it local: at the rim of the blast it is a quarter of
/// what it is at the centre.
pub fn blast_trauma(distance: f32, radius: f32) -> f32 {
    if radius <= 0.0 {
        return 0.0;
    }
    let t = (1.0 - distance / (radius * 2.0)).clamp(0.0, 1.0);
    t * t * 0.9
}

/// A decaying camera shake.
///
/// **Trauma, squared.** The stored value climbs linearly with what happened to
/// you and the *displacement* is its square, so a pistol round barely registers
/// while a grenade at your feet throws the view — with one number and no table
/// of cases. This is the standard trick and the reason it is worth the extra
/// multiply: linear trauma makes small hits too loud and large ones too quiet at
/// the same time, and no single scale factor fixes both.
///
/// **Nothing here ever touches the camera the game runs on.** The shake is
/// applied by `Camera::shaken`, which returns a *copy*, and only the renderer is
/// given that copy. The real camera is what `view_angles` puts on the wire and
/// what a shot's ray is built from, so a shake that wrote into it would not be a
/// visual effect — it would be a client-side aim modifier that the server would
/// faithfully honour. That is the failure this separation exists to prevent, and
/// it is silent: the game would simply feel like it had recoil nobody could tune.
#[derive(Debug, Clone, Copy, Default)]
pub struct Shake {
    /// 0..1. Squared before it becomes an angle.
    trauma: f32,
    /// Seconds, for the noise. A free-running clock rather than a per-event age,
    /// so a second impact during the first does not restart the waveform — which
    /// would read as the shake *stopping* for a frame at the moment it was told
    /// to get worse.
    t: f32,
}

impl Shake {
    /// Something happened. Additive, capped at full.
    ///
    /// Capped rather than summed because trauma is squared on the way out: two
    /// grenades adding to 2.0 would displace four times as far as one, which at
    /// these angles puts the horizon somewhere behind the player.
    pub fn add(&mut self, amount: f32) {
        if amount > 0.0 {
            self.trauma = (self.trauma + amount).clamp(0.0, 1.0);
        }
    }

    /// Advance the clock and bleed off trauma.
    pub fn update(&mut self, dt: f32) {
        self.t += dt;
        self.trauma = (self.trauma - TRAUMA_DECAY * dt).max(0.0);
        // Keep the clock bounded, **and only while nothing is being drawn**.
        //
        // `wobble` multiplies `t` by up to ~141, so an f32's seven significant
        // digits start quantising the phase once `t` reaches the thousands: the
        // per-frame step through the sine shrinks toward the representable
        // spacing and the shake goes from smooth to steppy over a long session,
        // with nothing anywhere to blame it on.
        //
        // Subtracted rather than zeroed, so two separate impacts still land on
        // different phases and no two shakes are identical; and gated on trauma
        // being *exactly* zero, because a wrap during a live shake would be a
        // visible jump. At zero there is nothing on screen to jump.
        if self.trauma == 0.0 && self.t > CLOCK_WRAP {
            self.t -= CLOCK_WRAP;
        }
    }

    /// Whether there is anything to apply. Lets the caller skip building a
    /// second camera on the overwhelming majority of frames.
    pub fn active(&self) -> bool {
        self.trauma > 0.0
    }

    /// This instant's `(yaw, pitch, roll)` offsets, in degrees.
    pub fn offsets(&self) -> (f32, f32, f32) {
        if self.trauma <= 0.0 {
            return (0.0, 0.0, 0.0);
        }
        let k = self.trauma * self.trauma;
        // Three unrelated seeds, and **none of them zero**: `wobble` is a sum of
        // sines, so seed 0 is identically 0 at t = 0. A free-running clock means
        // that instant is almost never sampled in play — and is exactly the one
        // a test samples, which is how a shake that works can look like a shake
        // that does nothing.
        (
            k * SHAKE_YAW * wobble(self.t, 4.1),
            k * SHAKE_PITCH * wobble(self.t, 11.7),
            k * SHAKE_ROLL * wobble(self.t, 23.4),
        )
    }
}

/// Smooth pseudo-noise in `-1..=1`, sampled on a **clock rather than a frame
/// counter** so the shake looks the same at 30 fps and at 240.
///
/// Three sines whose periods never re-align, which is enough to stop the eye
/// counting the wobble — one sine alone reads as a pendulum, not an impact. The
/// weights sum to exactly 1 so the result cannot leave the range the callers
/// scale against.
///
/// **The frequencies are deliberately all below ~23 Hz.** A component faster
/// than half the frame rate does not read as a faster shake; it aliases, and a
/// 40 Hz wobble sampled at 60 fps turns into a slow, wrong-looking crawl that
/// gets *worse* the smoother the machine is running. That is the one failure
/// here nobody would think to look for in this function.
fn wobble(t: f32, seed: f32) -> f32 {
    (t * 94.2 + seed).sin() * 0.55
        + (t * 141.4 + seed * 1.7).sin() * 0.25
        + (t * 59.7 + seed * 2.9).sin() * 0.20
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

    #[test]
    fn a_knife_swing_shakes_nothing() {
        // `kickback <= 0` is how this client tells a knife from a gun in three
        // places now. A camera jolt would announce the one weapon whose whole
        // value is that carrying it is silent.
        assert_eq!(fire_trauma(0.0), 0.0);
        assert_eq!(fire_trauma(-1.0), 0.0);
        assert!(fire_trauma(1.2) > 0.0);
    }

    #[test]
    fn a_sustained_rifle_settles_rather_than_saturating() {
        // The failure this guards is a rifle that reaches full trauma after
        // three rounds and then shakes exactly as hard as a grenade for as long
        // as the trigger is held.
        let mut shake = Shake::default();
        // 700 rpm for two full seconds.
        let dt = 1.0 / 240.0;
        let mut since = 0.0;
        for _ in 0..480 {
            since += dt;
            if since >= 60.0 / 700.0 {
                since = 0.0;
                shake.add(fire_trauma(1.6));
            }
            shake.update(dt);
        }
        let (_, _, roll) = shake.offsets();
        assert!(
            roll.abs() < SHAKE_ROLL * 0.45,
            "a held rifle reached {roll} of a {SHAKE_ROLL} maximum"
        );
    }

    #[test]
    fn a_heavier_weapon_jolts_harder() {
        assert!(fire_trauma(9.5) > fire_trauma(1.6));
        assert!(fire_trauma(1.6) > fire_trauma(1.2));
        // And no single shot is worth a whole grenade.
        assert!(fire_trauma(9.5) < blast_trauma(0.0, 8.0));
    }

    #[test]
    fn a_blast_reaches_further_than_it_hurts_and_then_stops() {
        let r = 8.0;
        assert!(blast_trauma(0.0, r) > blast_trauma(r, r));
        // Still felt at the rim, because the shake is not the damage radius.
        assert!(blast_trauma(r, r) > 0.0);
        // Gone at twice it, so a grenade across the map costs nothing.
        assert_eq!(blast_trauma(2.0 * r, r), 0.0);
        assert_eq!(blast_trauma(50.0, r), 0.0);
        // A radius of zero is a server that sent one; it must not divide.
        assert_eq!(blast_trauma(1.0, 0.0), 0.0);
    }

    #[test]
    fn a_scratch_is_felt_and_a_heavy_hit_is_not_much_more() {
        // The square root's job: the message is "you are being shot", which a
        // 5 hp graze means just as much as a 90 hp one.
        assert!(damage_trauma(5.0) > 0.15);
        assert!(damage_trauma(90.0) > damage_trauma(25.0));
        assert!(damage_trauma(90.0) < 4.0 * damage_trauma(25.0));
        assert_eq!(damage_trauma(0.0), 0.0);
    }

    #[test]
    fn a_roll_tilts_the_view_without_moving_where_it_looks() {
        // The whole point of rotating up about *forward*: the aim must not move.
        // If a roll leaked into the look direction, a cosmetic tilt would become
        // an aim modifier — and one that only shows up while being shot at.
        let straight = Camera {
            yaw: 40.0,
            pitch: 15.0,
            ..Default::default()
        };
        let rolled = Camera {
            roll: 8.0,
            ..straight
        };
        assert!((rolled.forward() - straight.forward()).length() < 1e-6);
        // But the matrix does differ, or the roll is doing nothing at all.
        let a = straight.view().to_cols_array();
        let b = rolled.view().to_cols_array();
        assert!(a.iter().zip(b.iter()).any(|(x, y)| (x - y).abs() > 1e-3));
    }

    #[test]
    fn the_noise_clock_stays_bounded_but_only_folds_while_at_rest() {
        // Unbounded, `t * 141` eventually outruns an f32's precision and the
        // shake quietly goes steppy. Folded at the wrong moment, it is a visible
        // jump mid-shake instead.
        let mut shake = Shake::default();
        for _ in 0..80_000 {
            shake.update(0.016);
        }
        assert!(shake.t <= CLOCK_WRAP, "clock ran away to {}", shake.t);

        // With trauma up, the clock is left alone however far past the fold it
        // is — the offsets must stay continuous.
        shake.t = CLOCK_WRAP + 5.0;
        shake.add(1.0);
        let before = shake.offsets();
        shake.update(0.001);
        let after = shake.offsets();
        assert!(shake.t > CLOCK_WRAP, "folded mid-shake");
        for (a, b) in [before.0, before.1, before.2]
            .iter()
            .zip([after.0, after.1, after.2].iter())
        {
            assert!((a - b).abs() < 0.5, "the shake jumped: {a} to {b}");
        }
    }

    #[test]
    fn trauma_decays_to_exactly_zero() {
        // Not "to nearly zero". An exponential never arrives, and `active`
        // would then keep answering yes for the rest of the match.
        let mut shake = Shake::default();
        shake.add(1.0);
        assert!(shake.active());
        for _ in 0..120 {
            shake.update(0.016);
        }
        assert!(!shake.active());
        assert_eq!(shake.offsets(), (0.0, 0.0, 0.0));
    }

    #[test]
    fn trauma_is_squared_so_a_small_hit_is_more_than_proportionally_small() {
        // A tenth of the trauma must be a hundredth of the shake, not a tenth.
        // This is the entire reason a pistol and a grenade can share one scale.
        let mut small = Shake::default();
        small.add(0.1);
        let mut large = Shake::default();
        large.add(1.0);
        // Same clock, so the noise term is identical and only the scale differs.
        let (sy, _, _) = small.offsets();
        let (ly, _, _) = large.offsets();
        assert!(ly.abs() > 1e-6, "the reference shake produced nothing");
        let ratio = sy.abs() / ly.abs();
        assert!((ratio - 0.01).abs() < 1e-3, "ratio {ratio}");
    }

    #[test]
    fn trauma_cannot_be_stacked_past_full() {
        let mut shake = Shake::default();
        for _ in 0..20 {
            shake.add(0.5);
        }
        let (yaw, pitch, roll) = shake.offsets();
        assert!(yaw.abs() <= SHAKE_YAW + 1e-6, "{yaw}");
        assert!(pitch.abs() <= SHAKE_PITCH + 1e-6, "{pitch}");
        assert!(roll.abs() <= SHAKE_ROLL + 1e-6, "{roll}");
    }

    #[test]
    fn the_noise_stays_inside_the_range_its_callers_scale_against() {
        // The three weights sum to 1, so this is arithmetic rather than luck —
        // but it is arithmetic somebody will later "tidy" by changing a weight.
        let mut worst: f32 = 0.0;
        for i in 0..20000 {
            let v = wobble(i as f32 * 0.0007, 3.5);
            worst = worst.max(v.abs());
        }
        assert!(worst <= 1.0 + 1e-6, "{worst}");
    }

    #[test]
    fn shaking_does_not_move_the_camera_the_game_runs_on() {
        // The security argument, as a test. `shaken` returns a copy; if it ever
        // becomes a mutation, the shake starts steering the shots.
        let cam = Camera {
            yaw: 30.0,
            pitch: 10.0,
            ..Default::default()
        };
        let mut shake = Shake::default();
        shake.add(1.0);
        let drawn = cam.shaken(&shake);
        assert_eq!(cam.yaw, 30.0);
        assert_eq!(cam.pitch, 10.0);
        assert_eq!(cam.roll, 0.0);
        assert!((drawn.yaw - cam.yaw).abs() > 1e-6, "the shake did nothing");
    }

    #[test]
    fn a_shake_while_looking_straight_up_does_not_blank_the_screen() {
        // Past 90° of pitch, forward is parallel to up and every vertex this
        // frame becomes NaN. The camera's own clamp stops at 89.9; adding an
        // unclamped shake to it walks straight past.
        let cam = Camera {
            pitch: 89.9,
            ..Default::default()
        };
        let mut shake = Shake::default();
        shake.add(1.0);
        // Sweep the clock so the test does not depend on catching the noise at
        // a moment it happens to be pushing upward.
        for i in 0..500 {
            shake.t = i as f32 * 0.003;
            let drawn = cam.shaken(&shake);
            assert!(drawn.pitch < 90.0, "{}", drawn.pitch);
            let m = drawn.view_projection(800, 600);
            assert!(m.to_cols_array().iter().all(|v| v.is_finite()));
        }
    }
}
