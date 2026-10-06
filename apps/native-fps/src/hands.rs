//! The skinned first-person hands: `hassault-hands.glb`, posed onto a weapon.
//!
//! The browser's `hands.ts`, in this client's terms, reading the same file —
//! built by `tools/blender/generate_arms.py` and compiled in here the way the
//! weapon props are. `arms.rs` keeps the procedural arms as the fallback, drawn
//! when this asset will not parse.
//!
//! ## The fist goes on the grip, and the arm is worked back from it
//!
//! A grip anchor (`models/grips.json`) is the centre of the closed fist — the
//! rig's non-deforming `grip` bone — plus the direction the hand points and the
//! direction of the hole through the fist. Those fix the hand's rotation; the
//! wrist is the grip minus where the grip sits in the hand; two-bone IK reaches
//! the wrist from the shoulder. Everything is relative to the rest pose read
//! out of the file, never to hardcoded axes, except the two flex axes that say
//! which way a finger folds — which the generator carries as the same vectors.
//!
//! ## Skinned on the CPU, drawn by the prop pipeline
//!
//! Twenty bones and ten thousand vertices: skinned here into camera space and
//! drawn with the weapon props' textured, lit shader, so a glove and the gun in
//! it are shaded by one model. The left arm is the right one mirrored in x —
//! solved as a right arm in mirrored space, as the browser does. The prop
//! pipeline does not cull, so the mirror needs no winding fix.

use std::collections::HashMap;
use std::sync::OnceLock;

use glam::{Mat3, Mat4, Quat, Vec3};

use crate::arms::solve_two_bone;
use crate::character::{MaterialDef, Operator, Pose, Primitive, TextureImage};
use crate::prop::PropVertex;

pub const HANDS_GLB: &[u8] = include_bytes!("../../web/public/hassault-hands.glb");

/// `hands.ts`'s `FINGER_FLEX_AXIS` and `THUMB_FLEX_AXIS`: the generator's
/// `FINGER_FLEX` and `THUMB_FLEX` in glTF axes.
const FINGER_FLEX_AXIS: Vec3 = Vec3::Y;
fn thumb_flex_axis() -> Vec3 {
    Vec3::new(-0.7, 0.0, 0.57).normalize()
}

/// How a curl of 1 is shared along a finger's three joints. `hands.ts`'s `JOINT_FLEX`.
const JOINT_FLEX_FINGER: [f32; 3] = [1.35, 1.6, 1.0];
const JOINT_FLEX_THUMB: [f32; 3] = [0.35, 0.6, 0.8];

const FINGERS: [&str; 5] = ["thumb", "index", "middle", "ring", "pinky"];

/// Everything a hand needs from the weapon this frame, in the arm's own space.
#[derive(Debug, Clone, Copy)]
pub struct HandTarget {
    pub grip: Vec3,
    pub aim: Vec3,
    pub up: Vec3,
    pub curl: [f32; 5],
    /// The handle's thickness around the fist's centre: no finger closes past
    /// touching it. Zero for "closes on nothing". The thumb settles on the
    /// handle's surface instead (`thumb_curl`): it lies along it, not around it.
    pub radius: f32,
}

/// The parsed asset and its rest pose. Immutable, shared by every view model.
pub struct HandsAsset {
    op: Operator,
    rest: Rest,
}

struct Rest {
    node: HashMap<String, usize>,
    world_rot: HashMap<String, Quat>,
    world_pos: HashMap<String, Vec3>,
    local_rot: HashMap<String, Quat>,
    flex_axis: HashMap<String, Vec3>,
    aim: Vec3,
    up: Vec3,
    grip_offset: Vec3,
    upper_len: f32,
    lower_len: f32,
    armature_rot: Quat,
    armature_pos: Vec3,
}

/// The asset, parsed once. `None` when it will not parse — reported, and the
/// procedural arms draw instead.
pub fn asset() -> Option<&'static HandsAsset> {
    static ASSET: OnceLock<Option<HandsAsset>> = OnceLock::new();
    ASSET
        .get_or_init(|| match HandsAsset::from_slice(HANDS_GLB) {
            Ok(a) => Some(a),
            Err(e) => {
                crate::divergence::note_prop("hands", &e);
                None
            }
        })
        .as_ref()
}

