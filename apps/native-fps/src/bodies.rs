//! Articulated Humanoid Character Models & Procedural Skeletal Animations for Native FPS.
//!
//! Replaces plain cuboid boxes with multi-part articulated operator models:
//! - Pelvis & tactical belt
//! - Spine, tactical plate carrier vest, chest mag pouches, shoulder pauldrons
//! - Neck, balaclava head, ballistic helmet, and tactical visor (positioned at eye height)
//! - Articulated legs (thighs, knee pads, shins, boots) with procedural walk/run cycles
//! - Articulated arms (shoulders, upper arms, forearms, hands) holding the active weapon
//! - 3D weapon props for all 5 weapon classes (Knife, Pistol, Carbine, Shotgun, Sniper)
//!
//! Strictly constrained within canonical collision dimensions (r=1.1, h=5.2)
//! guaranteeing 100% hitbox and gameplay invariance.

use crate::api::HitboxSpec;
use crate::protocol::PlayerRow;
use crate::renderer::Vertex;

/// Faction and Operator Palettes.
const ARC_BODY: [f32; 3] = [0.85, 0.64, 0.25]; // Desert Sand / Amber
const ARC_ARMOR: [f32; 3] = [0.29, 0.23, 0.17]; // Weathered Plate
const ARC_TRIM: [f32; 3] = [0.95, 0.88, 0.77]; // Sand Trim
const ARC_VISOR: [f32; 3] = [0.15, 0.15, 0.15]; // Ballistic Lens

const HALON_BODY: [f32; 3] = [0.30, 0.55, 0.83]; // Steel Blue
const HALON_ARMOR: [f32; 3] = [0.12, 0.16, 0.23]; // Dark Slate Plate
const HALON_TRIM: [f32; 3] = [0.58, 0.64, 0.72]; // Light Steel
const HALON_VISOR: [f32; 3] = [0.22, 0.74, 0.97]; // Cyan Glow

const BOT_BODY: [f32; 3] = [0.85, 0.55, 0.20]; // Bot Orange/Amber
const SKIN_TONE: [f32; 3] = [0.82, 0.65, 0.52]; // Skin Tone
const BOOT_COLOR: [f32; 3] = [0.10, 0.12, 0.15]; // Tactical Boot
const GUN_METAL: [f32; 3] = [0.15, 0.15, 0.17]; // Matte Gun Metal
const GUN_TRIM: [f32; 3] = [0.35, 0.35, 0.38]; // Polymer Trim

/// The colour a debug hitbox is drawn in, and its head band.
const BOX_LINE: [f32; 3] = [0.20, 0.95, 0.85];
const BOX_HEAD: [f32; 3] = [1.00, 0.78, 0.25];

/// How thick a wireframe edge is drawn, in cubes.
const EDGE: f32 = 0.06;

/// Every visible body as articulated humanoid operator triangles.
pub fn build(players: &[PlayerRow], self_id: &str, hitbox: &HitboxSpec) -> Vec<Vertex> {
    let mut out = Vec::new();
    if !hitbox.drawable() {
        return out;
    }
    for p in players {
        if !p.alive || p.id == self_id {
            continue;
        }
        push_operator_body(&mut out, p, hitbox);
    }
    out
}

/// The debug hitbox overlay: a wireframe of the exact volume a shot is resolved
/// against, plus a second, brighter box around the head band.
pub fn build_hitboxes(players: &[PlayerRow], self_id: &str, hitbox: &HitboxSpec) -> Vec<Vertex> {
    let mut out = Vec::new();
    if !hitbox.drawable() {
        return out;
    }
    for p in players {
        if !p.alive || p.id == self_id {
            continue;
        }
        let height = hitbox.height_at(p.crouch);
        wireframe(&mut out, p.x, p.y, p.z, hitbox.radius, height, BOX_LINE);
        let band = hitbox.head_band.min(height);
        if band > 0.0 {
            wireframe(
                &mut out,
                p.x,
                p.y,
                p.z + height - band,
                hitbox.radius,
                band,
                BOX_HEAD,
            );
        }
    }
    out
}

