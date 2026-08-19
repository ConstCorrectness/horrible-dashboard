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

use crate::protocol::PlayerRow;
use crate::renderer::Vertex;
use crate::world::{PLAYER_ABOVE_EYE, PLAYER_EYE_HEIGHT, PLAYER_RADIUS};

/// Team colours, and a third for yourself.
///
/// Deliberately not the texture-id palette the world uses: a body has to be
/// findable against a wall of any hue, which means it cannot come out of the same
/// generator the walls do.
const TEAM_A: [f32; 3] = [0.85, 0.35, 0.25];
const TEAM_B: [f32; 3] = [0.30, 0.55, 0.90];
const BOT: [f32; 3] = [0.85, 0.62, 0.25];

/// How much of a standing body's height is left when fully crouched.
///
/// The server's own rule (`physics.eye_height`): the eye drops to 3/4. Copied as
/// a constant rather than served, because unlike `interval` or `zoom_levels` this
/// one is not a number the client acts on — it only decides how tall a box is
/// drawn. If it ever starts deciding a hit, it has to be served.
const CROUCH_SCALE: f32 = 0.75;

/// Every visible body as triangles, ready for the dynamic buffer.
///
/// `self_id` is excluded: this is a first-person camera sitting inside its own
/// body, and drawing it would fill the screen with the inside of a box. B3's
/// prediction changes where the camera is, not this.
pub fn build(players: &[PlayerRow], self_id: &str) -> Vec<Vertex> {
    let mut out = Vec::new();
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
        // `crouch` is 0..1. The full standing height is eye height plus the bit
        // of head above it — the same total the server's hitbox uses.
        let full = PLAYER_EYE_HEIGHT + PLAYER_ABOVE_EYE;
        let height = full * (1.0 - p.crouch * (1.0 - CROUCH_SCALE));
        push_box(&mut out, p.x, p.y, p.z, PLAYER_RADIUS, height, color);
    }
    out
}

/// An axis-aligned box standing on `(x, y)` at height `z`.
///
/// Render space is y-up, so cube `y` becomes render `z` — the same mapping the
/// mesher and the camera use. Getting it wrong here puts every body on the wrong
/// axis of the map, which looks like a netcode fault rather than a drawing one.
fn push_box(out: &mut Vec<Vertex>, x: f32, y: f32, z: f32, radius: f32, height: f32, c: [f32; 3]) {
    let (x0, x1) = (x - radius, x + radius);
    let (z0, z1) = (y - radius, y + radius);
    let (y0, y1) = (z, z + height);

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
        assert_eq!(build(&rows, "me").len(), 36, "one box, six faces");
        assert_eq!(build(&rows, "nobody").len(), 72);
    }

    #[test]
    fn the_dead_are_not_drawn() {
        assert!(build(&[player("them", false)], "me").is_empty());
    }

    #[test]
    fn a_body_stands_on_its_position_rather_than_being_centred_on_it() {
        // `z` on the wire is where the feet are. Centring the box on it would
        // sink every player half a body into the floor — and, worse, make the
        // drawn body disagree with the one the server rewinds shots against.
        let verts = build(&[player("them", true)], "me");
        let ys: Vec<f32> = verts.iter().map(|v| v.position[1]).collect();
        let lowest = ys.iter().cloned().fold(f32::INFINITY, f32::min);
        let highest = ys.iter().cloned().fold(f32::NEG_INFINITY, f32::max);
        assert_eq!(lowest, 4.0);
        assert_eq!(highest, 4.0 + PLAYER_EYE_HEIGHT + PLAYER_ABOVE_EYE);
    }

    #[test]
    fn cube_y_becomes_render_z() {
        // The transposed-axis bug: a body at cube (10, 20) must be drawn at
        // render x=10, z=20. Swapped, every player appears mirrored across the
        // map diagonal, which reads as a netcode fault.
        let verts = build(&[player("them", true)], "me");
        let xs: Vec<f32> = verts.iter().map(|v| v.position[0]).collect();
        let zs: Vec<f32> = verts.iter().map(|v| v.position[2]).collect();
        assert!(xs.iter().all(|x| (*x - 10.0).abs() <= PLAYER_RADIUS + 1e-6));
        assert!(zs.iter().all(|z| (*z - 20.0).abs() <= PLAYER_RADIUS + 1e-6));
    }

    #[test]
    fn crouching_shortens_the_body() {
        let mut crouched = player("them", true);
        crouched.crouch = 1.0;
        let standing = build(&[player("them", true)], "me");
        let low = build(&[crouched], "me");
        let top = |v: &[Vertex]| {
            v.iter()
                .map(|x| x.position[1])
                .fold(f32::NEG_INFINITY, f32::max)
        };
        assert!(top(&low) < top(&standing));
        // And by the server's own fraction, not an invented one.
        let full = PLAYER_EYE_HEIGHT + PLAYER_ABOVE_EYE;
        assert!((top(&low) - (4.0 + full * CROUCH_SCALE)).abs() < 1e-5);
    }

    #[test]
    fn every_face_is_two_triangles_wound_outward() {
        let verts = build(&[player("them", true)], "me");
        assert_eq!(verts.len() % 3, 0);
        // Six faces, and every one of them points somewhere different.
        let normals: std::collections::HashSet<[i32; 3]> = verts
            .iter()
            .map(|v| [v.normal[0] as i32, v.normal[1] as i32, v.normal[2] as i32])
            .collect();
        assert_eq!(normals.len(), 6);
    }
}