fn bone_names() -> Vec<String> {
    let mut names: Vec<String> = ["upper", "fore", "hand", "grip"]
        .iter()
        .map(|s| s.to_string())
        .collect();
    for f in FINGERS {
        for i in 1..=3 {
            names.push(format!("{f}_{i}"));
        }
    }
    names
}

impl HandsAsset {
    pub fn from_slice(bytes: &[u8]) -> Result<HandsAsset, String> {
        let op = Operator::from_slice(bytes)?;
        let mut node = HashMap::new();
        for name in bone_names() {
            let index = op
                .node_named(&name)
                .ok_or_else(|| format!("hands rig has no bone \"{name}\""))?;
            node.insert(name, index);
        }
        // The rest pose's globals, from a pose nobody has touched.
        let mut pose = Pose::new(&op);
        let mut scratch = vec![Mat4::IDENTITY; op.bone_count()];
        pose.skinning(&op, Mat4::IDENTITY, &mut scratch);
        let mut world_rot = HashMap::new();
        let mut world_pos = HashMap::new();
        let mut local_rot = HashMap::new();
        for (name, &index) in &node {
            let (_, rot, pos) = pose.global(index).to_scale_rotation_translation();
            world_rot.insert(name.clone(), rot.normalize());
            world_pos.insert(name.clone(), pos);
            local_rot.insert(name.clone(), op.rest_trs(index).rotation);
        }
        let mut flex_axis = HashMap::new();
        for f in FINGERS {
            for i in 1..=3 {
                let name = format!("{f}_{i}");
                let axis = if f == "thumb" {
                    thumb_flex_axis()
                } else {
                    FINGER_FLEX_AXIS
                };
                flex_axis.insert(
                    name.clone(),
                    (world_rot[&name].inverse() * axis).normalize(),
                );
            }
        }
        let knuckles = (world_pos["index_1"]
            + world_pos["middle_1"]
            + world_pos["ring_1"]
            + world_pos["pinky_1"])
            * 0.25;
        let aim = (knuckles - world_pos["hand"]).normalize();
        let up_raw = world_rot["grip"] * Vec3::Y;
        let up = (up_raw - aim * up_raw.dot(aim)).normalize();
        let (armature_rot, armature_pos) = match op.parent_of(node["upper"]) {
            Some(parent) => {
                let (_, r, t) = pose.global(parent).to_scale_rotation_translation();
                (r.normalize(), t)
            }
            None => (Quat::IDENTITY, Vec3::ZERO),
        };
        let rest = Rest {
            upper_len: world_pos["fore"].distance(world_pos["upper"]),
            lower_len: world_pos["hand"].distance(world_pos["fore"]),
            grip_offset: world_pos["grip"] - world_pos["hand"],
            node,
            world_rot,
            world_pos,
            local_rot,
            flex_axis,
            aim,
            up,
            armature_rot,
            armature_pos,
        };
        Ok(HandsAsset { op, rest })
    }

    pub fn materials(&self) -> &[MaterialDef] {
        &self.op.materials
    }

    pub fn textures(&self) -> &[TextureImage] {
        &self.op.textures
    }

    pub fn primitives(&self) -> &[Primitive] {
        &self.op.primitives
    }

    /// Vertices per arm: the left arm's copy starts at this offset.
    pub fn vertex_count(&self) -> usize {
        self.op.vertices.len()
    }

    pub fn upper_len(&self) -> f32 {
        self.rest.upper_len
    }

    pub fn lower_len(&self) -> f32 {
        self.rest.lower_len
    }
}

/// The rotation taking frame (a0, u0) onto (a1, u1). Both pairs orthonormal.
fn frame_rotation(a0: Vec3, u0: Vec3, a1: Vec3, u1: Vec3) -> Quat {
    let from = Mat3::from_cols(a0, u0, a0.cross(u0));
    let to = Mat3::from_cols(a1, u1, a1.cross(u1));
    Quat::from_mat3(&(to * from.transpose())).normalize()
}