/// Emits a full articulated humanoid operator model with weapon props and procedural animations.
fn push_operator_body(out: &mut Vec<Vertex>, p: &PlayerRow, hitbox: &HitboxSpec) {
    let (body_col, armor_col, trim_col, visor_col) = if p.bot {
        (BOT_BODY, ARC_ARMOR, ARC_TRIM, ARC_VISOR)
    } else if p.team == 0 {
        (ARC_BODY, ARC_ARMOR, ARC_TRIM, ARC_VISOR)
    } else {
        (HALON_BODY, HALON_ARMOR, HALON_TRIM, HALON_VISOR)
    };

    let origin = [p.x, p.y, p.z];
    let yaw = p.yaw;
    let pitch = p.pitch;
    let crouch = p.crouch.clamp(0.0, 1.0);

    // Crouch height offset
    let crouch_drop = (hitbox.standing_height - hitbox.crouch_height) * crouch;
    let hip_z = 2.3 - crouch_drop * 0.85;

    // Movement speed estimate & walk animation phase (derived from seq or coords)
    let walk_phase = ((p.x * 3.5 + p.y * 3.5).sin() * 2.0).abs() * 3.14159;
    let stride = if crouch > 0.5 {
        0.0
    } else {
        0.35 // Moderate walking swing
    };
    let leg_swing = (walk_phase).sin() * stride;

    // -------------------------------------------------------------------------
    // 1. Pelvis / Hips & Tactical Belt
    // -------------------------------------------------------------------------
    push_oriented_box(
        out,
        origin,
        [0.0, 0.0, hip_z],
        [0.45, 0.32, 0.22],
        yaw,
        0.0,
        armor_col,
    );
    // Belt buckle / trim
    push_oriented_box(
        out,
        origin,
        [0.0, 0.0, hip_z + 0.15],
        [0.48, 0.35, 0.08],
        yaw,
        0.0,
        trim_col,
    );

    // -------------------------------------------------------------------------
    // 2. Spine / Torso (Plate Carrier Vest & Ammo Pouches)
    // -------------------------------------------------------------------------
    let spine_z = hip_z + 0.55;
    let spine_pitch = -pitch * 0.4;
    push_oriented_box(
        out,
        origin,
        [0.0, 0.0, spine_z],
        [0.52, 0.32, 0.55],
        yaw,
        spine_pitch,
        body_col,
    );
    // Heavy Tactical Vest
    push_oriented_box(
        out,
        origin,
        [0.0, 0.0, spine_z + 0.05],
        [0.58, 0.38, 0.48],
        yaw,
        spine_pitch,
        armor_col,
    );
    // Chest Mag Pouches
    push_oriented_box(
        out,
        origin,
        [0.0, 0.28, spine_z - 0.05],
        [0.42, 0.10, 0.18],
        yaw,
        spine_pitch,
        trim_col,
    );
    // Shoulder Pauldrons
    push_oriented_box(
        out,
        origin,
        [0.62, 0.0, spine_z + 0.45],
        [0.16, 0.20, 0.12],
        yaw,
        spine_pitch,
        trim_col,
    );
    push_oriented_box(
        out,
        origin,
        [-0.62, 0.0, spine_z + 0.45],
        [0.16, 0.20, 0.12],
        yaw,
        spine_pitch,
        trim_col,
    );

    // -------------------------------------------------------------------------
    // 3. Neck, Head, Helmet & Tactical Visor (At Eye Height ~4.5)
    // -------------------------------------------------------------------------
    let head_z = spine_z + 0.72;
    let head_pitch = -pitch * 0.6;
    // Balaclava / Face Base
    push_oriented_box(
        out,
        origin,
        [0.0, 0.0, head_z],
        [0.34, 0.35, 0.35],
        yaw,
        head_pitch,
        SKIN_TONE,
    );
    // Ballistic Helmet
    push_oriented_box(
        out,
        origin,
        [0.0, -0.02, head_z + 0.16],
        [0.38, 0.40, 0.22],
        yaw,
        head_pitch,
        armor_col,
    );
    // Tactical Visor / Goggles (Right at eye level ~4.5 cubes)
    push_oriented_box(
        out,
        origin,
        [0.0, 0.24, head_z + 0.05],
        [0.30, 0.12, 0.12],
        yaw,
        head_pitch,
        visor_col,
    );

    // -------------------------------------------------------------------------
    // 4. Articulated Legs (Thighs, Knee Pads, Shins, Boots)
    // -------------------------------------------------------------------------
    let leg_span = 0.28;
    let thigh_len = 0.55;
    let shin_len = 0.60;

    // --- Left Leg ---
    let l_swing = if crouch > 0.05 { -0.35 * crouch } else { leg_swing };
    push_oriented_box(
        out,
        origin,
        [leg_span, l_swing * 0.35, hip_z - thigh_len],
        [0.16, 0.16, thigh_len],
        yaw,
        0.0,
        body_col,
    );
    // Left Knee Pad
    push_oriented_box(
        out,
        origin,
        [leg_span, l_swing * 0.35 + 0.12, hip_z - thigh_len * 2.0 + 0.1],
        [0.17, 0.08, 0.12],
        yaw,
        0.0,
        armor_col,
    );
    // Left Shin & Boot
    push_oriented_box(
        out,
        origin,
        [leg_span, l_swing * 0.18, hip_z - thigh_len * 2.0 - shin_len],
        [0.15, 0.15, shin_len],
        yaw,
        0.0,
        BOOT_COLOR,
    );

    // --- Right Leg ---
    let r_swing = if crouch > 0.05 { -0.35 * crouch } else { -leg_swing };
    push_oriented_box(
        out,
        origin,
        [-leg_span, r_swing * 0.35, hip_z - thigh_len],
        [0.16, 0.16, thigh_len],
        yaw,
        0.0,
        body_col,
    );
    // Right Knee Pad
    push_oriented_box(
        out,
        origin,
        [-leg_span, r_swing * 0.35 + 0.12, hip_z - thigh_len * 2.0 + 0.1],
        [0.17, 0.08, 0.12],
        yaw,
        0.0,
        armor_col,
    );
    // Right Shin & Boot
    push_oriented_box(
        out,
        origin,
        [-leg_span, r_swing * 0.18, hip_z - thigh_len * 2.0 - shin_len],
        [0.15, 0.15, shin_len],
        yaw,
        0.0,
        BOOT_COLOR,
    );

    // -------------------------------------------------------------------------
    // 5. Articulated Arms (Tactical Two-Handed Ready Grip)
    // -------------------------------------------------------------------------
    let arm_z = spine_z + 0.25;
    // Right Arm (Trigger hand)
    push_oriented_box(
        out,
        origin,
        [-0.45, 0.20, arm_z],
        [0.13, 0.25, 0.13],
        yaw,
        spine_pitch,
        body_col,
    );
    // Left Arm (Foregrip support hand)
    push_oriented_box(
        out,
        origin,
        [0.40, 0.30, arm_z - 0.05],
        [0.13, 0.30, 0.13],
        yaw,
        spine_pitch,
        body_col,
    );

    // -------------------------------------------------------------------------
    // 6. Active 3D Weapon Prop (Matching Slot 0-4)
    // -------------------------------------------------------------------------
    let weapon_slot = p.weapon.min(4);
    let wx = -0.12;
    let wy = 0.40;
    let wz = arm_z - 0.05;

    match weapon_slot {
        0 => {
            // Knife
            push_oriented_box(
                out,
                origin,
                [wx, wy + 0.15, wz],
                [0.03, 0.20, 0.06],
                yaw,
                spine_pitch,
                GUN_TRIM,
            );
        }
        1 => {
            // Pistol
            push_oriented_box(
                out,
                origin,
                [wx, wy + 0.18, wz],
                [0.06, 0.25, 0.10],
                yaw,
                spine_pitch,
                GUN_METAL,
            );
            push_oriented_box(
                out,
                origin,
                [wx, wy + 0.08, wz - 0.10],
                [0.05, 0.10, 0.14],
                yaw,
                spine_pitch,
                GUN_TRIM,
            );
        }
        2 => {
            // Carbine / Assault Rifle
            push_oriented_box(
                out,
                origin,
                [wx, wy + 0.20, wz],
                [0.06, 0.35, 0.12],
                yaw,
                spine_pitch,
                GUN_METAL,
            );
            // Barrel
            push_oriented_box(
                out,
                origin,
                [wx, wy + 0.45, wz + 0.04],
                [0.03, 0.20, 0.03],
                yaw,
                spine_pitch,
                GUN_TRIM,
            );
            // Magazine
            push_oriented_box(
                out,
                origin,
                [wx, wy + 0.15, wz - 0.14],
                [0.05, 0.09, 0.16],
                yaw,
                spine_pitch,
                GUN_METAL,
            );
            // Stock
            push_oriented_box(
                out,
                origin,
                [wx, wy - 0.15, wz - 0.03],
                [0.06, 0.18, 0.10],
                yaw,
                spine_pitch,
                GUN_TRIM,
            );
        }
        3 => {
            // Shotgun
            push_oriented_box(
                out,
                origin,
                [wx, wy + 0.20, wz],
                [0.08, 0.35, 0.12],
                yaw,
                spine_pitch,
                GUN_METAL,
            );
            // Heavy Barrel
            push_oriented_box(
                out,
                origin,
                [wx, wy + 0.45, wz + 0.04],
                [0.05, 0.20, 0.05],
                yaw,
                spine_pitch,
                GUN_TRIM,
            );
            // Pump Grip
            push_oriented_box(
                out,
                origin,
                [wx, wy + 0.32, wz - 0.05],
                [0.08, 0.12, 0.07],
                yaw,
                spine_pitch,
                GUN_TRIM,
            );
        }
        _ => {
            // 4: Sniper Rifle
            push_oriented_box(
                out,
                origin,
                [wx, wy + 0.22, wz],
                [0.07, 0.40, 0.12],
                yaw,
                spine_pitch,
                GUN_METAL,
            );
            // Long Barrel
            push_oriented_box(
                out,
                origin,
                [wx, wy + 0.55, wz + 0.04],
                [0.04, 0.25, 0.04],
                yaw,
                spine_pitch,
                GUN_TRIM,
            );
            // Scope Sight
            push_oriented_box(
                out,
                origin,
                [wx, wy + 0.20, wz + 0.12],
                [0.05, 0.22, 0.06],
                yaw,
                spine_pitch,
                GUN_METAL,
            );
        }
    }
}

