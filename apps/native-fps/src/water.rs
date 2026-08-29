//! The map's water plane.
//!
//! `waterlevel` was parsed and drawn by nothing, which was survivable only while
//! it also *did* nothing. Now that it decides how a body moves — no jump, two
//! thirds speed, no fall damage — an invisible water plane is the worst kind of
//! bug: a player who suddenly cannot jump, with nothing on screen to say why.
//!
//! **One plane, not a volume.** Cube 1 water is a single global height, with no
//! per-cell water, so a quad at `waterlevel` spanning the map is an exact
//! depiction rather than an approximation. The parts of it inside rock are hidden
//! by the rock.
//!
//! It goes in the **volume pass** with the smoke and the tracers, because it is
//! translucent and the body pass is not. `MODE_FLAT` rather than `MODE_CLOUD`:
//! the cloud shading is interior noise, which on a surface this large would read
//! as a rendering fault rather than as water.
//!
//! Emitted **both ways round** — two quads with opposite winding — because you
//! spend real time under it. Seen from below the surface is the ceiling of the
//! pool, and one wound only for the view from above would vanish the moment your
//! head went under, which is exactly when the player most needs to see where the
//! surface is.
//!
//! The colour is the **map's own** `watercolor`, not a constant: a mapper who
//! chose green water meant it.

use crate::renderer::{VolumeVertex, MODE_FLAT};
use crate::world::World;

/// Fallback tint for a map whose `watercolor` is unset (every channel zero).
/// The browser's `DEFAULT_COLOR` in `water.ts`.
const DEFAULT_COLOR: u32 = 0x2f6f8f;

/// How see-through the surface is. Opaque water hides the bottom of every pool.
const OPACITY: f32 = 0.42;

/// Ripple: how far the surface swings, and how fast.
///
/// Small on purpose. This is a plane a player has to judge a jump against, so it
/// must not visibly move the line they are aiming at.
const RIPPLE: f32 = 0.06;
const RIPPLE_SPEED: f32 = 0.9;

/// Whether this map has water worth drawing.
///
/// A plane below every floor is how a `.cgz` says "no water" — every official map
/// ships one — so the test is not "is `waterlevel` set" but "is any floor under
/// it". That is also the only reading that matches what `physics::in_water` will
/// actually do, which is the point: a map where the physics says water and the
/// renderer says none is the divergence this whole module exists to close.
pub fn has_water(world: &World) -> bool {
    for y in 0..world.ssize {
        for x in 0..world.ssize {
            if world.is_solid(x, y) {
                continue;
            }
            if world.floor_at(x, y) < world.waterlevel {
                return true;
            }
        }
    }
    false
}

/// Append the surface to the volume stream. Nothing at all when the map has none.
///
/// `elapsed` drives the ripple, and is the same clock the rest of the frame uses.
pub fn vertices(world: &World, elapsed: f32, out: &mut Vec<VolumeVertex>) {
    if !has_water(world) {
        return;
    }
    let (r, g, b, a) = channels(world);
    let colour = [r, g, b, a];
    let z = world.waterlevel + (elapsed * RIPPLE_SPEED).sin() * RIPPLE;
    let far = world.ssize as f32;

    // Render space is `[x, z, y]`, so the surface is a quad in the render x/z
    // plane at render-y `z`. Mapped here, once, exactly as `nades.rs` does it.
    let corners = [[0.0, z, 0.0], [far, z, 0.0], [far, z, far], [0.0, z, far]];

    // Up, then down. Two quads rather than one with culling disabled, because the
    // volume pass is shared and its pipeline state is not this module's to
    // change — and a lit surface needs the normal to face the viewer either way.
    for (normal, order) in [
        ([0.0f32, 1.0, 0.0], [0usize, 1, 2, 0, 2, 3]),
        ([0.0f32, -1.0, 0.0], [0usize, 2, 1, 0, 3, 2]),
    ] {
        for idx in order {
            out.push(VolumeVertex {
                position: corners[idx],
                normal,
                color: colour,
                mode: MODE_FLAT,
            });
        }
    }
}