/// `v` with its component along `axis` removed, or any perpendicular.
fn perpendicular(v: Vec3, axis: Vec3) -> Vec3 {
    let out = v - axis * v.dot(axis);
    if out.length_squared() < 1e-8 {
        let any = if axis.y.abs() < 0.9 { Vec3::Y } else { Vec3::X };
        return (any - axis * any.dot(axis)).normalize();
    }
    out.normalize()
}

/// How far a wrist bends before the hand has to follow the forearm: ~40 degrees.
/// `hands.ts`'s `MAX_WRIST_BEND`.
pub const MAX_WRIST_BEND: f32 = 0.7;
/// Passes of the wrist/forearm fixed point. `hands.ts`'s `WRIST_PASSES`.
const WRIST_PASSES: usize = 3;

/// `aim` turned toward `forearm` until it is no more than `MAX_WRIST_BEND` from
/// it; unchanged when it already is. `hands.ts`'s `limitWristAim`.
pub fn limit_wrist_aim(forearm: Vec3, aim: Vec3) -> Vec3 {
    if forearm.angle_between(aim) <= MAX_WRIST_BEND {
        return aim;
    }
    let cross = forearm.cross(aim);
    let axis = if cross.length_squared() < 1e-8 {
        perpendicular(Vec3::Y, forearm)
    } else {
        cross.normalize()
    };
    (Quat::from_axis_angle(axis, MAX_WRIST_BEND) * forearm).normalize()
}

/// How far past the handle's surface a fingertip's pad is allowed to sit: the
/// pad is soft and the mesh is a glove, so touching is a hair inside.
const FINGERTIP_PAD: f32 = 0.015;
/// The coarse step of the contact search, in curl. `hands.ts`'s `CONTACT_STEP`.
const CONTACT_STEP: f32 = 0.05;
/// A curl no finger passes. `hands.ts`'s `MAX_CURL`.
const MAX_CURL: f32 = 1.2;

/// A finger's tip at `curl`, in the hand's rest frame — the same frame as
/// `Rest::world_pos` — walked down the chain with the same folds `solve` applies.
/// `hands.ts`'s `fingertip`.
fn fingertip(rest: &Rest, finger: &str, curl: f32) -> Vec3 {
    let ratios = if finger == "thumb" {
        JOINT_FLEX_THUMB
    } else {
        JOINT_FLEX_FINGER
    };
    let mut rot = rest.world_rot["hand"];
    let mut pos = rest.world_pos["hand"];
    let mut parent = "hand".to_string();
    for j in 1..=3 {
        let name = format!("{finger}_{j}");
        let local =
            rest.world_rot[&parent].inverse() * (rest.world_pos[&name] - rest.world_pos[&parent]);
        pos += rot * local;
        let fold = Quat::from_axis_angle(rest.flex_axis[&name], curl * ratios[j - 1]);
        rot = (rot * rest.local_rot[&name] * fold).normalize();
        parent = name;
    }
    // The tip is one more distal segment on: the last bone's own length, along
    // the line the previous bone ran into it.
    let last = format!("{finger}_3");
    let before = format!("{finger}_2");
    let span = rest.world_pos[&last] - rest.world_pos[&before];
    let local_tip = rest.world_rot[&last].inverse() * span;
    pos + rot * local_tip
}

/// How far a finger can close before its tip meets a handle `radius` thick
/// around the fist's centre: the fist's hole is the `up` axis through the `grip`
/// bone, so a handle is a cylinder on it. `MAX_CURL` when it never gets there,
/// or when `radius` is zero. `hands.ts`'s `contactCurl`.
fn contact_curl(rest: &Rest, finger: &str, radius: f32) -> f32 {
    if radius <= 0.0 {
        return MAX_CURL;
    }
    let target = radius + FINGERTIP_PAD;
    let centre = rest.world_pos["grip"];
    let distance = |curl: f32| {
        let d = fingertip(rest, finger, curl) - centre;
        (d - rest.up * d.dot(rest.up)).length()
    };
    let mut last = distance(0.0);
    if last <= target {
        return 0.0;
    }
    let mut curl = 0.0;
    while curl < MAX_CURL {
        let next = (curl + CONTACT_STEP).min(MAX_CURL);
        let here = distance(next);
        if here <= target {
            // Linear between the two samples: the tip's path is smooth.
            let t = (last - target) / (last - here).max(1e-6);
            return curl + (next - curl) * t.clamp(0.0, 1.0);
        }
        last = here;
        curl = next;
    }
    MAX_CURL
}

