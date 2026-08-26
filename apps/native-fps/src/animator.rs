//! Drives the operator's clips from what the server says each player is doing.
//!
//! The native counterpart of `CharacterAnimator.ts`, and it has the same shape
//! because the job is the same: choose a clip, crossfade to it, and layer the
//! two corrections a clip cannot know about — aim pitch, and an upper-body
//! action over whatever the legs are doing.
//!
//! What it replaced is worth stating, because it made a claim it could not keep.
//! `bodies.rs` derived a walk cycle from `(x * 3.5 + y * 3.5).sin()` — a
//! *position*, not a speed — so a player strafing on the spot animated at full
//! stride, a player sprinting along a wall of constant `x + y` stood still, and
//! nobody's feet ever touched the ground they were on. That is not a cheaper
//! approximation of a walk; it is a different function that happens to oscillate.
//!
//! One deliberate omission from the browser's version: **no stale fade.** The
//! browser dims a player whose updates have stopped arriving, and the native
//! client has no equivalent notion of a stale row to drive it from — inventing
//! one here would mean this module deciding what "stale" means, which is
//! `interp.rs`'s question, not the animator's.

use std::collections::HashMap;

use glam::{Mat4, Vec3, Vec4};

use crate::character::{Mask, Operator, Pose, FACING_OFFSET};
use crate::clips::{fade_for, is_one_shot, select_locomotion, OperatorState};
use crate::protocol::PlayerRow;

/// How long a fire action holds the upper body before locomotion takes it back.
const FIRE_HOLD: f32 = 0.28;
/// A reload is a whole animation; let it run.
const RELOAD_HOLD: f32 = 2.2;
/// How long an overlay takes to fade back out once its hold expires.
const OVERLAY_FADE: f32 = 0.15;

/// Aim pitch is split down the spine so the whole torso leans into a look.
/// Putting it all in the neck snaps the head off the shoulders.
const PITCH_SPINE: f32 = 0.35;
const PITCH_CHEST: f32 = 0.25;
const PITCH_HEAD: f32 = 0.4;

/// How much of the team colour washes over the texture.
///
/// A wash, not a repaint: the base map still has to read as a uniform, and a
/// full replace would make both teams the same silhouette in one colour. Matches
/// the browser's `color.lerp(tint, 0.28)`.
const TINT_STRENGTH: f32 = 0.28;

/// Faction armour colours, matching `bodies.rs` so the two body paths do not
/// disagree about which side a player is on while both exist.
const ARC_ARMOR: Vec3 = Vec3::new(0.29, 0.23, 0.17);
const HALON_ARMOR: Vec3 = Vec3::new(0.12, 0.16, 0.23);
const BOT_ARMOR: Vec3 = Vec3::new(0.85, 0.55, 0.20);

/// One player's copy of the operator, and everything remembered between frames.
struct Actor {
    pose: Pose,
    /// The base locomotion clip, and how far into it we are.
    current: Option<String>,
    time: f32,
    /// The clip being faded out of, held with its own clock: a crossfade has to
    /// keep advancing the outgoing cycle or the legs freeze mid-stride for the
    /// length of the fade.
    previous: Option<String>,
    previous_time: f32,
    fade_remaining: f32,
    fade_total: f32,
    /// An action layered over the upper body.
    overlay: Option<Overlay>,
    /// Position last frame, for deriving velocity.
    prev: Option<(f32, f32)>,
    smoothed_speed: f32,
    /// Set every frame a row arrives, so departed players can be swept.
    seen: bool,
}

struct Overlay {
    clip: String,
    time: f32,
    /// Seconds left at full weight.
    hold: f32,
    /// Seconds left of the fade out, once `hold` is spent.
    fade: f32,
}

/// What the renderer needs to draw one player.
pub struct ActorPose {
    /// One skinning matrix per bone, already in world space.
    pub bones: Vec<Mat4>,
    /// `rgb` the team wash, `a` unused today — kept because the shader's tint
    /// slot is a `vec4` either way and a future fade has somewhere to go.
    pub tint: Vec4,
    /// The right hand in world space, for hanging a weapon prop off.
    pub grip: Option<Mat4>,
    /// Which weapon slot this player is holding, for the prop in that hand.
    pub weapon: i32,
}

/// Every drawn player's operator.
pub struct Squad {
    operator: Operator,
    actors: HashMap<String, Actor>,
    poses: Vec<ActorPose>,
}

impl Squad {
    pub fn load() -> Result<Squad, String> {
        Ok(Squad {
            operator: Operator::load()?,
            actors: HashMap::new(),
            poses: Vec::new(),
        })
    }

    pub fn operator(&self) -> &Operator {
        &self.operator
    }

