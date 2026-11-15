//! Drives the operator's clips from what the server says each player is doing.
//!
//! The native counterpart of `CharacterAnimator.ts`, and it has the same shape
//! because the job is the same: choose a clip, crossfade to it, and layer the
//! corrections a clip cannot know about — legs solved onto planted feet
//! (`locomotion.rs`), an upper-body action over them, and a torso turned until
//! the weapon and the head point exactly down the row's yaw and pitch.
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

use glam::{Mat4, Quat, Vec3, Vec4};

use crate::arms::solve_two_bone;
use crate::character::{Mask, Operator, Pose, FACING_OFFSET};
use crate::clips::{fade_for, is_one_shot, select_locomotion, OperatorState};
use crate::locomotion::{Gait, GaitInput, GaitOutput};
use crate::protocol::PlayerRow;

/// How long a fire action holds the upper body before locomotion takes it back.
const FIRE_HOLD: f32 = 0.28;
/// A reload is a whole animation; let it run.
const RELOAD_HOLD: f32 = 2.2;
/// How long an overlay takes to fade back out once its hold expires.
const OVERLAY_FADE: f32 = 0.15;

/// How far up the knee's bend is biased, against the pelvis' forward.
const KNEE_POLE_UP: f32 = 0.2;

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
    /// Which operator the pose was built for: 0 CT, 1 T.
    side: usize,
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
    gait: Gait,
    /// The hip joints' height over a planted ankle in the pose last drawn.
    hip_height: f32,
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
#[derive(Clone, Debug, Default)]
pub struct ActorPose {
    /// Whose pose this is, so a look (`cosmetics.rs`) can be laid over it.
    pub id: String,
    /// The held prop's metal and trim, when the player's weapon wears a skin.
    /// `None` is bare metal (`held.rs`).
    pub prop_colors: Option<([f32; 3], [f32; 3])>,
    /// One skinning matrix per bone, already in world space.
    pub bones: Vec<Mat4>,
    /// `rgb` the team wash, `a` unused today — kept because the shader's tint
    /// slot is a `vec4` either way and a future fade has somewhere to go.
    pub tint: Vec4,
    /// The right hand in world space, for hanging a weapon prop off.
    pub grip: Option<Mat4>,
    /// Which weapon slot this player is holding, for the prop in that hand.
    pub weapon: i32,
    /// Team index: 0 = CT, 1 = T.
    pub team: usize,
}

use std::sync::{Arc, OnceLock};

static CT_OPERATOR: OnceLock<Result<Arc<Operator>, String>> = OnceLock::new();
static T_OPERATOR: OnceLock<Result<Arc<Operator>, String>> = OnceLock::new();

/// Every drawn player's operator.
pub struct Squad {
    operator: Arc<Operator>,
    operator_t: Arc<Operator>,
    rig: Rig,
    rig_t: Rig,
    actors: HashMap<String, Actor>,
    poses: Vec<ActorPose>,
}

impl Squad {
    pub fn load() -> Result<Squad, String> {
        let op_ct = CT_OPERATOR
            .get_or_init(|| Operator::load().map(Arc::new))
            .clone()?;
        let op_t = T_OPERATOR
            .get_or_init(|| Operator::load_t().map(Arc::new))
            .clone()?;
        Ok(Squad {
            rig: Rig::read(&op_ct),
            rig_t: Rig::read(&op_t),
            operator: op_ct,
            operator_t: op_t,
            actors: HashMap::new(),
            poses: Vec::new(),
        })
    }

    pub fn operator(&self) -> &Operator {
        &self.operator
    }

    pub fn operator_t(&self) -> &Operator {
        &self.operator_t
    }

    /// The poses computed by the last `update`.
    ///
    /// Separate from `update`'s return value so the caller can advance the
    /// animation early in the frame and upload it late, without holding a borrow
    /// of the whole `Squad` across everything in between.
    pub fn poses(&self) -> &[ActorPose] {
        &self.poses
    }

