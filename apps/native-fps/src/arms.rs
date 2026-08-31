//! The hands holding the gun.
//!
//! The browser's `arms.ts`, in this client's terms. The grip anchors and the arm
//! dimensions are pinned against it by `browser_parity.rs`, and the anchors
//! themselves come from the same `models/grips.json` — compiled in with
//! `include_str!`, the trick `prop.rs` already uses for the weapon GLBs.
//!
//! ## Procedural, and solved onto the weapon
//!
//! Two arms, each an upper arm, a forearm and a gloved hand built from
//! primitives — the module's licensing rule, and the same construction the
//! weapons use.
//!
//! The **shoulders are fixed in camera space** and each hand is solved onto a
//! grip anchor **on the weapon**, by two-bone analytic IK. That one decision is
//! most of what makes this work: the anchors are points in the weapon's own
//! space, so they inherit the view model's entire transform for free. Every bob,
//! sway, recoil kick, reload dip, stow and inspect roll reaches the hands with
//! nothing here knowing any of it happened.
//!
//! ## The solve must clamp, never NaN
//!
//! An unreachable target puts `acos` outside `[-1, 1]`, which yields `NaN`, a
//! `NaN` transform, and geometry the GPU draws as nothing. An arm that vanishes
//! with no error is exactly the failure this file has to not have, so both
//! bounds are clamped explicitly rather than assumed unreachable.

use glam::{Mat4, Vec3};

use crate::renderer::Vertex;

/// Where the shoulders sit, in **camera** space. `arms.ts`'s `SHOULDER_R`/`_L`.
pub const SHOULDER_R: Vec3 = Vec3::new(0.42, -0.62, 0.28);
pub const SHOULDER_L: Vec3 = Vec3::new(-0.42, -0.62, 0.28);

/// Segment lengths, in cube units. About 30cm and 27cm — an arm.
pub const UPPER_LEN: f32 = 0.84;
pub const LOWER_LEN: f32 = 0.76;

/// Sleeve, skin and glove. Muted, so the gun stays the thing you look at.
/// `0x3d4a3a`, `0xb98a68`, `0x2a2d33` — `arms.ts`'s `ARM_PALETTE`.
pub const SLEEVE: [f32; 3] = [0.2392, 0.2902, 0.2275];
pub const SKIN: [f32; 3] = [0.7255, 0.5412, 0.4078];
pub const GLOVE: [f32; 3] = [0.1647, 0.1765, 0.2000];

const UPPER_RADIUS: f32 = 0.15;
const LOWER_RADIUS: f32 = 0.12;
const HAND_SIZE: Vec3 = Vec3::new(0.20, 0.24, 0.26);

/// The grips file, compiled in. The browser reads the same bytes.
const GRIPS_JSON: &str =
    include_str!("../../../packages/core/src/modules/hassault/models/grips.json");

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct GripAnchors {
    /// The trigger hand. Always present — every weapon is held by something.
    pub primary: Vec3,
    /// The off hand, or `None` for a weapon held in one.
    ///
    /// `None` rather than an off-screen coordinate: an empty hand is a real
    /// state, and a hand parked somewhere arbitrary is a hand the player will
    /// eventually see.
    pub support: Option<Vec3>,
    pub primary_roll: f32,
    pub support_roll: f32,
}

fn vec3_from(value: &serde_json::Value) -> Option<Vec3> {
    let a = value.as_array()?;
    Some(Vec3::new(
        a.first()?.as_f64()? as f32,
        a.get(1)?.as_f64()? as f32,
        a.get(2)?.as_f64()? as f32,
    ))
}

