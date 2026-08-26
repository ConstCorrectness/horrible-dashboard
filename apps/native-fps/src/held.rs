//! The weapon in another player's hands.
//!
//! Separate from the operator's own geometry because it is not part of the
//! asset: the GLB is a character, not a character holding a rifle. The browser
//! solves this by parenting a procedural prop to the hand bone and letting the
//! skeleton carry it through every clip; this does the same thing with the same
//! numbers, only as a matrix rather than as a scene graph.
//!
//! The props stay **untextured boxes**, drawn into the same vertex stream as the
//! world. That is not a placeholder: a gun rendered at the fidelity of the
//! character would need its own art, and the thing that has to read at fifty
//! metres is *which* weapon, which a silhouette carries and a texture does not.
//!
//! Why a matrix emitter rather than reusing `push_oriented_box`: that one takes
//! a yaw and a pitch, which is exactly the assumption a skeleton breaks. A hand
//! at the top of a reload is rotated about all three axes, and no pair of Euler
//! angles applied at the body's origin can put a rifle in it.

use glam::{Mat4, Vec3};

use crate::animator::ActorPose;
use crate::renderer::Vertex;

/// Matte gun metal and polymer trim, matching `bodies.rs` so the two body paths
/// do not draw visibly different weapons while both exist.
const GUN_METAL: [f32; 3] = [0.15, 0.15, 0.17];
const GUN_TRIM: [f32; 3] = [0.35, 0.35, 0.38];

/// Where the prop sits relative to the hand, **in cubes**, and how big it is.
///
/// Mixamo's hand bone points down the fingers with Y up the back of the hand, so
/// a weapon modelled down +Z has to be rotated into it — that part is the
/// browser's `GRIP_ROTATION` verbatim, dialled in against `rifle_aiming_idle`,
/// the pose a player is in most of the time.
///
/// The *scale* is not the browser's, and this is the trap worth the paragraph.
/// The rig's internal unit is about **1/35th of a cube** — Mixamo authors in
/// centimetres, and the build script's scale-to-5.2-cubes lives on the armature
/// above the joints. So the hand's world matrix carries a scale of 0.0285, and
/// anything parented to it inherits that: a rifle stated in cubes comes out
/// 35 times too small and renders as a sub-pixel speck. Nothing errors, no
/// vertex is out of range, and the character simply appears empty-handed.
///
/// The fix is to state the weapon in the units it is actually measured in —
/// cubes — and take the hand's *orientation and position* while dropping its
/// scale. `strip_scale` below is that, and it is why this does not silently
/// break the next time the asset is re-exported at a different unit.
const GRIP_POSITION: Vec3 = Vec3::new(0.0, 0.05, 0.28);

/// Roughly a metre of rifle: a player is 5.2 cubes to 1.8 m, so a cube is about
/// 35 cm and a carbine's ~90 cm is a little over two and a half of them.
const GRIP_SCALE: f32 = 1.5;

/// Drop the scale from a bone's world matrix, keeping where it is and which way
/// it points. See `GRIP_POSITION`.
fn strip_scale(m: Mat4) -> Mat4 {
    let (_, rotation, translation) = m.to_scale_rotation_translation();
    Mat4::from_rotation_translation(rotation, translation)
}

/// One box of a weapon: half extents and an offset, in gun space — **+Z is down
/// the barrel**, +Y is up, +X is right.
struct Box {
    offset: Vec3,
    half: Vec3,
    color: [f32; 3],
}

const fn part(offset: [f32; 3], half: [f32; 3], color: [f32; 3]) -> Box {
    Box {
        offset: Vec3::new(offset[0], offset[1], offset[2]),
        half: Vec3::new(half[0], half[1], half[2]),
        color,
    }
}