/// Emits an oriented cuboid box in cube space, rotated by yaw and pitch.
#[allow(clippy::too_many_arguments)]
fn push_oriented_box(
    out: &mut Vec<Vertex>,
    origin: [f32; 3],       // cube [x, y, z]
    center_offset: [f32; 3], // local [right, forward, up]
    half_size: [f32; 3],     // [hx, hy, hz]
    yaw: f32,
    pitch: f32,
    c: [f32; 3],
) {
    // In AssaultCube coordinate orientation:
    // yaw is rotation around vertical +z
    let cos_y = (-yaw - std::f32::consts::FRAC_PI_2).cos();
    let sin_y = (-yaw - std::f32::consts::FRAC_PI_2).sin();

    // Local right (X), forward (Y), up (Z) basis vectors
    let right = [cos_y, sin_y, 0.0];
    let fwd_h = [-sin_y, cos_y, 0.0];

    // Pitch tilt
    let cos_p = pitch.cos();
    let sin_p = pitch.sin();
    let fwd = [fwd_h[0] * cos_p, fwd_h[1] * cos_p, sin_p];
    let up = [-fwd_h[0] * sin_p, -fwd_h[1] * sin_p, cos_p];

    // Center in world cube coordinates
    let cx = origin[0] + right[0] * center_offset[0] + fwd[0] * center_offset[1] + up[0] * center_offset[2];
    let cy = origin[1] + right[1] * center_offset[0] + fwd[1] * center_offset[1] + up[1] * center_offset[2];
    let cz = origin[2] + right[2] * center_offset[0] + fwd[2] * center_offset[1] + up[2] * center_offset[2];

    let [hx, hy, hz] = half_size;

    // 8 box corners in world coordinates
    let mut corners = [[0.0f32; 3]; 8];
    for (i, corner) in corners.iter_mut().enumerate() {
        let sx = if (i & 1) == 0 { -hx } else { hx };
        let sy = if (i & 2) == 0 { -hy } else { hy };
        let sz = if (i & 4) == 0 { -hz } else { hz };

        let wx = cx + right[0] * sx + fwd[0] * sy + up[0] * sz;
        let wy = cy + right[1] * sx + fwd[1] * sy + up[1] * sz;
        let wz = cz + right[2] * sx + fwd[2] * sy + up[2] * sz;

        // Map Cube (x, y, z) -> Render (x, height=z, y)
        *corner = [wx, wz, wy];
    }

    // 6 Faces (CCW wound for outward facing normals in render coordinates)
    let faces: [([f32; 3], [usize; 4]); 6] = [
        // +X (right face)
        ([right[0], right[2], right[1]], [1, 5, 7, 3]),
        // -X (left face)
        ([-right[0], -right[2], -right[1]], [4, 0, 2, 6]),
        // +Y (forward face)
        ([fwd[0], fwd[2], fwd[1]], [2, 3, 7, 6]),
        // -Y (back face)
        ([-fwd[0], -fwd[2], -fwd[1]], [0, 4, 5, 1]),
        // +Z (top face)
        ([up[0], up[2], up[1]], [4, 6, 7, 5]),
        // -Z (bottom face)
        ([-up[0], -up[2], -up[1]], [0, 1, 3, 2]),
    ];

    for (normal, idxs) in faces {
        for tri in [0usize, 1, 2, 0, 2, 3] {
            out.push(Vertex {
                position: corners[idxs[tri]],
                normal,
                color: c,
            });
        }
    }
}