    /// The last `update`'s poses, to lay each player's look over
    /// (`App::dress_squad`): the agent's wash and the held skin.
    pub fn poses_mut(&mut self) -> &mut [ActorPose] {
        &mut self.poses
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
            let (operator, rig) = if row.team == 1 {
                (&self.operator_t, &self.rig_t)
            } else {
                (&self.operator, &self.rig)
            };
            let side = usize::from(row.team == 1);
            let actor = self
                .actors
                .entry(row.id.clone())
                .or_insert_with(|| Actor::new(operator, rig, side));
            if actor.side != side {
                // Swapped sides, which is a different skeleton: the two rigs
                // do not have the same bones, and a pose built for one cannot
                // be reset from the other.
                *actor = Actor::new(operator, rig, side);
            }
            actor.seen = true;
            self.poses.push(actor.advance(operator, rig, dt, row));
        }

        // Sweep players who have left. Without this the map grows for the life
        // of the process and a rejoining player inherits the pose of whoever
        // last held their id.
        self.actors.retain(|_, actor| actor.seen);
        &self.poses
    }
}

impl Actor {
    fn new(operator: &Operator, rig: &Rig, side: usize) -> Actor {
        Actor {
            side,
            pose: Pose::new(operator),
            current: None,
            time: 0.0,
            previous: None,
            previous_time: 0.0,
            fade_remaining: 0.0,
            fade_total: 0.0,
            overlay: None,
            gait: Gait::new(),
            hip_height: rig.legs.as_ref().map_or(0.0, |legs| legs.hip_height),
            seen: true,
        }
    }