/// The map's water colour as linear-ish channels plus alpha.
///
/// A map may carry its own alpha; zero means **unset**, not invisible — reading
/// it the second way makes every official map's water disappear, since most ship
/// a colour and no alpha.
fn channels(world: &World) -> (f32, f32, f32, f32) {
    let c = &world.info.watercolor;
    let (r, g, b) = (
        c.first().copied().unwrap_or(0),
        c.get(1).copied().unwrap_or(0),
        c.get(2).copied().unwrap_or(0),
    );
    let alpha = c.get(3).copied().unwrap_or(0);
    let hex = if r | g | b != 0 {
        ((r as u32) << 16) | ((g as u32) << 8) | b as u32
    } else {
        DEFAULT_COLOR
    };
    let rgb = crate::nades::rgb(hex);
    let a = if alpha != 0 {
        (alpha as f32 / 255.0).min(1.0)
    } else {
        OPACITY
    };
    (rgb[0], rgb[1], rgb[2], a)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::MapInfo;
    use crate::world::{SOLID, SPACE};

    const PLANES: [&str; 9] = [
        "type", "floor", "ceil", "wtex", "ftex", "ctex", "vdelta", "utex", "tag",
    ];

    fn world(floor: i8, waterlevel: f32, watercolor: Vec<u8>) -> World {
        let ssize = 8;
        let n = (ssize * ssize) as usize;
        let planes = vec![
            vec![SPACE; n],
            vec![floor as u8; n],
            vec![60u8; n],
            vec![0u8; n],
            vec![0u8; n],
            vec![0u8; n],
            vec![0u8; n],
            vec![0u8; n],
            vec![0u8; n],
        ];
        let info = MapInfo {
            ssize,
            cubic_size: n,
            plane_order: PLANES.iter().map(|s| s.to_string()).collect(),
            waterlevel,
            watercolor,
            ..Default::default()
        };
        World::new(info, &planes.concat()).unwrap()
    }

    #[test]
    fn a_plane_below_every_floor_is_how_a_map_says_it_has_no_water() {
        // Every official map ships one, so this is the common case and not an
        // edge: reading it as water would put a surface under the level in most
        // of the map set.
        assert!(!has_water(&world(0, -100.0, vec![])));
        assert!(has_water(&world(0, 6.0, vec![])));
    }

    #[test]
    fn a_map_with_no_water_draws_nothing_at_all() {
        let mut out = Vec::new();
        vertices(&world(0, -100.0, vec![]), 0.0, &mut out);
        assert!(out.is_empty());
    }

    #[test]
    fn the_surface_is_emitted_both_ways_round() {
        // You spend real time under it, where the surface is the ceiling of the
        // pool. Wound one way only, it would vanish exactly when your head went
        // under — which is when the player most needs to see where it is.
        let mut out = Vec::new();
        vertices(&world(0, 6.0, vec![]), 0.0, &mut out);
        assert_eq!(out.len(), 12, "two quads, six vertices each");
        let up = out.iter().filter(|v| v.normal[1] > 0.0).count();
        let down = out.iter().filter(|v| v.normal[1] < 0.0).count();
        assert_eq!((up, down), (6, 6));
    }

    #[test]
    fn the_surface_sits_at_the_water_level() {
        let mut out = Vec::new();
        vertices(&world(0, 6.0, vec![]), 0.0, &mut out);
        for v in &out {
            assert!(
                (v.position[1] - 6.0).abs() <= RIPPLE,
                "a vertex at {} is not on the water plane",
                v.position[1]
            );
        }
    }

    #[test]
    fn the_ripple_never_moves_the_line_a_player_judges_a_jump_against() {
        // Small on purpose. This is a plane people aim at.
        let mut a = Vec::new();
        let mut b = Vec::new();
        let w = world(0, 6.0, vec![]);
        vertices(&w, 0.0, &mut a);
        vertices(&w, 1.0, &mut b);
        let swing = (a[0].position[1] - b[0].position[1]).abs();
        assert!(swing <= RIPPLE * 2.0);
    }

    #[test]
    fn an_unset_alpha_means_unset_and_not_invisible() {
        // Most official maps ship a colour and no alpha. Read the other way,
        // every one of them would have water you cannot see.
        let mut out = Vec::new();
        vertices(&world(0, 6.0, vec![20, 90, 120, 0]), 0.0, &mut out);
        assert!((out[0].color[3] - OPACITY).abs() < 1e-6);
    }

    #[test]
    fn the_maps_own_colour_wins_over_the_fallback() {
        // A mapper who chose green water meant it.
        let mut out = Vec::new();
        vertices(&world(0, 6.0, vec![0, 200, 0, 128]), 0.0, &mut out);
        assert!(out[0].color[1] > out[0].color[0], "not the map's green");
        assert!(out[0].color[3] < 1.0);
    }

    #[test]
    fn water_under_solid_rock_is_not_water() {
        // `has_water` asks whether any *open* cell's floor is under the plane,
        // which is the same question `physics::in_water` will ask of a body.
        let ssize = 8;
        let n = (ssize * ssize) as usize;
        let planes = vec![
            vec![SOLID; n],
            vec![0u8; n],
            vec![60u8; n],
            vec![0u8; n],
            vec![0u8; n],
            vec![0u8; n],
            vec![0u8; n],
            vec![0u8; n],
            vec![0u8; n],
        ];
        let info = MapInfo {
            ssize,
            cubic_size: n,
            plane_order: PLANES.iter().map(|s| s.to_string()).collect(),
            waterlevel: 6.0,
            ..Default::default()
        };
        let w = World::new(info, &planes.concat()).unwrap();
        assert!(!has_water(&w));
    }
}