/// The five weapon classes, by the slot the wire uses.
///
/// Proportions carried over from `bodies.rs`, re-expressed in gun space: what
/// was `[right, forward, up]` about the body's origin is `[x, z, y]` about the
/// muzzle line. They are deliberately still five distinguishable silhouettes —
/// a knife that reads as a rifle at range is a lie about what is about to
/// happen to you.
fn parts(slot: i32) -> &'static [Box] {
    const KNIFE: [Box; 2] = [
        part([0.0, 0.0, 0.30], [0.03, 0.10, 0.30], GUN_TRIM),
        part([0.0, -0.10, -0.05], [0.04, 0.10, 0.08], GUN_METAL),
    ];
    const PISTOL: [Box; 2] = [
        part([0.0, 0.0, 0.22], [0.06, 0.10, 0.25], GUN_METAL),
        part([0.0, -0.18, 0.02], [0.05, 0.14, 0.08], GUN_TRIM),
    ];
    const CARBINE: [Box; 4] = [
        part([0.0, 0.0, 0.20], [0.06, 0.12, 0.35], GUN_METAL),
        part([0.0, 0.04, 0.62], [0.03, 0.03, 0.20], GUN_TRIM),
        part([0.0, -0.16, 0.12], [0.05, 0.16, 0.09], GUN_METAL),
        part([0.0, 0.0, -0.28], [0.05, 0.09, 0.18], GUN_TRIM),
    ];
    const SHOTGUN: [Box; 3] = [
        part([0.0, 0.0, 0.24], [0.08, 0.13, 0.40], GUN_METAL),
        part([0.0, -0.09, 0.30], [0.05, 0.06, 0.26], GUN_TRIM),
        part([0.0, -0.02, -0.30], [0.06, 0.11, 0.20], GUN_TRIM),
    ];
    const SNIPER: [Box; 4] = [
        part([0.0, 0.0, 0.26], [0.05, 0.11, 0.46], GUN_METAL),
        part([0.0, 0.03, 0.86], [0.025, 0.025, 0.26], GUN_TRIM),
        // The scope, which is most of what tells a sniper from a carbine at the
        // distance a sniper is used from.
        part([0.0, 0.15, 0.18], [0.035, 0.035, 0.22], GUN_TRIM),
        part([0.0, -0.03, -0.34], [0.05, 0.10, 0.22], GUN_METAL),
    ];
    match slot {
        0 => &KNIFE,
        1 => &PISTOL,
        3 => &SHOTGUN,
        4 => &SNIPER,
        // Slot 2 is the carbine, and it is also the fallback: an unknown slot
        // drawing nothing would make an empty-handed player look like a bug in
        // the animation rather than a gap in the weapon table.
        _ => &CARBINE,
    }
}

/// Every drawn player's weapon, as world-space triangles.
pub fn build(poses: &[ActorPose]) -> Vec<Vertex> {
    let mut out = Vec::new();
    for pose in poses {
        let Some(grip) = pose.grip else { continue };
        // Rotation first as the browser's Euler XYZ composes it, then the
        // uniform scale that takes a weapon modelled in cube units down into a
        // hand.
        let local = Mat4::from_translation(GRIP_POSITION)
            * Mat4::from_rotation_x(-std::f32::consts::FRAC_PI_2)
            * Mat4::from_rotation_z(std::f32::consts::FRAC_PI_2)
            * Mat4::from_scale(Vec3::splat(GRIP_SCALE));
        let transform = strip_scale(grip) * local;
        for part in parts(pose.weapon) {
            push_box(&mut out, transform, part);
        }
    }
    out
}

