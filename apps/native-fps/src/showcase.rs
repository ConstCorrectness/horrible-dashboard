//! The menu's stage: one weapon or operator, in three dimensions, to turn over.
//!
//! The front end drew its weapons as silhouettes built from rectangles
//! (`front::weapon_art`), which says which class a weapon is and nothing about
//! what it looks like. The models were already compiled into this executable —
//! the match draws them — so the menu shows those instead, and lets them be
//! handled: drag to turn, scroll to close in, `F` to turn it over, `R` to put
//! it back.
//!
//! This half is the state and the arithmetic, free of `wgpu` so it is tested
//! headless: what is on the stage, how it has been turned, and where the camera
//! stands. `showcase_gpu.rs` draws it.

use glam::{Mat4, Vec3};

use crate::camera::Camera;

/// Radians of turn per pixel of drag.
const DRAG: f32 = 0.008;
/// How far the camera may climb over or drop under the subject, radians.
const TILT_LIMIT: f32 = 1.1;
pub const ZOOM_MIN: f32 = 0.6;
pub const ZOOM_MAX: f32 = 2.6;
/// One notch of the wheel.
const ZOOM_STEP: f32 = 1.12;
/// Left alone this long, the subject starts turning by itself again.
const IDLE_RESUME: f32 = 2.5;
/// Radians a second, turning by itself.
const IDLE_TURN: f32 = 0.35;
/// How long `F` takes to turn a weapon over and bring it back.
pub const INSPECT_TIME: f32 = 2.2;
/// How far a weapon rolls toward the eye at the middle of an inspect.
const INSPECT_ROLL: f32 = 0.9;
/// The stage camera's field of view, degrees.
const FOV: f32 = 34.0;
/// How much of the frame's short side the subject is fitted to.
const FILL: f32 = 0.9;
/// Where a weapon rests: a quarter turn off side-on, so it has depth.
const WEAPON_REST_YAW: f32 = -0.45;
const WEAPON_REST_TILT: f32 = 0.18;

/// What is on the stage.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Subject {
    /// A weapon prop, by the id it is compiled in under (`prop::WEAPON_GLBS`).
    Weapon(String),
    /// A side's operator: 0 CT, 1 T.
    Operator(usize),
}

/// How the subject has been handled.
#[derive(Debug, Clone)]
pub struct Showcase {
    subject: Option<Subject>,
    /// The subject's turn about the vertical.
    pub yaw: f32,
    /// The camera's climb above the subject.
    pub tilt: f32,
    pub zoom: f32,
    held: bool,
    /// Seconds since it was last touched.
    idle: f32,
    /// Seconds into an inspect, while one plays.
    inspect: Option<f32>,
    /// The weapon's finish, as `viewmodel::prop_tint` gives it. Kept apart
    /// from the subject so that trying finishes on one weapon leaves it turned
    /// however it was.
    tint: [f32; 3],
}

impl Default for Showcase {
    fn default() -> Showcase {
        Showcase {
            subject: None,
            yaw: 0.0,
            tilt: 0.0,
            zoom: 1.0,
            held: false,
            idle: IDLE_RESUME,
            inspect: None,
            tint: [1.0; 3],
        }
    }
}