/// Where this weapon's hands go.
///
/// Read from the shared `grips.json` rather than tabulated here, and
/// deliberately **not served**: an anchor is a fact about the *model*, not about
/// the weapon's balance, so putting it in `weapons.py` would give the
/// simulation's table a rendering column and hand it the `response_model`
/// failure mode for nothing.
///
/// A weapon with no entry gets the defaults, so **a weapon added later has
/// hands** rather than being empty-handed until somebody notices.
pub fn grips_for(weapon_id: &str) -> GripAnchors {
    let file: serde_json::Value = match serde_json::from_str(GRIPS_JSON) {
        Ok(v) => v,
        // Compiled in, so this cannot happen at runtime — but a panic in a
        // renderer for a malformed constant would take the whole client down
        // rather than drawing one weapon without hands.
        Err(_) => {
            return GripAnchors {
                primary: Vec3::new(0.0, -0.3, 0.22),
                support: Some(Vec3::new(0.0, -0.22, -0.55)),
                primary_roll: 0.0,
                support_roll: 0.0,
            }
        }
    };
    let defaults = &file["defaults"];
    let mut out = GripAnchors {
        primary: vec3_from(&defaults["primary"]).unwrap_or(Vec3::new(0.0, -0.3, 0.22)),
        support: vec3_from(&defaults["support"]),
        primary_roll: defaults["primaryRoll"].as_f64().unwrap_or(0.0) as f32,
        support_roll: defaults["supportRoll"].as_f64().unwrap_or(0.0) as f32,
    };
    let Some(listed) = file["weapons"].get(weapon_id) else {
        return out;
    };
    if let Some(p) = vec3_from(&listed["primary"]) {
        out.primary = p;
    }
    // `null` means "this weapon has no off hand"; a *missing* key means "use the
    // default". Collapsing the two would give the knife a second hand gripping
    // thin air.
    if listed.get("support").is_some() {
        out.support = vec3_from(&listed["support"]);
    }
    if let Some(r) = listed["primaryRoll"].as_f64() {
        out.primary_roll = r as f32;
    }
    if let Some(r) = listed["supportRoll"].as_f64() {
        out.support_roll = r as f32;
    }
    out
}

/// Put the elbow somewhere plausible between a shoulder and a hand.
///
/// The law of cosines. `pole` picks *which* of the circle of valid elbows — a
/// real arm bends outward and down, not through the chest.
///
/// **Both bounds are clamped.** Out of reach the arm goes straight; folded past
/// its own reach it stops folding. Unclamped, `acos` outside `[-1, 1]` is `NaN`,
/// and NaN geometry is geometry the GPU draws as nothing.
///
/// Returns the elbow and whether the arm ended up straight.
pub fn solve_two_bone(
    root: Vec3,
    target: Vec3,
    upper: f32,
    lower: f32,
    pole: Vec3,
) -> (Vec3, bool) {
    let to_target = target - root;
    let d = to_target.length();
    if d < 1e-6 {
        // Degenerate: the hand is at the shoulder. Fold rather than divide by
        // nothing.
        return (root + pole.normalize_or(Vec3::X) * upper, false);
    }
    let direction = to_target / d;
    if d >= upper + lower {
        return (root + direction * upper, true);
    }
    let along = (d * d + upper * upper - lower * lower) / (2.0 * d);
    // Clamped at zero: a folded-past-reach arm has an imaginary offset, and the
    // square root of a negative is NaN.
    let off = (upper * upper - along * along).max(0.0).sqrt();
    // The pole, made perpendicular to the arm — Gram-Schmidt. A pole parallel to
    // the arm gives no direction at all, so it falls back to any perpendicular.
    let mut side = pole - direction * pole.dot(direction);
    if side.length() < 1e-5 {
        let axis = if direction.y.abs() < 0.9 {
            Vec3::Y
        } else {
            Vec3::X
        };
        side = direction.cross(axis);
    }
    (
        root + direction * along + side.normalize_or(Vec3::X) * off,
        false,
    )
}

/// Both arms as vertices, in camera space.
///
/// Emitted into the view-model stream rather than a pass of its own: that pass
/// already draws in camera space with a cleared depth buffer, which is exactly
/// what a pair of arms in front of everything wants — and it needs no second
/// pipeline, uniform or bind group.
///
/// `to_camera` takes a point from the weapon's model space into camera space —
/// the view model's own pivot chain, so the arms inherit every animation it has
/// without knowing about any of them.
pub fn vertices(anchors: &GripAnchors, to_camera: &Mat4, out: &mut Vec<Vertex>) {
    limb(
        SHOULDER_R,
        to_camera.transform_point3(anchors.primary),
        Vec3::new(1.0, -1.0, 0.0),
        out,
    );
    // A one-handed weapon draws one arm. The off arm is omitted rather than
    // parked, because a hand gripping thin air is a hand the player will
    // eventually see.
    if let Some(support) = anchors.support {
        limb(
            SHOULDER_L,
            to_camera.transform_point3(support),
            Vec3::new(-1.0, -1.0, 0.0),
            out,
        );
    }
}

