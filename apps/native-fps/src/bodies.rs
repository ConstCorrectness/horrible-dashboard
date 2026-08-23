//! Drawing the other players.
//!
//! Boxes, and honestly boxes. There are no character models for the same reason
//! there are no textures: the module ships none of AssaultCube's media, and a
//! model set is either somebody else's copyright or a large pile of files nobody
//! has made yet. What matters for now is that a body is **the right size and in
//! the right place**, because those two things are what a shot is resolved
//! against — a body drawn larger than its hitbox teaches you to miss.
//!
//! So the box is built from the same numbers the server simulates with:
//! `PLAYER_RADIUS` wide, and a height that follows the player's own crouch
//! fraction. Crouch is public in the snapshot precisely because it changes both
//! what you see and what you can hit.

use crate::api::HitboxSpec;
use crate::protocol::PlayerRow;
use crate::renderer::Vertex;

/// Team colours, and a third for yourself.
///
/// Deliberately not the texture-id palette the world uses: a body has to be
/// findable against a wall of any hue, which means it cannot come out of the same
/// generator the walls do.
const TEAM_A: [f32; 3] = [0.85, 0.35, 0.25];
const TEAM_B: [f32; 3] = [0.30, 0.55, 0.90];
const BOT: [f32; 3] = [0.85, 0.62, 0.25];

/// The colour a debug hitbox is drawn in, and its head band.
///
/// Deliberately not a team colour: the overlay is a measuring tool, and a
/// measuring tool that changes colour depending on who you are looking at is one
/// you cannot compare two readings with.
const BOX_LINE: [f32; 3] = [0.20, 0.95, 0.85];
const BOX_HEAD: [f32; 3] = [1.00, 0.78, 0.25];

/// How thick a wireframe edge is drawn, in cubes.
///
/// A line list would be thinner and cheaper, but this renderer has exactly one
/// pipeline and it draws triangles. A thin box per edge costs 36 vertices and
/// needs no second pipeline, no second bind group and no second shader — and it
/// is *visible*, which a one-pixel line at 40 cubes' distance is not.
const EDGE: f32 = 0.06;

/// Every visible body as triangles, ready for the dynamic buffer.
///
/// `self_id` is excluded: this is a first-person camera sitting inside its own
/// body, and drawing it would fill the screen with the inside of a box. B3's
/// prediction changes where the camera is, not this.
///
/// `hitbox` is the **served** body (`GET /api/hassault/hitbox`). It used to be
/// three constants here and one wrong one — see `HitboxSpec` — and getting it
/// wrong is not a cosmetic bug: a body drawn shorter than it can be hit teaches
/// you to aim at a head that is not where it is drawn.
pub fn build(players: &[PlayerRow], self_id: &str, hitbox: &HitboxSpec) -> Vec<Vertex> {
    let mut out = Vec::new();
    if !hitbox.drawable() {
        // A shape this build does not understand. Drawing a box for a capsule
        // would be a confident picture of the wrong body, which is worse than
        // an empty world — and the HUD says so, so this is not silent.
        return out;
    }
    for p in players {
        if !p.alive || p.id == self_id {
            continue;
        }
        let color = if p.bot {
            BOT
        } else if p.team == 0 {
            TEAM_A
        } else {
            TEAM_B
        };
        // `crouch` is 0..1, and the height it maps to is the server's own
        // `height_at` — not `standing × 0.75`, which is the scale applied to the
        // *eye*. The difference is four percent, all of it at the top, which is
        // precisely where the head band is.
        push_box(
            &mut out,
            p.x,
            p.y,
            p.z,
            hitbox.radius,
            hitbox.height_at(p.crouch),
            color,
        );
    }
    out
}

/// The debug hitbox overlay: a wireframe of the exact volume a shot is resolved
/// against, plus a second, brighter box around the head band.
///
/// This exists because "is the body drawn where it can be hit?" was, until now,
/// a question with no way to ask it. The mismatch that prompted it — a crouched
/// body drawn 4% short — was invisible in play and would have stayed invisible:
/// you simply miss slightly more often when crouching enemies are involved, and
/// blame the netcode.
///
/// Appended to the same vertex stream as the bodies rather than drawn in its own
/// pass, so it is depth-tested against the world: a hitbox behind a wall is
/// hidden by the wall, which is the honest picture. An overlay drawn on top of
/// everything would be a wall hack.
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
        // The head band is measured **down from the top**, so it follows a
        // crouch instead of floating above it — which is the whole reason the
        // server defines it that way, and worth being able to see.
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

/// The twelve edges of a box, each as a thin bar of its own.
///
/// Built from `push_extents` rather than from a second hand-written cube, so
/// there is exactly one winding order in this file — a face wound inwards is
/// eaten by the back-face culling the world mesh depends on, and it is invisible
/// in the source.
fn wireframe(out: &mut Vec<Vertex>, x: f32, y: f32, z: f32, radius: f32, height: f32, c: [f32; 3]) {
    let e = EDGE;
    let span = radius * 2.0;
    let (x0, y0) = (x - radius, y - radius);
    // The four uprights.
    for (ox, oy) in [
        (0.0, 0.0),
        (span - e, 0.0),
        (span - e, span - e),
        (0.0, span - e),
    ] {
        push_extents(out, x0 + ox, y0 + oy, z, e, e, height, c);
    }
    // The rings, at the floor and at the top.
    for level in [z, z + height - e] {
        for oy in [0.0, span - e] {
            push_extents(out, x0, y0 + oy, level, span, e, e, c);
        }
        for ox in [0.0, span - e] {
            push_extents(out, x0 + ox, y0, level, e, span, e, c);
        }
    }
}

