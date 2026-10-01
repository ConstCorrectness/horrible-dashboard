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
        let up = perpendicular(target.up, aim);
        let hand_delta = frame_rotation(rest.aim, rest.up, aim, up);

        let wrist = target.grip - hand_delta * rest.grip_offset;
        let shoulder = slide_shoulder(shoulder, wrist, (rest.upper_len + rest.lower_len) * 0.985);
        let (elbow, stretched) =
            solve_two_bone(shoulder, wrist, rest.upper_len, rest.lower_len, pole);

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
            let amount = target.curl[i].clamp(0.0, 1.2);
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
    fn the_left_arm_is_on_the_left() {
        let a = asset();
        let mut arms = Arms::new(&a);
        let target = HandTarget {
            grip: Vec3::new(0.0, -0.3, -0.9),
            aim: Vec3::X,
            up: Vec3::NEG_Z,
            curl: FIST,
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