fn limb(shoulder: Vec3, hand: Vec3, pole: Vec3, out: &mut Vec<Vertex>) {
    let (elbow, _) = solve_two_bone(shoulder, hand, UPPER_LEN, LOWER_LEN, pole);
    segment(shoulder, elbow, UPPER_RADIUS, LOWER_RADIUS, SLEEVE, out);
    segment(elbow, hand, LOWER_RADIUS, LOWER_RADIUS * 0.85, SLEEVE, out);
    // A cuff of skin between the sleeve and the glove, so a bare box does not
    // read as the arm simply ending.
    let wrist = elbow + (hand - elbow) * 0.88;
    segment(
        wrist,
        hand,
        LOWER_RADIUS * 0.86,
        LOWER_RADIUS * 0.8,
        SKIN,
        out,
    );
    fist(hand, (hand - elbow).normalize_or(Vec3::NEG_Z), out);
}

/// A tapered prism between two points. Six sides — enough to read as round at
/// arm's length, and a third of the triangles a cylinder would cost.
fn segment(from: Vec3, to: Vec3, r0: f32, r1: f32, color: [f32; 3], out: &mut Vec<Vertex>) {
    let axis = to - from;
    let len = axis.length();
    if len < 1e-5 {
        return;
    }
    let dir = axis / len;
    let up = if dir.y.abs() < 0.9 { Vec3::Y } else { Vec3::X };
    let u = dir.cross(up).normalize_or(Vec3::X);
    let v = dir.cross(u).normalize_or(Vec3::Z);
    let sides = 6;
    for i in 0..sides {
        let a0 = (i as f32 / sides as f32) * std::f32::consts::TAU;
        let a1 = ((i + 1) as f32 / sides as f32) * std::f32::consts::TAU;
        let ring = |angle: f32, at: Vec3, r: f32| at + (u * angle.cos() + v * angle.sin()) * r;
        let quad = [
            ring(a0, from, r0),
            ring(a1, from, r0),
            ring(a1, to, r1),
            ring(a0, to, r1),
        ];
        let normal = ((quad[0] - from) + (quad[1] - from)).normalize_or(u);
        for i in [0usize, 1, 2, 0, 2, 3] {
            out.push(Vertex {
                position: quad[i].into(),
                normal: normal.into(),
                color,
            });
        }
    }
}