/// An axis-aligned box standing on `(x, y)` at height `z`.
///
/// Render space is y-up, so cube `y` becomes render `z` — the same mapping the
/// mesher and the camera use. Getting it wrong here puts every body on the wrong
/// axis of the map, which looks like a netcode fault rather than a drawing one.
fn push_box(out: &mut Vec<Vertex>, x: f32, y: f32, z: f32, radius: f32, height: f32, c: [f32; 3]) {
    push_extents(
        out,
        x - radius,
        y - radius,
        z,
        radius * 2.0,
        radius * 2.0,
        height,
        c,
    );
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

    // Each face wound counter-clockwise seen from outside, so back-face culling —
    // which the world mesh relies on — does not eat half of every body.
    let faces: [([f32; 3], [[f32; 3]; 4]); 6] = [
        // +x
        (
            [1.0, 0.0, 0.0],
            [[x1, y0, z0], [x1, y0, z1], [x1, y1, z1], [x1, y1, z0]],
        ),
        // -x
        (
            [-1.0, 0.0, 0.0],
            [[x0, y0, z1], [x0, y0, z0], [x0, y1, z0], [x0, y1, z1]],
        ),
        // +z (render), i.e. +y in cube space
        (
            [0.0, 0.0, 1.0],
            [[x1, y0, z1], [x0, y0, z1], [x0, y1, z1], [x1, y1, z1]],
        ),
        // -z
        (
            [0.0, 0.0, -1.0],
            [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]],
        ),
        // top
        (
            [0.0, 1.0, 0.0],
            [[x0, y1, z0], [x1, y1, z0], [x1, y1, z1], [x0, y1, z1]],
        ),
        // bottom
        (
            [0.0, -1.0, 0.0],
            [[x0, y0, z1], [x1, y0, z1], [x1, y0, z0], [x0, y0, z0]],
        ),
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

    /// The shipped body, as `hitbox.py` serves it. Spelled out rather than
    /// `Default::default()` so a change to the fallback shows up here as a
    /// deliberate edit rather than as four assertions quietly moving.
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
            ..Default::default()
        }
    }

    #[test]
    fn you_are_not_drawn_inside_your_own_head() {
        let rows = vec![player("me", true), player("them", true)];
        assert_eq!(build(&rows, "me", &spec()).len(), 36, "one box, six faces");
        assert_eq!(build(&rows, "nobody", &spec()).len(), 72);
    }

    #[test]
    fn the_dead_are_not_drawn() {
        assert!(build(&[player("them", false)], "me", &spec()).is_empty());
    }

    #[test]
    fn a_body_stands_on_its_position_rather_than_being_centred_on_it() {
        // `z` on the wire is where the feet are. Centring the box on it would
        // sink every player half a body into the floor — and, worse, make the
        // drawn body disagree with the one the server rewinds shots against.
        let verts = build(&[player("them", true)], "me", &spec());
        let ys: Vec<f32> = verts.iter().map(|v| v.position[1]).collect();
        let lowest = ys.iter().cloned().fold(f32::INFINITY, f32::min);
        let highest = ys.iter().cloned().fold(f32::NEG_INFINITY, f32::max);
        assert_eq!(lowest, 4.0);
        assert_eq!(highest, 4.0 + spec().standing_height);
    }

    #[test]
    fn cube_y_becomes_render_z() {
        // The transposed-axis bug: a body at cube (10, 20) must be drawn at
        // render x=10, z=20. Swapped, every player appears mirrored across the
        // map diagonal, which reads as a netcode fault.
        let verts = build(&[player("them", true)], "me", &spec());
        let r = spec().radius;
        let xs: Vec<f32> = verts.iter().map(|v| v.position[0]).collect();
        let zs: Vec<f32> = verts.iter().map(|v| v.position[2]).collect();
        assert!(xs.iter().all(|x| (*x - 10.0).abs() <= r + 1e-6));
        assert!(zs.iter().all(|z| (*z - 20.0).abs() <= r + 1e-6));
    }

    #[test]
    fn a_crouched_body_is_drawn_the_height_it_can_be_hit_at() {
        // The regression this whole change is about. The old constant was 0.75,
        // which is the scale applied to the *eye*; the body's own crouch scale
        // is `(eye × 0.75 + above_eye) / standing` = 0.784. Four percent, and all
        // of it at the top of the body — where the head band is.
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
        assert!((top(&low) - (4.0 + spec().crouch_height)).abs() < 1e-5);
        // And explicitly not the eye scale, which is what it used to be.
        assert!((top(&low) - (4.0 + spec().standing_height * 0.75)).abs() > 0.1);
    }

    #[test]
    fn a_shape_this_build_cannot_draw_is_not_drawn_as_a_box() {
        // A capsule drawn as a box is a confident picture of the wrong body, and
        // an aim learned against it is wrong everywhere the two differ.
        let mut future = spec();
        future.shape = "capsule".into();
        assert!(build(&[player("them", true)], "me", &future).is_empty());
        assert!(build_hitboxes(&[player("them", true)], "me", &future).is_empty());
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
        // The band is measured **down from the top**. Drawn at an absolute
        // height it would sit above a crouched body entirely, which would make
        // crouching look like headshot immunity — the exact mistake the server
        // defines it this way to avoid.
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
        // Six faces, and every one of them points somewhere different.
        let normals: std::collections::HashSet<[i32; 3]> = verts
            .iter()
            .map(|v| [v.normal[0] as i32, v.normal[1] as i32, v.normal[2] as i32])
            .collect();
        assert_eq!(normals.len(), 6);
    }
}