    /// The poses computed by the last `update`.
    ///
    /// Separate from `update`'s return value so the caller can advance the
    /// animation early in the frame and upload it late, without holding a borrow
    /// of the whole `Squad` across everything in between.
    pub fn poses(&self) -> &[ActorPose] {
        &self.poses
    }

    /// Somebody fired: kick their upper body.
    ///
    /// Driven from the server's `shot` effect rather than from a local trigger,
    /// for the same reason the muzzle flash is — a fire animation on a shot the
    /// server refused is a lie about what happened.
    pub fn note_shot(&mut self, id: &str) {
        if let Some(actor) = self.actors.get_mut(id) {
            actor.overlay = Some(Overlay {
                clip: "firing_rifle".to_string(),
                time: 0.0,
                hold: FIRE_HOLD,
                fade: OVERLAY_FADE,
            });
        }
    }

    /// Somebody started a reload.
    ///
    /// Unwired today: the wire carries no reload effect for *other* players, and
    /// the only player whose reload this client knows about is the one whose
    /// body is never drawn. Kept because the layering it needs is the part that
    /// was hard, and the server growing the event is a one-line change here.
    pub fn note_reload(&mut self, id: &str) {
        if let Some(actor) = self.actors.get_mut(id) {
            actor.overlay = Some(Overlay {
                clip: "reloading".to_string(),
                time: 0.0,
                hold: RELOAD_HOLD,
                fade: OVERLAY_FADE,
            });
        }
    }

    /// Advance every drawn player and return their poses.
    ///
    /// `self_id` is skipped: you never see your own body, and posing it would be
    /// a character's worth of skinning per frame for nothing.
    pub fn update(&mut self, dt: f32, rows: &[PlayerRow], self_id: &str) -> &[ActorPose] {
        let dt = if dt > 0.0 && dt.is_finite() {
            dt.min(0.25)
        } else {
            1.0 / 60.0
        };

        for actor in self.actors.values_mut() {
            actor.seen = false;
        }
        self.poses.clear();

        for row in rows {
            if row.id == self_id {
                continue;
            }
            // Dead players are still drawn — that is the whole point of having
            // death animations. `bodies.rs` skipped them, so a kill made the
            // body vanish rather than fall over.
            let operator = &self.operator;
            let actor = self
                .actors
                .entry(row.id.clone())
                .or_insert_with(|| Actor::new(operator));
            actor.seen = true;
            self.poses.push(actor.advance(operator, dt, row));
        }

        // Sweep players who have left. Without this the map grows for the life
        // of the process and a rejoining player inherits the pose of whoever
        // last held their id.
        self.actors.retain(|_, actor| actor.seen);
        &self.poses
    }
}

impl Actor {
    fn new(operator: &Operator) -> Actor {
        Actor {
            pose: Pose::new(operator),
            current: None,
            time: 0.0,
            previous: None,
            previous_time: 0.0,
            fade_remaining: 0.0,
            fade_total: 0.0,
            overlay: None,
            prev: None,
            smoothed_speed: 0.0,
            seen: true,
        }
    }