    fn advance(&mut self, operator: &Operator, rig: &Rig, dt: f32, row: &PlayerRow) -> ActorPose {
        let (leg_length, hip_width) = rig
            .legs
            .as_ref()
            .map_or((0.0, 0.0), |legs| (legs.leg_length, legs.hip_width));
        let gait = self.gait.step(
            &GaitInput {
                x: row.x as f64,
                y: row.y as f64,
                yaw: row.yaw as f64,
                alive: row.alive,
                grounded: row.ground,
                hip_height: self.hip_height as f64,
                leg_length: leg_length as f64,
                hip_width: hip_width as f64,
            },
            dt as f64,
        );
        let wanted = select_locomotion(&OperatorState {
            alive: row.alive,
            ground: row.ground,
            crouch: row.crouch,
            speed: gait.speed as f32,
        });

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
        // wholesale, so a solve applied first is simply overwritten.
        let model = model_matrix(row);
        if row.alive {
            self.solve(operator, rig, row, &gait, model);
        }

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
            id: row.id.clone(),
            prop_colors: None,
            bones,
            tint: armor.extend(TINT_STRENGTH),
            grip: self.pose.bone_matrix(operator, "RightHand", model),
            weapon: row.weapon,
            team: if row.team == 1 { 1 } else { 0 },
        }
    }

    /// Everything laid over the clips for a living body.
    ///
    /// In **model space** throughout — the pose's own, with the feet at the
    /// origin and the body facing +Z — so the row's world-space targets are
    /// brought in through the inverse of the model matrix rather than each
    /// bone being taken out through it.
    fn solve(&mut self, op: &Operator, rig: &Rig, row: &PlayerRow, gait: &GaitOutput, model: Mat4) {
        self.pose.update_globals(op);
        let to_model = model.inverse();
        if let Some(legs) = &rig.legs {
            // Read before the hips are moved, so the gait plans next frame's
            // stride against the pose the clip gave rather than against its
            // own correction.
            let [left, right] = &legs.chains;
            let hip_y = (self.pose.global_position(left.up).y
                + self.pose.global_position(right.up).y)
                * 0.5;
            self.hip_height = hip_y - legs.ankle_height;
            if gait.weight > 0.0 {
                self.solve_legs(op, legs, row, gait, to_model);
            }
        }
        self.solve_aim(op, rig, row, to_model);
    }

    /// Put the pelvis and both legs where the gait says.
    fn solve_legs(
        &mut self,
        op: &Operator,
        legs: &Legs,
        row: &PlayerRow,
        gait: &GaitOutput,
        to_model: Mat4,
    ) {
        let pose = &mut self.pose;
        let weight = gait.weight as f32;
        let heading = row.yaw + gait.hip_yaw as f32;
        // Cube (x, y) is render (x, z).
        let ground = |angle: f32| to_model.transform_vector3(Vec3::new(angle.cos(), 0.0, angle.sin()));
        let forward = ground(heading);
        let side = ground(heading + std::f32::consts::FRAC_PI_2);

        // The pelvis: squared onto its heading (the aiming clips stand it 40°
        // off), leant into the travel, and sunk into the stride.
        let world = pose.global_rotation(legs.hips);
        let mut facing = world * legs.hips_forward;
        facing.y = 0.0;
        let square = if facing.length_squared() > 1e-8 {
            Quat::IDENTITY.slerp(Quat::from_rotation_arc(facing.normalize(), forward), weight)
        } else {
            Quat::IDENTITY
        };
        let tilt = (Vec3::Y
            + ground(row.yaw) * gait.lean_forward as f32
            + ground(row.yaw + std::f32::consts::FRAC_PI_2) * gait.lean_side as f32)
            .normalize();
        let lean = Quat::from_rotation_arc(Vec3::Y, tilt);
        let mut placed = pose.global_position(legs.hips);
        placed.y -= gait.drop as f32;
        pose.set_global_position(op, legs.hips, placed);
        pose.set_global_rotation(op, legs.hips, lean * square * world);

        for (chain, target) in legs.chains.iter().zip(gait.feet.iter()) {
            let hip = pose.global_position(chain.up);
            let knee_at = pose.global_position(chain.knee);
            let ankle = pose.global_position(chain.foot);
            let upper = hip.distance(knee_at);
            let lower = knee_at.distance(ankle);
            let planted = to_model.transform_point3(Vec3::new(
                target.x as f32,
                row.z + legs.ankle_height + target.lift as f32,
                target.y as f32,
            ));
            let goal = ankle.lerp(planted, weight);

            let (knee, _) = solve_two_bone(
                hip,
                goal,
                upper,
                lower,
                Vec3::new(forward.x, KNEE_POLE_UP, forward.z),
            );
            // Thigh onto the solved knee, then shin onto the ankle: two
            // shortest-arc turns, which leave each bone's own twist as the
            // clip had it.
            swing(pose, op, &[chain.up], knee_at - hip, (knee - hip).normalize());
            let knee_at = pose.global_position(chain.knee);
            swing(
                pose,
                op,
                &[chain.knee],
                pose.global_position(chain.foot) - knee_at,
                (goal - knee_at).normalize(),
            );

            // The foot: flat and pointing where the pelvis does, tipped by the
            // swing. About the side axis a positive turn lifts the toe; the
            // gait's is down.
            let flat = Quat::from_rotation_y(forward.x.atan2(forward.z)) * chain.rest;
            let tipped = Quat::from_axis_angle(side, -target.pitch as f32) * flat;
            let foot = pose.global_rotation(chain.foot).slerp(tipped, weight);
            pose.set_global_rotation(op, chain.foot, foot);
        }
    }

    /// Point the weapon and the head exactly where the player is looking.
    ///
    /// No clip knows the pitch — Mixamo's animations all look at the horizon —
    /// and the aiming clips do not even hold the rifle along the body's own
    /// forward. So the barrel's direction is measured off the hand that holds
    /// it and the spine is turned by whatever takes it onto the look direction;
    /// then the neck and head are turned the rest of the way. An enemy aiming
    /// at you is drawn aiming at you, from a balcony or across a strafe.
    ///
    /// A reload is the exception: the rifle is meant to come off the line then,
    /// so the chest is aimed instead and the arms are left to the clip.
    fn solve_aim(&mut self, op: &Operator, rig: &Rig, row: &PlayerRow, to_model: Mat4) {
        let pose = &mut self.pose;
        let look = to_model.transform_vector3(Vec3::new(
            row.yaw.cos() * row.pitch.cos(),
            row.pitch.sin(),
            row.yaw.sin() * row.pitch.cos(),
        ));

        let reloading = self
            .overlay
            .as_ref()
            .is_some_and(|overlay| overlay.clip == "reloading");
        match (rig.hand, rig.spine.last(), rig.chest_forward) {
            (Some(hand), _, _) if !reloading => {
                // The prop is modelled down +Z and gripped so that lands on
                // the hand bone's +Y, down the fingers (`held.rs`).
                let barrel = pose.global_rotation(hand) * Vec3::Y;
                swing(pose, op, &rig.spine, barrel, look);
            }
            (_, Some(&chest), Some(forward)) => {
                let front = pose.global_rotation(chest) * forward;
                swing(pose, op, &rig.spine, front, look);
            }
            _ => {}
        }

        if let (Some(&head), Some((forward, up))) = (rig.neck.last(), rig.head_axes) {
            let face = pose.global_rotation(head) * forward;
            swing(pose, op, &rig.neck, face, look);
            // That fixes where the face points and nothing about which way up
            // it is: the spine's turn leaves the head cocked to one side. Level
            // it by twisting about the look itself, which cannot move the face
            // off it.
            let level = Vec3::Y - look * look.y;
            let crown = pose.global_rotation(head) * up;
            let crown = crown - look * crown.dot(look);
            // Looking straight up or down there is no level to find.
            if level.length_squared() > 1e-4 {
                swing(pose, op, &rig.neck, crown, level.normalize());
            }
        }
    }
}