/// The hand: a box on the end of the forearm, oriented along it.
fn fist(at: Vec3, along: Vec3, out: &mut Vec<Vertex>) {
    let up = if along.y.abs() < 0.9 {
        Vec3::Y
    } else {
        Vec3::X
    };
    let u = along.cross(up).normalize_or(Vec3::X);
    let v = along.cross(u).normalize_or(Vec3::Z);
    let h = HAND_SIZE * 0.5;
    let corner = |a: f32, b: f32, c: f32| at + u * (h.x * a) + v * (h.y * b) + along * (h.z * c);
    // Six faces, each two triangles, wound outward.
    let faces: [([f32; 3], [Vec3; 4]); 6] = [
        (
            u.into(),
            [
                corner(1.0, -1.0, -1.0),
                corner(1.0, 1.0, -1.0),
                corner(1.0, 1.0, 1.0),
                corner(1.0, -1.0, 1.0),
            ],
        ),
        (
            (-u).into(),
            [
                corner(-1.0, -1.0, 1.0),
                corner(-1.0, 1.0, 1.0),
                corner(-1.0, 1.0, -1.0),
                corner(-1.0, -1.0, -1.0),
            ],
        ),
        (
            v.into(),
            [
                corner(-1.0, 1.0, -1.0),
                corner(-1.0, 1.0, 1.0),
                corner(1.0, 1.0, 1.0),
                corner(1.0, 1.0, -1.0),
            ],
        ),
        (
            (-v).into(),
            [
                corner(-1.0, -1.0, 1.0),
                corner(-1.0, -1.0, -1.0),
                corner(1.0, -1.0, -1.0),
                corner(1.0, -1.0, 1.0),
            ],
        ),
        (
            along.into(),
            [
                corner(-1.0, -1.0, 1.0),
                corner(1.0, -1.0, 1.0),
                corner(1.0, 1.0, 1.0),
                corner(-1.0, 1.0, 1.0),
            ],
        ),
        (
            (-along).into(),
            [
                corner(-1.0, 1.0, -1.0),
                corner(1.0, 1.0, -1.0),
                corner(1.0, -1.0, -1.0),
                corner(-1.0, -1.0, -1.0),
            ],
        ),
    ];
    for (normal, quad) in faces {
        for i in [0usize, 1, 2, 0, 2, 3] {
            out.push(Vertex {
                position: quad[i].into(),
                normal,
                color: GLOVE,
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_elbow_is_exactly_a_bone_from_each_end() {
        let root = Vec3::ZERO;
        let target = Vec3::new(0.8, -0.4, -0.6);
        let (elbow, stretched) = solve_two_bone(
            root,
            target,
            UPPER_LEN,
            LOWER_LEN,
            Vec3::new(1.0, -1.0, 0.0),
        );
        assert!(!stretched);
        assert!((root.distance(elbow) - UPPER_LEN).abs() < 1e-4);
        assert!((elbow.distance(target) - LOWER_LEN).abs() < 1e-4);
    }

    #[test]
    fn an_unreachable_target_straightens_rather_than_producing_nan() {
        // Unclamped this is `acos` of something greater than 1 — NaN all the way
        // down to geometry the GPU draws as nothing.
        let (elbow, stretched) = solve_two_bone(
            Vec3::ZERO,
            Vec3::new(50.0, 0.0, 0.0),
            UPPER_LEN,
            LOWER_LEN,
            Vec3::new(1.0, -1.0, 0.0),
        );
        assert!(stretched);
        assert!(elbow.is_finite());
    }

    #[test]
    fn nothing_in_the_reachable_space_produces_a_nan() {
        // Belt and braces, because the failure is invisible: the arm is simply
        // not there, and nothing logs.
        let pole = Vec3::new(1.0, -1.0, 0.0);
        let mut d = 0.0;
        while d <= (UPPER_LEN + LOWER_LEN) * 1.5 {
            for dir in [Vec3::X, Vec3::NEG_Y, Vec3::NEG_Z, Vec3::ONE.normalize()] {
                let (elbow, _) = solve_two_bone(Vec3::ZERO, dir * d, UPPER_LEN, LOWER_LEN, pole);
                assert!(elbow.is_finite(), "NaN elbow at {d} along {dir}");
            }
            d += 0.05;
        }
    }

    #[test]
    fn the_pole_decides_which_way_the_elbow_bends() {
        let target = Vec3::new(0.0, 0.0, -1.0);
        let (out, _) = solve_two_bone(Vec3::ZERO, target, UPPER_LEN, LOWER_LEN, Vec3::X);
        let (across, _) = solve_two_bone(Vec3::ZERO, target, UPPER_LEN, LOWER_LEN, Vec3::NEG_X);
        assert!(out.x > 0.0);
        assert!(across.x < 0.0);
    }

    #[test]
    fn every_shipped_weapon_has_a_trigger_hand() {
        for id in ["knife", "pistol", "assault", "shotgun", "sniper"] {
            assert!(grips_for(id).primary.is_finite(), "{id}");
        }
    }

    #[test]
    fn the_knife_has_one_hand_and_the_rifles_have_two() {
        // `None` is a real state and not a missing value.
        assert!(grips_for("knife").support.is_none());
        assert!(grips_for("assault").support.is_some());
    }

    #[test]
    fn a_weapon_this_client_has_never_heard_of_still_gets_hands() {
        // The `fitWeaponModel` spirit: measure the general case, list only the
        // exceptions. A weapon the server grew should look ordinary.
        let grips = grips_for("railgun");
        assert!(grips.support.is_some());
        assert!(grips.primary.is_finite());
    }

    #[test]
    fn a_one_handed_weapon_draws_one_arm() {
        let mut two = Vec::new();
        vertices(&grips_for("assault"), &Mat4::IDENTITY, &mut two);
        let mut one = Vec::new();
        vertices(&grips_for("knife"), &Mat4::IDENTITY, &mut one);
        assert!(one.len() < two.len());
        assert!(!one.is_empty());
    }

    #[test]
    fn the_arms_follow_the_weapon_rather_than_standing_still() {
        // The whole design: the anchors are points on the *weapon*, so the view
        // model's transform reaches the hands with nothing here knowing it.
        let grips = grips_for("assault");
        let mut still = Vec::new();
        vertices(&grips, &Mat4::IDENTITY, &mut still);
        let mut moved = Vec::new();
        vertices(
            &grips,
            &Mat4::from_translation(Vec3::new(0.0, -0.3, 0.0)),
            &mut moved,
        );
        assert_eq!(still.len(), moved.len());
        assert!(
            still
                .iter()
                .zip(moved.iter())
                .any(|(a, b)| a.position != b.position),
            "the hands did not move with the gun"
        );
    }
}