    fn advance(&mut self, operator: &Operator, dt: f32, row: &PlayerRow) -> ActorPose {
        let state = self.derive_state(dt, row);
        let wanted = select_locomotion(&state);

        if self.current.as_deref() != Some(wanted) {
            self.previous = self.current.take();
            self.previous_time = self.time;
            self.fade_total = fade_for(wanted);
            self.fade_remaining = self.fade_total;
            self.current = Some(wanted.to_string());
            self.time = 0.0;
        }

        self.time += dt;
        self.previous_time += dt;
        if self.fade_remaining > 0.0 {
            self.fade_remaining = (self.fade_remaining - dt).max(0.0);
        }

        self.pose.reset(operator);

        // Outgoing clip first at full weight, then the incoming one at the fade
        // fraction — which leaves the pose at `lerp(previous, current, alpha)`.
        let alpha = if self.fade_total > 0.0 && self.fade_remaining > 0.0 {
            1.0 - self.fade_remaining / self.fade_total
        } else {
            1.0
        };
        if alpha < 1.0 {
            if let Some(previous) = self.previous.as_deref() {
                if let Some(clip) = operator.clip(previous) {
                    let t = clip_time(previous, self.previous_time, clip.duration);
                    self.pose.blend(operator, clip, t, 1.0, Mask::All);
                }
            }
        } else {
            self.previous = None;
        }
        if let Some(current) = self.current.as_deref() {
            if let Some(clip) = operator.clip(current) {
                let t = clip_time(current, self.time, clip.duration);
                self.pose.blend(operator, clip, t, alpha, Mask::All);
            }
        }

        // The action layer, over the arms and chest only. Its weight falls to
        // zero over `OVERLAY_FADE` so the arms return to the walk rather than
        // snapping back to it.
        if let Some(overlay) = self.overlay.as_mut() {
            overlay.time += dt;
            let weight = if overlay.hold > 0.0 {
                overlay.hold = (overlay.hold - dt).max(0.0);
                1.0
            } else {
                overlay.fade = (overlay.fade - dt).max(0.0);
                if OVERLAY_FADE > 0.0 {
                    overlay.fade / OVERLAY_FADE
                } else {
                    0.0
                }
            };
            if weight <= 0.0 {
                self.overlay = None;
            } else if let Some(clip) = operator.clip(&overlay.clip) {
                let t = clip_time(&overlay.clip, overlay.time, clip.duration);
                self.pose.blend(operator, clip, t, weight, Mask::UpperBody);
            }
        }

        // After the clips, never before: a blend writes bone rotations
        // wholesale, so a pitch applied first is simply overwritten.
        if row.alive && row.pitch != 0.0 {
            self.pose
                .rotate_bone_x(operator, "Spine", -row.pitch * PITCH_SPINE);
            self.pose
                .rotate_bone_x(operator, "Spine2", -row.pitch * PITCH_CHEST);
            self.pose
                .rotate_bone_x(operator, "Head", -row.pitch * PITCH_HEAD);
        }

        let model = model_matrix(row);
        let mut bones = vec![Mat4::IDENTITY; operator.bone_count()];
        self.pose.skinning(operator, model, &mut bones);

        let armor = if row.bot {
            BOT_ARMOR
        } else if row.team == 0 {
            ARC_ARMOR
        } else {
            HALON_ARMOR
        };

        ActorPose {
            bones,
            tint: armor.extend(TINT_STRENGTH),
            grip: self.pose.bone_matrix(operator, "RightHand", model),
            weapon: row.weapon,
        }
    }

    /// Turn two positions and a row into the state a clip is chosen from.
    ///
    /// Speed is smoothed because it is derived from *interpolated* positions:
    /// the raw frame-to-frame delta crosses the idle threshold constantly at a
    /// walk, and a clip that reselects every other frame never finishes a
    /// crossfade — it just shivers.
    fn derive_state(&mut self, dt: f32, row: &PlayerRow) -> OperatorState {
        let (vx, vy) = match self.prev {
            Some((px, py)) => ((row.x - px) / dt, (row.y - py) / dt),
            None => (0.0, 0.0),
        };
        self.prev = Some((row.x, row.y));

        let speed = vx.hypot(vy).min(25.0);
        self.smoothed_speed += (speed - self.smoothed_speed) * (dt * 10.0).min(1.0);

        // Project velocity onto where the player is looking, so "forward" means
        // forward for them rather than for the world.
        let (sin, cos) = row.yaw.sin_cos();
        let forward = vx * cos + vy * sin;
        let strafe = -vx * sin + vy * cos;
        let magnitude = forward.hypot(strafe).max(1e-3);

        OperatorState {
            alive: row.alive,
            ground: row.ground,
            crouch: row.crouch,
            speed: self.smoothed_speed,
            forward: forward / magnitude,
            strafe: strafe / magnitude,
            hurt: row.hp < 35.0,
        }
    }
}

/// Where in a clip to sample, given how long it has been playing.
///
/// One-shots clamp and hold their last frame; everything else wraps. Getting
/// this backwards is the difference between a body that falls over and stays
/// down, and one that falls over on a loop.
fn clip_time(name: &str, elapsed: f32, duration: f32) -> f32 {
    if duration <= 0.0 {
        return 0.0;
    }
    if is_one_shot(name) {
        elapsed.min(duration)
    } else {
        elapsed % duration
    }
}