/// How closed the thumb ends up for a handle `radius` thick, given the curl it
/// was asked for: that curl if its tip is clear of the handle, otherwise the
/// nearest curl that is. A thumb asked to close all the way (`MAX_CURL`) so comes
/// to rest touching the handle, wherever the handle's thickness puts that, and is
/// never inside it. Unchanged for a zero radius. `hands.ts`'s `thumbCurl`.
fn thumb_curl(rest: &Rest, radius: f32, requested: f32) -> f32 {
    if radius <= 0.0 {
        return requested;
    }
    let target = radius + FINGERTIP_PAD;
    let centre = rest.world_pos["grip"];
    let distance = |curl: f32| {
        let d = fingertip(rest, "thumb", curl) - centre;
        (d - rest.up * d.dot(rest.up)).length()
    };
    let clear = |curl: f32| distance(curl) >= target;
    if clear(requested) {
        return requested;
    }
    let mut step = CONTACT_STEP;
    while step <= MAX_CURL {
        // Nearest first, and the way it was asked to go first: a thumb asked to
        // close further than it can backs off rather than springing open.
        for candidate in [requested - step, requested + step] {
            if (0.0..=MAX_CURL).contains(&candidate) && clear(candidate) {
                return candidate;
            }
        }
        step += CONTACT_STEP;
    }
    // A handle thicker than any pose clears: the furthest the thumb gets.
    let mut best = requested;
    let mut curl = 0.0;
    while curl <= MAX_CURL {
        if distance(curl) > distance(best) {
            best = curl;
        }
        curl += CONTACT_STEP;
    }
    best
}

/// Where the shoulder has to be for the arm to reach `wrist`: straight forward
/// when that is enough, along the line to the hand when it is not. `hands.ts`'s
/// `slideShoulder`, which explains why.
pub fn slide_shoulder(shoulder: Vec3, wrist: Vec3, reach: f32) -> Vec3 {
    let d = wrist - shoulder;
    let length = d.length();
    if length <= reach {
        return shoulder;
    }
    let lateral = reach * reach - d.x * d.x - d.y * d.y;
    if lateral > 0.0 {
        let k = -d.z - lateral.sqrt();
        if k >= 0.0 {
            return shoulder - Vec3::Z * k;
        }
    }
    shoulder + d * ((length - reach) / length)
}

/// Where one posed arm's joints ended up.
#[derive(Debug, Clone, Copy)]
pub struct ArmSolve {
    pub wrist: Vec3,
    pub elbow: Vec3,
    pub stretched: bool,
}

/// One arm's pose, kept per view model.
pub struct ArmPose {
    pose: Pose,
    skin: Vec<Mat4>,
}

impl ArmPose {
    pub fn new(asset: &HandsAsset) -> ArmPose {
        ArmPose {
            pose: Pose::new(&asset.op),
            skin: vec![Mat4::IDENTITY; asset.op.bone_count()],
        }
    }