/// Rotate a chain so that `from` lands on `to`, each bone taking an equal part.
///
/// Every part is the same rotation about the same axis, so they compose to the
/// whole of it exactly: whatever hangs off the last bone ends up pointing along
/// `to` with no iteration and no residual, however the clip left it.
fn swing(pose: &mut Pose, op: &Operator, chain: &[usize], from: Vec3, to: Vec3) {
    if chain.is_empty() || from.length_squared() < 1e-10 {
        return;
    }
    let whole = Quat::from_rotation_arc(from.normalize(), to);
    let part = Quat::IDENTITY.slerp(whole, 1.0 / chain.len() as f32);
    for &node in chain {
        let world = pose.global_rotation(node);
        pose.set_global_rotation(op, node, part * world);
    }
}

/// One leg, hip to ankle, as node indices.
struct LegChain {
    up: usize,
    knee: usize,
    foot: usize,
    /// The foot's bind-pose rotation in model space: flat, toes down +Z.
    rest: Quat,
}

struct Legs {
    hips: usize,
    /// The pelvis' forward in its own frame.
    hips_forward: Vec3,
    /// Left, then right — the order `locomotion.rs` numbers feet in.
    chains: [LegChain; 2],
    /// How far above the ground an ankle sits with the foot flat.
    ankle_height: f32,
    leg_length: f32,
    hip_width: f32,
    /// The hip joints' height over a planted ankle, in the bind pose.
    hip_height: f32,
}

/// The bones the solve drives, and what the bind pose says about them.
struct Rig {
    /// `None` on a rig without legs to solve, which is then left to its clips.
    legs: Option<Legs>,
    /// Spine, bottom to top.
    spine: Vec<usize>,
    /// Neck then head.
    neck: Vec<usize>,
    hand: Option<usize>,
    chest_forward: Option<Vec3>,
    /// Model +Z and +Y in the head's own frame.
    head_axes: Option<(Vec3, Vec3)>,
}