fn smooth(t: f32) -> f32 {
    let t = t.clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

impl Showcase {
    pub fn subject(&self) -> Option<&Subject> {
        self.subject.as_ref()
    }

    /// Put `subject` on the stage. A change of subject is a fresh look at it;
    /// the same one again keeps however it was left.
    pub fn show(&mut self, subject: Option<Subject>) {
        if self.subject == subject {
            return;
        }
        self.subject = subject;
        self.reset();
    }

    /// Dress the weapon on the stage in a finish; white is stock.
    pub fn dress(&mut self, tint: [f32; 3]) {
        self.tint = tint;
    }

    pub fn tint(&self) -> [f32; 3] {
        self.tint
    }

    /// Back to how the subject first appeared.
    pub fn reset(&mut self) {
        let weapon = matches!(self.subject, Some(Subject::Weapon(_)));
        self.yaw = if weapon { WEAPON_REST_YAW } else { 0.0 };
        self.tilt = if weapon { WEAPON_REST_TILT } else { 0.08 };
        self.zoom = 1.0;
        self.held = false;
        self.inspect = None;
        self.idle = IDLE_RESUME;
    }

    pub fn press(&mut self) {
        self.held = true;
        self.idle = 0.0;
    }

    pub fn release(&mut self) {
        self.held = false;
    }

    pub fn held(&self) -> bool {
        self.held
    }

    /// The pointer moved by this many pixels with the button down.
    pub fn drag(&mut self, dx: f32, dy: f32) {
        if !self.held {
            return;
        }
        self.yaw += dx * DRAG;
        self.tilt = (self.tilt + dy * DRAG).clamp(-TILT_LIMIT, TILT_LIMIT);
        self.idle = 0.0;
    }

    /// The wheel moved by this many notches; positive closes in.
    pub fn scroll(&mut self, notches: f32) {
        self.zoom = (self.zoom * ZOOM_STEP.powf(notches)).clamp(ZOOM_MIN, ZOOM_MAX);
        self.idle = 0.0;
    }

    /// Turn the weapon over. Only a weapon: an operator has nothing to show
    /// that turning it round does not.
    pub fn inspect(&mut self) {
        if matches!(self.subject, Some(Subject::Weapon(_))) && self.inspect.is_none() {
            self.inspect = Some(0.0);
            self.idle = 0.0;
        }
    }

    pub fn inspecting(&self) -> bool {
        self.inspect.is_some()
    }

    pub fn tick(&mut self, dt: f32) {
        if let Some(t) = self.inspect.as_mut() {
            *t += dt;
            if *t >= INSPECT_TIME {
                self.inspect = None;
            }
            return;
        }
        if self.held {
            return;
        }
        self.idle += dt;
        if self.idle >= IDLE_RESUME {
            // Eased in over a second, so it drifts off rather than lurching.
            self.yaw += IDLE_TURN * dt * smooth(self.idle - IDLE_RESUME);
        }
    }

    /// The subject's turn about the vertical, the inspect's full turn included.
    pub fn turn(&self) -> f32 {
        let over = self
            .inspect
            .map_or(0.0, |t| std::f32::consts::TAU * smooth(t / INSPECT_TIME));
        self.yaw + over
    }

    /// A weapon's transform about the middle of its bounds.
    ///
    /// The model turns and the camera only climbs: turned this way the light
    /// stays where it is and moves across the metal, which is most of what
    /// there is to see on a weapon. Orbiting the camera instead would show the
    /// same lit side from every angle.
    pub fn weapon_model(&self, centre: Vec3) -> Mat4 {
        let roll = self.inspect.map_or(0.0, |t| {
            INSPECT_ROLL * (std::f32::consts::PI * t / INSPECT_TIME).sin()
        });
        Mat4::from_translation(centre)
            * Mat4::from_rotation_y(self.turn())
            * Mat4::from_rotation_z(roll)
            * Mat4::from_translation(-centre)
    }

    /// Where the camera stands to frame `bounds` (render space, Y up) in a
    /// frame of this aspect.
    ///
    /// Fitted to what the subject can sweep rather than to its box as it
    /// stands: it turns about the vertical, so across the frame it needs its
    /// widest horizontal reach, and up the frame it needs its height plus
    /// whatever of that reach the camera's climb tips into view. A bounding
    /// sphere would do the same job and leave a rifle — all length, no height
    /// — using half the frame it is given.
    pub fn camera(&self, bounds: (Vec3, Vec3), aspect: f32) -> Camera {
        let centre = (bounds.0 + bounds.1) * 0.5;
        let half = ((bounds.1 - bounds.0) * 0.5).max(Vec3::splat(0.02));
        let (across, up) = match self.subject {
            // An operator stands on its feet: only the yaw moves it.
            Some(Subject::Operator(_)) => (half.x.hypot(half.z), half.y),
            // A weapon also rolls about its barrel during an inspect, so its
            // two short axes are one radius.
            _ => {
                let thin = half.x.hypot(half.y);
                (half.z.hypot(thin), thin)
            }
        };
        let up = up + across * self.tilt.abs().sin();
        let tan_v = (FOV.to_radians() * 0.5).tan();
        let tan_h = tan_v * aspect.max(0.05);
        // A point swinging round at radius `r` is widest on screen not when it
        // is furthest to the side but a little before, when it is also nearer:
        // it peaks at r / sqrt(d² - r²), which is what this solves for `d`.
        let fit = |reach: f32, tan: f32| reach * (1.0 + 1.0 / (tan * FILL).powi(2)).sqrt();
        let distance = fit(across, tan_h).max(fit(up, tan_v)) / self.zoom;
        let mut camera = Camera {
            yaw: 0.0,
            pitch: -self.tilt.to_degrees(),
            fov: FOV,
            ..Default::default()
        };
        let eye = centre - camera.forward() * distance;
        // `Camera` is stated in cube space, where height is `z`.
        (camera.x, camera.y, camera.z) = (eye.x, eye.z, eye.y);
        camera
    }
}

/// The prop to show for a weapon the server lists.
///
/// The models are keyed by class — `knife`, `pistol`, `assault`, `shotgun`,
/// `sniper` — and a weapon names its class in `model`, falling back to its id.
/// A class with no model of its own is shown as the nearest one that has: a
/// stage with nothing on it tells the player less than a rifle standing in for
/// a submachine gun.
pub fn prop_for(model: &str, id: &str) -> &'static str {
    let name = if model.is_empty() { id } else { model }.to_ascii_lowercase();
    if name.contains("knife") {
        "knife"
    } else if name.contains("sniper") || name.contains("scout") {
        "sniper"
    } else if name.contains("shotgun") || name.contains("breacher") {
        "shotgun"
    } else if name.contains("pistol") || name.contains("cannon") {
        "pistol"
    } else {
        "assault"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn weapon() -> Showcase {
        let mut show = Showcase::default();
        show.show(Some(Subject::Weapon("assault".into())));
        show
    }

    #[test]
    fn a_new_finish_keeps_the_weapon_where_it_was_left() {
        let mut show = weapon();
        show.press();
        show.drag(100.0, 0.0);
        show.release();
        let yaw = show.yaw;
        show.dress([1.0, 0.2, 0.1]);
        show.show(Some(Subject::Weapon("assault".into())));
        assert_eq!(show.yaw, yaw);
        assert_eq!(show.tint(), [1.0, 0.2, 0.1]);
    }

    #[test]
    fn a_new_subject_is_a_fresh_look_and_the_same_one_is_not() {
        let mut show = weapon();
        show.press();
        show.drag(100.0, 0.0);
        show.scroll(3.0);
        let (yaw, zoom) = (show.yaw, show.zoom);

        // Painting the same slot again every frame must not undo the drag.
        show.show(Some(Subject::Weapon("assault".into())));
        assert_eq!((show.yaw, show.zoom), (yaw, zoom));

        show.show(Some(Subject::Weapon("sniper".into())));
        assert_eq!(show.zoom, 1.0);
        assert_ne!(show.yaw, yaw);
        assert!(!show.held(), "a drag does not carry over to the next subject");
    }

    #[test]
    fn a_drag_turns_only_while_the_button_is_down() {
        let mut show = weapon();
        let rest = show.yaw;
        show.drag(50.0, 50.0);
        assert_eq!(show.yaw, rest, "moving the pointer over it is not a drag");
        show.press();
        show.drag(50.0, 0.0);
        assert!(show.yaw > rest);
        show.release();
        let turned = show.yaw;
        show.drag(50.0, 0.0);
        assert_eq!(show.yaw, turned);
    }

    #[test]
    fn the_camera_cannot_go_over_the_top_or_through_the_subject() {
        let mut show = weapon();
        show.press();
        show.drag(0.0, 10_000.0);
        assert_eq!(show.tilt, TILT_LIMIT);
        show.drag(0.0, -100_000.0);
        assert_eq!(show.tilt, -TILT_LIMIT);
        show.scroll(100.0);
        assert_eq!(show.zoom, ZOOM_MAX);
        show.scroll(-200.0);
        assert_eq!(show.zoom, ZOOM_MIN);
    }

    #[test]
    fn it_turns_by_itself_until_touched_and_again_once_left_alone() {
        let mut show = weapon();
        let rest = show.yaw;
        show.tick(1.0);
        assert!(show.yaw > rest, "an untouched subject should be turning");

        show.press();
        show.release();
        let left = show.yaw;
        show.tick(1.0);
        assert_eq!(show.yaw, left, "it waits before taking the turn back");
        for _ in 0..40 {
            show.tick(0.1);
        }
        assert!(show.yaw > left);
    }

    #[test]
    fn an_inspect_is_one_full_turn_that_ends_where_it_began() {
        let mut show = weapon();
        show.press();
        let before = show.weapon_model(Vec3::ZERO);
        show.inspect();
        assert!(show.inspecting());
        show.tick(INSPECT_TIME * 0.5);
        let half = show.weapon_model(Vec3::ZERO);
        assert!(!half.abs_diff_eq(before, 0.1), "half way it is turned over");
        // A second press mid-way does not restart it.
        show.inspect();
        show.tick(INSPECT_TIME * 0.5 + 0.01);
        assert!(!show.inspecting());
        assert!(show.weapon_model(Vec3::ZERO).abs_diff_eq(before, 1e-4));
    }

    #[test]
    fn an_operator_is_not_inspected() {
        let mut show = Showcase::default();
        show.show(Some(Subject::Operator(0)));
        show.inspect();
        assert!(!show.inspecting());
    }

    #[test]
    fn the_subject_stays_in_frame_however_it_is_turned() {
        // The corners of the box, through the camera, at every zoom-out turn:
        // all inside the clip volume, in a wide frame and a tall one.
        let bounds = (Vec3::new(-0.2, -0.4, -1.6), Vec3::new(0.2, 0.5, 1.9));
        for (w, h) in [(800u32, 400u32), (300, 700)] {
            let mut show = weapon();
            show.press();
            for step in 0..12 {
                show.yaw = step as f32 * 0.6;
                show.tilt = (step as f32 * 0.3).sin() * TILT_LIMIT;
                let camera = show.camera(bounds, w as f32 / h as f32);
                let view_proj =
                    camera.view_projection(w, h) * show.weapon_model((bounds.0 + bounds.1) * 0.5);
                for corner in 0..8 {
                    let p = Vec3::new(
                        if corner & 1 == 0 { bounds.0.x } else { bounds.1.x },
                        if corner & 2 == 0 { bounds.0.y } else { bounds.1.y },
                        if corner & 4 == 0 { bounds.0.z } else { bounds.1.z },
                    );
                    let clip = view_proj * p.extend(1.0);
                    assert!(clip.w > 0.0, "a corner is behind the camera");
                    assert!(
                        (clip.x / clip.w).abs() <= 1.0 && (clip.y / clip.w).abs() <= 1.0,
                        "a corner left the {w}x{h} frame at step {step}"
                    );
                }
            }
        }
    }

    #[test]
    fn every_listed_class_has_a_model_to_stand_on_the_stage() {
        for (model, id, want) in [
            ("knife", "knife", "knife"),
            ("", "pistol", "pistol"),
            ("hand_cannon", "deagle", "pistol"),
            ("sniper", "awp", "sniper"),
            ("scout", "scout", "sniper"),
            ("shotgun", "nova", "shotgun"),
            ("smg", "mp9", "assault"),
            ("", "something_new", "assault"),
        ] {
            let prop = prop_for(model, id);
            assert_eq!(prop, want, "{model}/{id}");
            assert!(crate::prop::weapon_glb(prop).is_some(), "{prop} has no GLB");
        }
    }
}