    /// Pose the arm: fist onto `target`, shoulder at `shoulder`, both in the
    /// arm's own space. `hands.ts`'s `poseArm`, line for line.
    pub fn solve(
        &mut self,
        asset: &HandsAsset,
        shoulder: Vec3,
        target: &HandTarget,
        pole: Vec3,
    ) -> ArmSolve {
        let rest = &asset.rest;
        let aim = if target.aim.length_squared() < 1e-8 {
            rest.aim
        } else {
            target.aim.normalize()
        };
        let wanted_aim = aim;
        let reach = (rest.upper_len + rest.lower_len) * 0.985;
        // Each pass is `hands.ts`'s: the hand's frame, the wrist that puts the
        // fist on the grip, the arm that reaches it.
        let arm = |aim: Vec3| {
            let up = perpendicular(target.up, aim);
            let hand_delta = frame_rotation(rest.aim, rest.up, aim, up);
            let wrist = target.grip - hand_delta * rest.grip_offset;
            let shoulder = slide_shoulder(shoulder, wrist, reach);
            let (elbow, stretched) =
                solve_two_bone(shoulder, wrist, rest.upper_len, rest.lower_len, pole);
            (up, hand_delta, wrist, shoulder, elbow, stretched)
        };
        let mut aim = aim;
        let (mut up, mut hand_delta, mut wrist, mut shoulder, mut elbow, mut stretched) = arm(aim);
        for _ in 0..WRIST_PASSES {
            let forearm = (wrist - elbow).normalize_or(aim);
            let limited = limit_wrist_aim(forearm, wanted_aim);
            if limited.angle_between(aim) < 1e-4 {
                break;
            }
            aim = limited;
            (up, hand_delta, wrist, shoulder, elbow, stretched) = arm(aim);
        }

        let rest_dir =
            |from: &str, to: &str| (rest.world_pos[to] - rest.world_pos[from]).normalize();
        let segment = |from: &str, to: &str, a: Vec3, b: Vec3| {
            let dir0 = rest_dir(from, to);
            let up0 = perpendicular(rest.up, dir0);
            let dir1 = (b - a).normalize_or(dir0);
            let up1 = perpendicular(up, dir1);
            frame_rotation(dir0, up0, dir1, up1) * rest.world_rot[from]
        };
        let upper_world = segment("upper", "fore", shoulder, elbow);
        let wrist_reached = elbow + (wrist - elbow).normalize_or(aim) * rest.lower_len;
        let fore_world = segment("fore", "hand", elbow, wrist_reached);
        let hand_world = hand_delta * rest.world_rot["hand"];

        let parent_inv = rest.armature_rot.inverse();
        self.pose.set_local(
            rest.node["upper"],
            (parent_inv * upper_world).normalize(),
            Some(parent_inv * (shoulder - rest.armature_pos)),
        );
        self.pose.set_local(
            rest.node["fore"],
            (upper_world.inverse() * fore_world).normalize(),
            None,
        );
        self.pose.set_local(
            rest.node["hand"],
            (fore_world.inverse() * hand_world).normalize(),
            None,
        );

        for (i, f) in FINGERS.iter().enumerate() {
            let mut amount = target.curl[i].clamp(0.0, MAX_CURL);
            amount = if *f == "thumb" {
                thumb_curl(rest, target.radius, amount)
            } else {
                amount.min(contact_curl(rest, f, target.radius))
            };
            let ratios = if *f == "thumb" {
                JOINT_FLEX_THUMB
            } else {
                JOINT_FLEX_FINGER
            };
            for j in 1..=3 {
                let name = format!("{f}_{j}");
                let fold = Quat::from_axis_angle(rest.flex_axis[&name], amount * ratios[j - 1]);
                self.pose.set_local(
                    rest.node[&name],
                    (rest.local_rot[&name] * fold).normalize(),
                    None,
                );
            }
        }
        self.pose
            .skinning(&asset.op, Mat4::IDENTITY, &mut self.skin);
        ArmSolve {
            wrist: wrist_reached,
            elbow,
            stretched,
        }
    }

    /// A bone's position as last solved, in the arm's own space.
    pub fn bone_position(&self, asset: &HandsAsset, name: &str) -> Option<Vec3> {
        let node = *asset.rest.node.get(name)?;
        Some(self.pose.global(node).w_axis.truncate())
    }

    /// Skin the arm into `out`, mirrored in x for the left arm.
    pub fn skin_into(&self, asset: &HandsAsset, mirror: bool, out: &mut Vec<PropVertex>) {
        let sx = if mirror { -1.0 } else { 1.0 };
        for v in &asset.op.vertices {
            let p = Vec3::from_array(v.position);
            let n = Vec3::from_array(v.normal);
            let mut sp = Vec3::ZERO;
            let mut sn = Vec3::ZERO;
            for k in 0..4 {
                let w = v.weights[k];
                if w <= 0.0 {
                    continue;
                }
                let m = self.skin[v.joints[k] as usize];
                sp += m.transform_point3(p) * w;
                sn += m.transform_vector3(n) * w;
            }
            let sn = sn.normalize_or(Vec3::Y);
            out.push(PropVertex {
                position: [sp.x * sx, sp.y, sp.z],
                normal: [sn.x * sx, sn.y, sn.z],
                uv: v.uv,
            });
        }
    }
}