/// A player's world transform.
///
/// The renderer works in **Y-up** space while the game's cubes are Z-up — see
/// the mapping at the bottom of `push_oriented_box`. That swap happens here, so
/// everything downstream of this function is in one space.
///
/// `yaw` is **radians** here. The wire and every `PlayerRow` carry radians; only
/// `Camera` is in degrees, and mixing the two is a character rotated by a factor
/// of 57 — which reads as a random facing rather than as a unit bug.
///
/// Public so `examples/operator_preview.rs` can check the facing through the
/// same function the client uses, rather than through a copy of it that could
/// agree with itself while both were wrong.
pub fn model_matrix(row: &PlayerRow) -> Mat4 {
    Mat4::from_translation(Vec3::new(row.x, row.z, row.y))
        * Mat4::from_rotation_y(row.yaw + FACING_OFFSET)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(id: &str) -> PlayerRow {
        PlayerRow {
            id: id.to_string(),
            alive: true,
            ground: true,
            hp: 100.0,
            ..Default::default()
        }
    }

    /// The facing convention, pinned against the box rig's own formula.
    ///
    /// `FACING_OFFSET` was solved on paper from `push_oriented_box`, and a
    /// picture of one operator at one yaw cannot tell a correct rotation from a
    /// mirrored one — both look right head-on and disagree everywhere else. This
    /// checks the whole circle against the forward vector `bodies.rs` derives,
    /// which is the definition the rest of the client already agrees with.
    #[test]
    fn the_model_faces_where_the_box_rig_faced() {
        for step in 0..16 {
            let yaw = step as f32 * std::f32::consts::TAU / 16.0;
            let mut r = row("them");
            r.yaw = yaw;

            // Mixamo's characters face model +Z.
            let facing = model_matrix(&r) * glam::Vec4::new(0.0, 0.0, 1.0, 0.0);

            // `bodies.rs`, verbatim: the cube-space heading, then its render
            // mapping of (x, y, z) -> (x, z, y).
            let angle = -yaw - std::f32::consts::FRAC_PI_2;
            let expected = Vec3::new(-angle.sin(), 0.0, angle.cos());

            let got = Vec3::new(facing.x, facing.y, facing.z).normalize();
            assert!(
                got.abs_diff_eq(expected, 1e-5),
                "at yaw {yaw}: model faces {got}, the box rig faced {expected}"
            );
        }
    }

    #[test]
    fn a_player_is_placed_where_the_wire_puts_them() {
        // The cube grid is Z-up and the renderer is Y-up. Getting the swap
        // backwards puts every player at a height equal to their y coordinate,
        // which on a large map is somewhere above the ceiling.
        let mut r = row("them");
        r.x = 12.0;
        r.y = 34.0;
        r.z = 5.0;
        let origin = model_matrix(&r) * glam::Vec4::new(0.0, 0.0, 0.0, 1.0);
        assert!(
            Vec3::new(origin.x, origin.y, origin.z).abs_diff_eq(Vec3::new(12.0, 5.0, 34.0), 1e-6)
        );
    }

    #[test]
    fn a_one_shot_holds_its_last_frame_and_a_cycle_wraps() {
        assert_eq!(clip_time("dying", 99.0, 2.0), 2.0);
        assert_eq!(clip_time("standard_walk", 2.5, 2.0), 0.5);
        // A zero-length clip must not divide by it.
        assert_eq!(clip_time("standard_walk", 1.0, 0.0), 0.0);
    }

    #[test]
    fn a_player_who_leaves_is_swept() {
        let mut squad = Squad::load().expect("operator");
        let rows = vec![row("a"), row("b")];
        squad.update(1.0 / 60.0, &rows, "");
        assert_eq!(squad.actors.len(), 2);
        squad.update(1.0 / 60.0, &rows[..1], "");
        assert_eq!(squad.actors.len(), 1, "b should have been swept");
        assert!(squad.actors.contains_key("a"));
    }

    #[test]
    fn your_own_body_is_never_posed() {
        let mut squad = Squad::load().expect("operator");
        let rows = vec![row("me"), row("them")];
        let poses = squad.update(1.0 / 60.0, &rows, "me");
        assert_eq!(poses.len(), 1);
    }

    #[test]
    fn a_dead_player_is_still_drawn() {
        // `bodies.rs` skipped them, so a kill made the body disappear instead of
        // falling over — which is the single most visible thing the clips buy.
        let mut squad = Squad::load().expect("operator");
        let mut dead = row("them");
        dead.alive = false;
        let poses = squad.update(1.0 / 60.0, &[dead], "me");
        assert_eq!(poses.len(), 1);
    }

    #[test]
    fn standing_still_does_not_drive_the_walk_cycle() {
        // The bug this whole module replaces: a body that animates from where it
        // is rather than from how fast it is going.
        let mut squad = Squad::load().expect("operator");
        let mut still = row("them");
        still.x = 40.0;
        still.y = 37.5;
        for _ in 0..30 {
            squad.update(1.0 / 60.0, &[still.clone()], "me");
        }
        let actor = &squad.actors["them"];
        assert_eq!(actor.current.as_deref(), Some("rifle_aiming_idle"));
    }

    #[test]
    fn a_crossfade_finishes_and_drops_the_outgoing_clip() {
        let mut squad = Squad::load().expect("operator");
        let mut walker = row("them");
        for step in 0..40 {
            walker.x = step as f32 * 0.1;
            squad.update(1.0 / 60.0, &[walker.clone()], "me");
        }
        let actor = &squad.actors["them"];
        assert_eq!(actor.current.as_deref(), Some("standard_walk"));
        assert!(
            actor.previous.is_none(),
            "the fade should have completed and released the idle clip"
        );
    }
}