/// Emit one box, transformed.
///
/// Normals go through the same matrix with `w = 0` and are renormalised: the
/// grip carries a 0.55 scale, and a normal scaled with the position is a normal
/// of length 0.55, which the shader's Lambert term reads as a surface facing 45%
/// away from wherever it actually points.
fn push_box(out: &mut Vec<Vertex>, transform: Mat4, part: &Box) {
    // Six faces, each two triangles, wound counter-clockwise seen from outside.
    const FACES: [([f32; 3], [[f32; 3]; 4]); 6] = [
        // +X
        (
            [1.0, 0.0, 0.0],
            [
                [1.0, -1.0, -1.0],
                [1.0, -1.0, 1.0],
                [1.0, 1.0, 1.0],
                [1.0, 1.0, -1.0],
            ],
        ),
        // -X
        (
            [-1.0, 0.0, 0.0],
            [
                [-1.0, -1.0, 1.0],
                [-1.0, -1.0, -1.0],
                [-1.0, 1.0, -1.0],
                [-1.0, 1.0, 1.0],
            ],
        ),
        // +Y
        (
            [0.0, 1.0, 0.0],
            [
                [-1.0, 1.0, -1.0],
                [1.0, 1.0, -1.0],
                [1.0, 1.0, 1.0],
                [-1.0, 1.0, 1.0],
            ],
        ),
        // -Y
        (
            [0.0, -1.0, 0.0],
            [
                [-1.0, -1.0, 1.0],
                [1.0, -1.0, 1.0],
                [1.0, -1.0, -1.0],
                [-1.0, -1.0, -1.0],
            ],
        ),
        // +Z
        (
            [0.0, 0.0, 1.0],
            [
                [-1.0, -1.0, 1.0],
                [-1.0, 1.0, 1.0],
                [1.0, 1.0, 1.0],
                [1.0, -1.0, 1.0],
            ],
        ),
        // -Z
        (
            [0.0, 0.0, -1.0],
            [
                [1.0, -1.0, -1.0],
                [1.0, 1.0, -1.0],
                [-1.0, 1.0, -1.0],
                [-1.0, -1.0, -1.0],
            ],
        ),
    ];

    for (normal, corners) in FACES {
        let n = (transform * Vec3::from(normal).extend(0.0)).truncate();
        let n = if n.length_squared() > 1e-12 {
            n.normalize()
        } else {
            Vec3::Y
        };
        let world: Vec<Vec3> = corners
            .iter()
            .map(|c| {
                let local = part.offset + Vec3::from(*c) * part.half;
                (transform * local.extend(1.0)).truncate()
            })
            .collect();
        for &[a, b, c] in &[[0usize, 1, 2], [0, 2, 3]] {
            for index in [a, b, c] {
                out.push(Vertex {
                    position: world[index].into(),
                    normal: n.into(),
                    color: part.color,
                });
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use glam::Vec4;

    fn pose(weapon: i32, grip: Option<Mat4>) -> ActorPose {
        ActorPose {
            bones: Vec::new(),
            tint: Vec4::ONE,
            grip,
            weapon,
        }
    }

    #[test]
    fn a_player_with_no_resolved_hand_carries_nothing() {
        // Better than a rifle at the world origin, which is what an
        // `unwrap_or_default` on the grip would produce.
        assert!(build(&[pose(2, None)]).is_empty());
    }

    #[test]
    fn every_slot_draws_something_distinguishable() {
        let mut counts = Vec::new();
        for slot in 0..5 {
            let verts = build(&[pose(slot, Some(Mat4::IDENTITY))]);
            assert!(!verts.is_empty(), "slot {slot} drew nothing");
            // Every triangle is a triangle.
            assert_eq!(verts.len() % 3, 0);
            counts.push(verts.len());
        }
        // A knife and a sniper must not be the same silhouette.
        assert_ne!(counts[0], counts[4]);
    }

    #[test]
    fn an_unknown_slot_falls_back_rather_than_vanishing() {
        assert_eq!(
            build(&[pose(99, Some(Mat4::IDENTITY))]).len(),
            build(&[pose(2, Some(Mat4::IDENTITY))]).len()
        );
    }

    #[test]
    fn normals_survive_the_grip_scale() {
        // The grip is a 0.55 uniform scale. A normal transformed with the
        // position and not renormalised comes out short, which the shader reads
        // as a surface tilted away from the light rather than as a bug.
        let verts = build(&[pose(2, Some(Mat4::IDENTITY))]);
        for v in &verts {
            let length = Vec3::from(v.normal).length();
            assert!(
                (length - 1.0).abs() < 1e-4,
                "normal of length {length} escaped the grip scale"
            );
        }
    }
}