/// Both arms, solved and skinned: the right onto `primary`, the left onto
/// `support` (or not drawn, for a one-handed weapon). Returns how many vertices
/// were written for each arm.
pub struct Arms {
    right: ArmPose,
    left: ArmPose,
}

impl Arms {
    pub fn new(asset: &HandsAsset) -> Arms {
        Arms {
            right: ArmPose::new(asset),
            left: ArmPose::new(asset),
        }
    }

    pub fn build(
        &mut self,
        asset: &HandsAsset,
        primary: &HandTarget,
        support: Option<&HandTarget>,
        out: &mut Vec<PropVertex>,
    ) -> (usize, usize) {
        out.clear();
        self.right.solve(
            asset,
            crate::arms::SHOULDER_R,
            primary,
            Vec3::new(1.0, -1.0, 0.0),
        );
        self.right.skin_into(asset, false, out);
        let right = out.len();
        let Some(support) = support else {
            return (right, 0);
        };
        let m = |v: Vec3| Vec3::new(-v.x, v.y, v.z);
        let mirrored = HandTarget {
            grip: m(support.grip),
            aim: m(support.aim),
            up: m(support.up),
            curl: support.curl,
            radius: support.radius,
        };
        self.left.solve(
            asset,
            m(crate::arms::SHOULDER_L),
            &mirrored,
            Vec3::new(1.0, -1.0, 0.0),
        );
        self.left.skin_into(asset, true, out);
        (right, out.len() - right)
    }