impl Rig {
    /// Measure an operator's bind pose, which stands facing model +Z with its
    /// feet flat — the one pose "forward" and "flat" can be read off the bones
    /// rather than assumed about them.
    fn read(op: &Operator) -> Rig {
        let mut pose = Pose::new(op);
        pose.update_globals(op);
        let present = |names: &[&str]| -> Vec<usize> {
            names.iter().filter_map(|name| op.bone_node(name)).collect()
        };
        // A model-space axis, in a bone's own frame.
        let axis_of = |node: usize, axis: Vec3| pose.global_rotation(node).inverse() * axis;
        let forward_of = |node: usize| axis_of(node, Vec3::Z);

        let spine = present(&["Spine", "Spine1", "Spine2"]);
        let neck = present(&["Neck", "Head"]);
        let chain = |side: &str| -> Option<LegChain> {
            let foot = op.bone_node(&format!("{side}Foot"))?;
            Some(LegChain {
                up: op.bone_node(&format!("{side}UpLeg"))?,
                knee: op.bone_node(&format!("{side}Leg"))?,
                foot,
                rest: pose.global_rotation(foot),
            })
        };
        let legs = (|| {
            let hips = op.bone_node("Hips")?;
            let (left, right) = (chain("Left")?, chain("Right")?);
            let at = |node: usize| pose.global_position(node);
            let leg_length =
                at(left.up).distance(at(left.knee)) + at(left.knee).distance(at(left.foot));
            // A rig with nothing between hip and ankle has no stride to solve.
            if leg_length < 1e-6 {
                return None;
            }
            let ankle_height = (at(left.foot).y + at(right.foot).y) * 0.5;
            Some(Legs {
                hips,
                hips_forward: forward_of(hips),
                ankle_height,
                leg_length,
                hip_width: at(left.up).distance(at(right.up)),
                hip_height: (at(left.up).y + at(right.up).y) * 0.5 - ankle_height,
                chains: [left, right],
            })
        })();

        Rig {
            legs,
            chest_forward: spine.last().map(|&node| forward_of(node)),
            head_axes: neck
                .last()
                .map(|&node| (forward_of(node), axis_of(node, Vec3::Y))),
            hand: op.bone_node("RightHand"),
            spine,
            neck,
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
        * Mat4::from_rotation_y(FACING_OFFSET - row.yaw)
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

    /// The facing convention, pinned against the camera.
    ///
    /// A picture of one operator at one yaw cannot tell a correct rotation from
    /// a mirrored one — both look right at yaw 0 and disagree everywhere else —
    /// and this was first pinned against `bodies.rs`, whose box rig was itself
    /// mirrored. The camera is the definition that cannot be wrong without the
    /// player noticing: it is where they are looking. A body at `yaw` has to
    /// face the way a camera at that yaw sees.
    #[test]
    fn the_model_faces_where_its_player_is_looking() {
        for step in 0..16 {
            let yaw = step as f32 * std::f32::consts::TAU / 16.0;
            let mut r = row("them");
            r.yaw = yaw;

            // Mixamo's characters face model +Z.
            let facing = model_matrix(&r) * glam::Vec4::new(0.0, 0.0, 1.0, 0.0);
            let expected = crate::camera::Camera {
                yaw: yaw.to_degrees(),
                ..Default::default()
            }
            .forward();

            let got = Vec3::new(facing.x, facing.y, facing.z).normalize();
            assert!(
                got.abs_diff_eq(expected, 1e-5),
                "at yaw {yaw}: model faces {got}, its player looks along {expected}"
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
    fn a_player_who_swaps_sides_gets_the_other_skeleton() {
        // The two operators are different rigs (34 bones and 65). A pose kept
        // across the half-time swap used to be reset from the wrong one, which
        // is a slice-length panic in the frame loop.
        let mut squad = Squad::load().expect("operator");
        let mut r = row("them");
        squad.update(1.0 / 60.0, std::slice::from_ref(&r), "me");
        r.team = 1;
        let poses = squad.update(1.0 / 60.0, std::slice::from_ref(&r), "me");
        assert_eq!(poses[0].bones.len(), 65);
        r.team = 0;
        let poses = squad.update(1.0 / 60.0, std::slice::from_ref(&r), "me");
        assert_eq!(poses[0].bones.len(), 34);
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
        let mut croucher = row("them");
        for step in 0..40 {
            croucher.crouch = if step > 5 { 1.0 } else { 0.0 };
            squad.update(1.0 / 60.0, &[croucher.clone()], "me");
        }
        let actor = &squad.actors["them"];
        assert_eq!(actor.current.as_deref(), Some("rifle_crouch_walk_to_kneel"));
        assert!(
            actor.previous.is_none(),
            "the fade should have completed and released the idle clip"
        );
    }

    const DT: f32 = 1.0 / 60.0;

    /// A bone's world position, as of the last `update`.
    fn bone_at(squad: &Squad, r: &PlayerRow, bone: &str) -> Vec3 {
        let op = if r.team == 1 {
            squad.operator_t()
        } else {
            squad.operator()
        };
        squad.actors[&r.id]
            .pose
            .bone_matrix(op, bone, model_matrix(r))
            .expect(bone)
            .w_axis
            .truncate()
    }

    /// Players looked like they were skating because they were: the clips' feet
    /// covered a fifth of the ground the body did. Stated as the measurement —
    /// how far a foot that is on the ground moves — on both shipped rigs, at a
    /// walk and a run, in all eight directions.
    #[test]
    fn a_foot_on_the_ground_stays_where_it_is() {
        for team in [0, 1] {
            for (speed, share) in [(4.0f32, 0.4), (18.0, 0.12)] {
                for dir in 0..8 {
                    let mut squad = Squad::load().expect("operator");
                    let mut r = row("them");
                    r.team = team;
                    (r.x, r.y, r.z, r.yaw) = (40.0, 37.5, 3.0, 0.7);
                    let angle = r.yaw + dir as f32 * std::f32::consts::FRAC_PI_4;
                    let ankle_rest = if team == 1 { &squad.rig_t } else { &squad.rig }
                        .legs
                        .as_ref()
                        .expect("legs")
                        .ankle_height;

                    let mut last = [Vec3::ZERO; 2];
                    let mut down = [false; 2];
                    let (mut slid, mut travelled, mut grounded) = (0.0f32, 0.0f32, 0u32);
                    let frames = 240;
                    for i in 0..frames {
                        r.x += speed * angle.cos() * DT;
                        r.y += speed * angle.sin() * DT;
                        squad.update(DT, std::slice::from_ref(&r), "me");
                        let settled = i >= 60;
                        if settled {
                            travelled += speed * DT;
                        }
                        for (f, bone) in ["LeftFoot", "RightFoot"].into_iter().enumerate() {
                            let at = bone_at(&squad, &r, bone);
                            // On the ground is where the ankle is as low as a
                            // flat foot puts it.
                            let is_down = at.y - r.z < ankle_rest + 0.03;
                            if settled && is_down && down[f] {
                                slid += (at.x - last[f].x).hypot(at.z - last[f].z);
                                grounded += 1;
                            }
                            down[f] = is_down;
                            last[f] = at;
                        }
                    }
                    // A leg that cannot reach its target never reads as down,
                    // and would pass the slide test by never being measured.
                    let on_ground = grounded as f32 / (2 * (frames - 60)) as f32;
                    assert!(
                        on_ground > share,
                        "team {team} speed {speed} direction {dir}: on the ground {on_ground}"
                    );
                    assert!(
                        slid / travelled < 0.02,
                        "team {team} speed {speed} direction {dir}: slid {slid} over {travelled}"
                    );
                }
            }
        }
    }

    /// Degrees between a direction and where the row is looking.
    fn off_the_look(direction: Vec3, r: &PlayerRow) -> f32 {
        let look = Vec3::new(
            r.yaw.cos() * r.pitch.cos(),
            r.pitch.sin(),
            r.yaw.sin() * r.pitch.cos(),
        );
        direction.normalize().angle_between(look).to_degrees()
    }

    /// How far the head is cocked to one side: the height of its ear-to-ear
    /// axis, which is zero on a level head.
    fn head_tilt(squad: &Squad, r: &PlayerRow) -> f32 {
        let rig = if r.team == 1 { &squad.rig_t } else { &squad.rig };
        let pose = &squad.actors[&r.id].pose;
        let (forward, up) = rig.head_axes.expect("head axes");
        let head = pose.global_rotation(*rig.neck.last().expect("head"));
        (head * up.cross(forward)).y.abs()
    }

    /// Which way the barrel and the face point, in world space.
    fn aim_of(squad: &Squad, r: &PlayerRow) -> (Vec3, Vec3) {
        let rig = if r.team == 1 { &squad.rig_t } else { &squad.rig };
        let pose = &squad.actors[&r.id].pose;
        let model = model_matrix(r);
        let hand = pose.global_rotation(rig.hand.expect("hand"));
        let head = pose.global_rotation(*rig.neck.last().expect("head"));
        (
            model.transform_vector3(hand * Vec3::Y),
            model.transform_vector3(head * rig.head_axes.expect("head axes").0),
        )
    }

    /// A moving player's rifle pointed across their chest, and nobody's pitch
    /// reached their weapon exactly. Both shipped rigs, every posture, looking
    /// down, level and up: the barrel and the face are on the look direction.
    #[test]
    fn the_rifle_and_the_face_point_down_the_look() {
        // (name, crouch, grounded, forward and strafe speed, fires)
        let states: [(&str, f32, bool, (f32, f32), bool); 9] = [
            ("standing", 0.0, true, (0.0, 0.0), false),
            ("running forward", 0.0, true, (18.0, 0.0), false),
            ("strafing", 0.0, true, (0.0, 9.0), false),
            ("backing up", 0.0, true, (-9.0, 0.0), false),
            ("on a diagonal", 0.0, true, (9.0, -9.0), false),
            ("crouched", 1.0, true, (0.0, 0.0), false),
            ("crouch-walking", 1.0, true, (5.0, 0.0), false),
            ("in the air", 0.0, false, (12.0, 0.0), false),
            ("firing on the move", 0.0, true, (18.0, 0.0), true),
        ];
        for team in [0, 1] {
            for (name, crouch, ground, (forward, strafe), fires) in states {
                for pitch in [-1.0f32, 0.0, 1.0] {
                    for yaw in [0.0f32, 2.3] {
                        let mut squad = Squad::load().expect("operator");
                        let mut r = row("them");
                        r.team = team;
                        (r.x, r.y, r.z) = (40.0, 37.5, 3.0);
                        (r.yaw, r.pitch, r.crouch, r.ground) = (yaw, pitch, crouch, ground);
                        for i in 0..50 {
                            r.x += (forward * yaw.cos() - strafe * yaw.sin()) * DT;
                            r.y += (forward * yaw.sin() + strafe * yaw.cos()) * DT;
                            if fires && i == 45 {
                                squad.note_shot("them");
                            }
                            squad.update(DT, std::slice::from_ref(&r), "me");
                            // Every frame, not only the settled one: a
                            // crossfade or a stride is exactly when the old
                            // fixed lean drifted off.
                            let (barrel, face) = aim_of(&squad, &r);
                            let at = format!("team {team}, {name}, pitch {pitch}, yaw {yaw}, frame {i}");
                            assert!(off_the_look(barrel, &r) < 3.0, "{at}: barrel {barrel}");
                            assert!(off_the_look(face, &r) < 3.0, "{at}: face {face}");
                            assert!(head_tilt(&squad, &r) < 0.05, "{at}: head cocked");
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn a_reload_takes_the_rifle_off_the_line_while_the_head_stays_on_it() {
        let mut squad = Squad::load().expect("operator");
        let mut r = row("them");
        (r.yaw, r.pitch) = (1.1, 0.4);
        squad.update(DT, std::slice::from_ref(&r), "me");
        squad.note_reload("them");
        let mut most = 0.0f32;
        for _ in 0..60 {
            squad.update(DT, std::slice::from_ref(&r), "me");
            let (barrel, face) = aim_of(&squad, &r);
            most = most.max(off_the_look(barrel, &r));
            assert!(off_the_look(face, &r) < 3.0, "face {face}");
        }
        assert!(most > 10.0, "the rifle never left the line: {most}");
    }
}