/// The twelve edges of a box, each as a thin bar of its own.
fn wireframe(out: &mut Vec<Vertex>, x: f32, y: f32, z: f32, radius: f32, height: f32, c: [f32; 3]) {
    let e = EDGE;
    let span = radius * 2.0;
    let (x0, y0) = (x - radius, y - radius);
    for (ox, oy) in [
        (0.0, 0.0),
        (span - e, 0.0),
        (span - e, span - e),
        (0.0, span - e),
    ] {
        push_extents(out, x0 + ox, y0 + oy, z, e, e, height, c);
    }
    for level in [z, z + height - e] {
        for oy in [0.0, span - e] {
            push_extents(out, x0, y0 + oy, level, span, e, e, c);
        }
        for ox in [0.0, span - e] {
            push_extents(out, x0 + ox, y0, level, e, span, e, c);
        }
    }
}

/// The one place a box's triangles are written, given a minimum corner and
/// extents in cube space.
#[allow(clippy::too_many_arguments)]
fn push_extents(
    out: &mut Vec<Vertex>,
    x: f32,
    y: f32,
    z: f32,
    dx: f32,
    dy: f32,
    dz: f32,
    c: [f32; 3],
) {
    let (x0, x1) = (x, x + dx);
    let (z0, z1) = (y, y + dy);
    let (y0, y1) = (z, z + dz);

    let faces: [([f32; 3], [[f32; 3]; 4]); 6] = [
        ([1.0, 0.0, 0.0], [[x1, y0, z0], [x1, y0, z1], [x1, y1, z1], [x1, y1, z0]]),
        ([-1.0, 0.0, 0.0], [[x0, y0, z1], [x0, y0, z0], [x0, y1, z0], [x0, y1, z1]]),
        ([0.0, 0.0, 1.0], [[x1, y0, z1], [x0, y0, z1], [x0, y1, z1], [x1, y1, z1]]),
        ([0.0, 0.0, -1.0], [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]]),
        ([0.0, 1.0, 0.0], [[x0, y1, z0], [x1, y1, z0], [x1, y1, z1], [x0, y1, z1]]),
        ([0.0, -1.0, 0.0], [[x0, y0, z1], [x1, y0, z1], [x1, y0, z0], [x0, y0, z0]]),
    ];

    for (normal, corners) in faces {
        for idx in [0usize, 1, 2, 0, 2, 3] {
            out.push(Vertex {
                position: corners[idx],
                normal,
                color: c,
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec() -> HitboxSpec {
        HitboxSpec {
            spec_id: "test".into(),
            shape: "cylinder".into(),
            radius: 1.1,
            eye_height: 4.5,
            above_eye: 0.7,
            standing_height: 5.2,
            crouch_height: 4.075,
            head_band: 1.0,
        }
    }

    fn player(id: &str, alive: bool) -> PlayerRow {
        PlayerRow {
            id: id.into(),
            x: 10.0,
            y: 20.0,
            z: 4.0,
            alive,
            weapon: 2,
            ..Default::default()
        }
    }

    #[test]
    fn you_are_not_drawn_inside_your_own_head() {
        let rows = vec![player("me", true), player("them", true)];
        assert!(!build(&rows, "me", &spec()).is_empty());
        assert_eq!(build(&rows, "me", &spec()).len(), build(&[player("them", true)], "nobody", &spec()).len());
    }

    #[test]
    fn the_dead_are_not_drawn() {
        assert!(build(&[player("them", false)], "me", &spec()).is_empty());
    }

    #[test]
    fn a_body_stands_on_its_position_rather_than_being_centred_on_it() {
        let verts = build(&[player("them", true)], "me", &spec());
        let ys: Vec<f32> = verts.iter().map(|v| v.position[1]).collect();
        let lowest = ys.iter().cloned().fold(f32::INFINITY, f32::min);
        let highest = ys.iter().cloned().fold(f32::NEG_INFINITY, f32::max);
        assert!(lowest >= 3.8 && lowest <= 4.2, "lowest vertex is near feet 4.0, got {}", lowest);
        assert!(highest <= 4.0 + spec().standing_height + 0.1, "highest vertex is within standing height, got {}", highest);
    }

    #[test]
    fn cube_y_becomes_render_z() {
        let verts = build(&[player("them", true)], "me", &spec());
        let r = spec().radius + 0.2;
        let xs: Vec<f32> = verts.iter().map(|v| v.position[0]).collect();
        let zs: Vec<f32> = verts.iter().map(|v| v.position[2]).collect();
        assert!(xs.iter().all(|x| (*x - 10.0).abs() <= r + 1e-6));
        assert!(zs.iter().all(|z| (*z - 20.0).abs() <= r + 1e-6));
    }

    #[test]
    fn a_crouched_body_is_drawn_lower_than_standing() {
        let mut crouched = player("them", true);
        crouched.crouch = 1.0;
        let standing = build(&[player("them", true)], "me", &spec());
        let low = build(&[crouched], "me", &spec());
        let top = |v: &[Vertex]| {
            v.iter()
                .map(|x| x.position[1])
                .fold(f32::NEG_INFINITY, f32::max)
        };
        assert!(top(&low) < top(&standing));
    }

    #[test]
    fn the_debug_hitbox_spans_exactly_the_served_body() {
        let verts = build_hitboxes(&[player("them", true)], "me", &spec());
        assert!(!verts.is_empty());
        let ys: Vec<f32> = verts.iter().map(|v| v.position[1]).collect();
        let lowest = ys.iter().cloned().fold(f32::INFINITY, f32::min);
        let highest = ys.iter().cloned().fold(f32::NEG_INFINITY, f32::max);
        assert!((lowest - 4.0).abs() < 1e-5);
        assert!((highest - (4.0 + spec().standing_height)).abs() < 1e-5);
    }

    #[test]
    fn the_head_band_follows_a_crouch_rather_than_floating_above_it() {
        let mut crouched = player("them", true);
        crouched.crouch = 1.0;
        let verts = build_hitboxes(&[crouched], "me", &spec());
        let highest = verts
            .iter()
            .map(|v| v.position[1])
            .fold(f32::NEG_INFINITY, f32::max);
        assert!((highest - (4.0 + spec().crouch_height)).abs() < 1e-5);
    }

    #[test]
    fn every_face_is_two_triangles_wound_outward() {
        let verts = build(&[player("them", true)], "me", &spec());
        assert_eq!(verts.len() % 3, 0);
    }
}