    pub fn right(&self) -> &ArmPose {
        &self.right
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIST: [f32; 5] = [1.0; 5];

    fn asset() -> HandsAsset {
        HandsAsset::from_slice(HANDS_GLB).expect("hands GLB parses")
    }

    #[test]
    fn the_rig_matches_the_two_bone_solve() {
        let a = asset();
        assert!((a.upper_len() - crate::arms::UPPER_LEN).abs() < 1e-4);
        assert!((a.lower_len() - crate::arms::LOWER_LEN).abs() < 1e-4);
    }

    #[test]
    fn the_fist_closes_on_the_target() {
        let a = asset();
        let mut arm = ArmPose::new(&a);
        let target = HandTarget {
            grip: Vec3::new(0.28, -0.35, -0.55),
            aim: Vec3::new(0.0, 0.3, -1.0),
            up: Vec3::new(0.0, 1.0, 0.3),
            curl: FIST,
            radius: 0.0,
        };
        let solve = arm.solve(
            &a,
            crate::arms::SHOULDER_R,
            &target,
            Vec3::new(1.0, -1.0, 0.0),
        );
        assert!(!solve.stretched);
        let grip = arm.bone_position(&a, "grip").unwrap();
        assert!(
            grip.distance(target.grip) < 1e-3,
            "grip at {grip}, wanted {}",
            target.grip
        );
    }

    #[test]
    fn an_unreachable_target_skins_to_finite_vertices() {
        let a = asset();
        let mut arms = Arms::new(&a);
        let far = HandTarget {
            grip: Vec3::new(0.0, 0.0, -9.0),
            aim: Vec3::NEG_Z,
            up: Vec3::NEG_Z,
            curl: FIST,
            radius: 0.0,
        };
        let mut out = Vec::new();
        let (right, left) = arms.build(&a, &far, Some(&far), &mut out);
        assert_eq!(right, a.vertex_count());
        assert_eq!(left, a.vertex_count());
        assert!(out.iter().all(|v| v
            .position
            .iter()
            .chain(v.normal.iter())
            .all(|c| c.is_finite())));
    }

    #[test]
    fn a_finger_stops_where_its_tip_meets_the_handle() {
        let a = asset();
        let centre = a.rest.world_pos["grip"];
        let from_axis = |finger: &str, curl: f32| {
            let d = fingertip(&a.rest, finger, curl) - centre;
            (d - a.rest.up * d.dot(a.rest.up)).length()
        };
        for finger in ["index", "middle", "ring", "pinky"] {
            // No handle: closes as far as it is asked to.
            assert_eq!(contact_curl(&a.rest, finger, 0.0), MAX_CURL);
            // A handle: the tip stops on its surface, not inside it.
            let stop = contact_curl(&a.rest, finger, 0.07);
            assert!(stop > 0.0 && stop < MAX_CURL, "{finger} stops at {stop}");
            assert!((from_axis(finger, stop) - (0.07 + FINGERTIP_PAD)).abs() < 0.01);
            // A fatter handle stops it sooner.
            assert!(contact_curl(&a.rest, finger, 0.12) < stop);
        }
    }

    #[test]
    fn a_thumb_rests_on_the_handle_and_never_in_it() {
        let a = asset();
        let centre = a.rest.world_pos["grip"];
        let from_axis = |curl: f32| {
            let d = fingertip(&a.rest, "thumb", curl) - centre;
            (d - a.rest.up * d.dot(a.rest.up)).length()
        };
        // No handle: it does as it is told.
        assert_eq!(thumb_curl(&a.rest, 0.0, MAX_CURL), MAX_CURL);
        for radius in [0.05, 0.07, 0.09, 0.12] {
            let curl = thumb_curl(&a.rest, radius, MAX_CURL);
            assert!(
                from_axis(curl) >= (radius + FINGERTIP_PAD).min(0.12) - 1e-3,
                "thumb inside a {radius} handle at {curl}"
            );
        }
        // A thumb already clear of the handle is left where it was put.
        assert_eq!(thumb_curl(&a.rest, 0.05, 0.3), 0.3);
    }

    #[test]
    fn a_closed_fist_does_not_pass_through_its_handle() {
        let a = asset();
        let mut arm = ArmPose::new(&a);
        let target = |radius: f32| HandTarget {
            grip: Vec3::new(0.3, -0.35, -0.6),
            aim: Vec3::NEG_Z,
            up: Vec3::Y,
            curl: FIST,
            radius,
        };
        let tip = |arm: &mut ArmPose, radius: f32| {
            arm.solve(&a, crate::arms::SHOULDER_R, &target(radius), Vec3::new(1.0, -1.0, 0.0));
            arm.bone_position(&a, "middle_3").unwrap()
        };
        let bare = tip(&mut arm, 0.0);
        let held = tip(&mut arm, 0.09);
        // Held open by the handle: the tip is further from the fist's centre.
        let centre = Vec3::new(0.3, -0.35, -0.6);
        assert!(held.distance(centre) > bare.distance(centre) + 0.01);
    }

    #[test]
    fn a_wrist_bends_no_further_than_its_limit() {
        let forearm = Vec3::NEG_Z;
        let slight = Vec3::new(0.3, 0.0, -1.0).normalize();
        assert!(limit_wrist_aim(forearm, slight).angle_between(slight) < 1e-4);
        let limited = limit_wrist_aim(forearm, Vec3::X);
        assert!((limited.angle_between(forearm) - MAX_WRIST_BEND).abs() < 1e-3);
        assert!(limited.x > 0.0, "turned toward the aim, not away from it");
        let behind = limit_wrist_aim(forearm, Vec3::Z);
        assert!(behind.is_finite());
        assert!((behind.angle_between(forearm) - MAX_WRIST_BEND).abs() < 1e-3);
    }

    #[test]
    fn the_left_arm_is_on_the_left() {
        let a = asset();
        let mut arms = Arms::new(&a);
        let target = HandTarget {
            grip: Vec3::new(0.0, -0.3, -0.9),
            aim: Vec3::X,
            up: Vec3::NEG_Z,
            curl: FIST,
            radius: 0.0,
        };
        let right = HandTarget {
            grip: Vec3::new(0.3, -0.35, -0.55),
            ..target
        };
        let mut out = Vec::new();
        let (r, l) = arms.build(&a, &right, Some(&target), &mut out);
        let mean_x =
            |vs: &[PropVertex]| vs.iter().map(|v| v.position[0]).sum::<f32>() / vs.len() as f32;
        assert!(mean_x(&out[..r]) > 0.0, "right arm drawn on the left");
        assert!(
            mean_x(&out[r..r + l]) < mean_x(&out[..r]),
            "left arm not left of the right one"
        );
    }
}
