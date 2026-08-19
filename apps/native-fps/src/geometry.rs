//! Turning the cube grid into triangles.
//!
//! A port of `packages/core/src/modules/hassault/geometry.ts`, and it follows the
//! Cube engine's rule: an open cell contributes a floor and a ceiling, and a
//! **wall appears wherever an open cell meets something taller** — a solid
//! neighbour, a neighbour with a higher floor, or one with a lower ceiling.
//!
//! Emitting a wall **from the open side only** is what makes the mesh cheap and
//! correct at once: every surface is produced exactly once, always facing the
//! space you can stand in, so there are no back faces to draw and no z-fighting
//! between two copies of the same wall.
//!
//! Output is flat vertex arrays rather than anything GPU-shaped, so this module
//! has no renderer dependency and stays testable with no device at all. B2 hands
//! these straight to `wgpu`.

use crate::world::{World, CHF, FHF};

/// Held whole even though this stage only reads `triangles`: these three
/// arrays are precisely what B2 uploads to the GPU, and building the mesh but
/// discarding it would make this stage prove less than it does.
#[allow(dead_code)]
pub struct MeshData {
    /// xyz per vertex, three vertices per triangle.
    pub positions: Vec<f32>,
    pub normals: Vec<f32>,
    pub colors: Vec<f32>,
    pub triangles: usize,
}

/// A stable, muted colour per texture id.
///
/// AssaultCube's real textures are resolved through map `.cfg` files this client
/// does not parse, so surfaces are tinted by texture id. That is not purely a
/// placeholder: giving distinct ids distinct hues is what makes walls, floors and
/// trim read as architecture instead of uniform grey soup. Golden-ratio hue
/// stepping puts adjacent ids far apart.
///
/// Identical maths to the TS version, so the two clients render the same map in
/// the same colours — a map that looks different in the native client is a map
/// somebody will report as a native-client bug.
fn tex_color(tex: u8, shade: f32) -> [f32; 3] {
    // The golden ratio conjugate, at the precision an f32 can actually hold —
    // the TS side spells it to f64 digits, but both round to the same f32, so
    // the two clients tint a map identically.
    let hue = (tex as f32 * 0.618_034) % 1.0;
    let sat = 0.22_f32;
    let light = (0.55 * shade).min(0.95);
    let f = |n: f32| {
        let k = (n + hue * 12.0) % 12.0;
        let a = sat * light.min(1.0 - light);
        light - a * (-1.0_f32).max(((k - 3.0).min(9.0 - k)).min(1.0))
    };
    [f(0.0), f(8.0), f(4.0)]
}

/// Face shading, so surfaces read apart without real lighting.
const SHADE_FLOOR: f32 = 1.0;
const SHADE_CEIL: f32 = 0.55;
const SHADE_WALL_X: f32 = 0.8;
const SHADE_WALL_Y: f32 = 0.68;

#[derive(Default)]
struct MeshBuilder {
    positions: Vec<f32>,
    normals: Vec<f32>,
    colors: Vec<f32>,
}

impl MeshBuilder {
    /// A quad as two triangles. Corners must be listed counter-clockwise as seen
    /// from the side the normal points at, or the face is back-face culled and
    /// the surface silently vanishes — which, with no back faces to fall back on,
    /// means a hole you can see through into the void.
    #[allow(clippy::too_many_arguments)]
    fn quad(
        &mut self,
        a: [f32; 3],
        b: [f32; 3],
        c: [f32; 3],
        d: [f32; 3],
        normal: [f32; 3],
        tex: u8,
        shade: f32,
    ) {
        self.positions.extend_from_slice(&a);
        self.positions.extend_from_slice(&b);
        self.positions.extend_from_slice(&c);
        self.positions.extend_from_slice(&a);
        self.positions.extend_from_slice(&c);
        self.positions.extend_from_slice(&d);
        for _ in 0..6 {
            self.normals.extend_from_slice(&normal);
        }
        let rgb = tex_color(tex, shade);
        for _ in 0..6 {
            self.colors.extend_from_slice(&rgb);
        }
    }

    fn finish(self) -> MeshData {
        let triangles = self.positions.len() / 9;
        MeshData {
            positions: self.positions,
            normals: self.normals,
            colors: self.colors,
            triangles,
        }
    }
}

/// Build the world mesh.
///
/// Solid cells contribute nothing, which implicitly skips the two-cube solid
/// border the engine guarantees — no special case needed for it.
pub fn build_world_mesh(world: &World) -> MeshData {
    let mut b = MeshBuilder::default();

    for y in 0..world.ssize {
        for x in 0..world.ssize {
            if world.is_solid(x, y) {
                continue;
            }
            let i = world.index(x, y);
            let t = world.cell_type[i];
            let fx = x as f32;
            let fy = y as f32;

            // Corner heights. For a flat cell all four come out equal, so flat
            // cells and heightfields go through exactly one path.
            let f00 = world.corner_floor(x, y, x, y);
            let f10 = world.corner_floor(x, y, x + 1, y);
            let f11 = world.corner_floor(x, y, x + 1, y + 1);
            let f01 = world.corner_floor(x, y, x, y + 1);
            let c00 = world.corner_ceil(x, y, x, y);
            let c10 = world.corner_ceil(x, y, x + 1, y);
            let c11 = world.corner_ceil(x, y, x + 1, y + 1);
            let c01 = world.corner_ceil(x, y, x, y + 1);

            // Floor: normal up, wound CCW seen from above.
            b.quad(
                [fx, f00, fy],
                [fx, f01, fy + 1.0],
                [fx + 1.0, f11, fy + 1.0],
                [fx + 1.0, f10, fy],
                [0.0, 1.0, 0.0],
                world.ftex[i],
                if t == FHF {
                    SHADE_FLOOR * 0.95
                } else {
                    SHADE_FLOOR
                },
            );

            // Ceiling: normal down, so the opposite winding.
            b.quad(
                [fx, c00, fy],
                [fx + 1.0, c10, fy],
                [fx + 1.0, c11, fy + 1.0],
                [fx, c01, fy + 1.0],
                [0.0, -1.0, 0.0],
                world.ctex[i],
                if t == CHF {
                    SHADE_CEIL * 0.95
                } else {
                    SHADE_CEIL
                },
            );

            emit_wall(&mut b, world, x, y, -1, 0);
            emit_wall(&mut b, world, x, y, 1, 0);
            emit_wall(&mut b, world, x, y, 0, -1);
            emit_wall(&mut b, world, x, y, 0, 1);
        }
    }
    b.finish()
}

/// The wall between open cell `(x, y)` and its neighbour in direction `(dx, dy)`.
///
/// Three cases: a solid neighbour walls off this cell's full height; an open
/// neighbour with a higher floor gives a step up (textured `wtex`); one with a
/// lower ceiling gives an overhang (textured **`utex`** — the *upper* wall
/// texture, and reaching for `wtex` there is a classic source of walls that are
/// subtly, unreportably wrong).
///
/// Both sides read the same two shared grid vertices, so heights meet exactly and
/// no seam opens even across heightfields.
fn emit_wall(b: &mut MeshBuilder, world: &World, x: i32, y: i32, dx: i32, dy: i32) {
    let nx = x + dx;
    let ny = y + dy;

    // The shared edge as two grid vertices, ordered so the quad winds CCW when
    // seen from inside this cell.
    let (vx0, vy0, vx1, vy1) = if dx == -1 {
        (x, y + 1, x, y)
    } else if dx == 1 {
        (x + 1, y, x + 1, y + 1)
    } else if dy == -1 {
        (x, y, x + 1, y)
    } else {
        (x + 1, y + 1, x, y + 1)
    };

    // The normal points from the neighbour back into this cell — the open side.
    let normal = [-dx as f32, 0.0, -dy as f32];
    let shade = if dx != 0 { SHADE_WALL_X } else { SHADE_WALL_Y };

    let my_f0 = world.corner_floor(x, y, vx0, vy0);
    let my_f1 = world.corner_floor(x, y, vx1, vy1);
    let my_c0 = world.corner_ceil(x, y, vx0, vy0);
    let my_c1 = world.corner_ceil(x, y, vx1, vy1);

    if world.is_solid(nx, ny) {
        // A solid cube stores only `wtex`; out of bounds falls back to ours.
        let tex = if world.in_bounds(nx, ny) {
            world.wtex[world.index(nx, ny)]
        } else {
            world.wtex[world.index(x, y)]
        };
        quad_vertical(
            b, vx0, vy0, my_f0, my_c0, vx1, vy1, my_f1, my_c1, normal, tex, shade,
        );
        return;
    }

    let nb = world.index(nx, ny);
    let me = world.index(x, y);

    // Step up: the neighbour's floor sits above ours.
    if world.floor[nb] > world.floor[me] {
        let top0 = world.corner_floor(nx, ny, vx0, vy0);
        let top1 = world.corner_floor(nx, ny, vx1, vy1);
        quad_vertical(
            b,
            vx0,
            vy0,
            my_f0,
            top0,
            vx1,
            vy1,
            my_f1,
            top1,
            normal,
            world.wtex[nb],
            shade,
        );
    }

    // Overhang: the neighbour's ceiling sits below ours.
    if world.ceil[nb] < world.ceil[me] {
        let bot0 = world.corner_ceil(nx, ny, vx0, vy0);
        let bot1 = world.corner_ceil(nx, ny, vx1, vy1);
        quad_vertical(
            b,
            vx0,
            vy0,
            bot0,
            my_c0,
            vx1,
            vy1,
            bot1,
            my_c1,
            normal,
            world.utex[nb],
            shade,
        );
    }
}

/// A vertical quad across two edge vertices, each with its own bottom and top.
#[allow(clippy::too_many_arguments)]
fn quad_vertical(
    b: &mut MeshBuilder,
    vx0: i32,
    vy0: i32,
    bottom0: f32,
    top0: f32,
    vx1: i32,
    vy1: i32,
    bottom1: f32,
    top1: f32,
    normal: [f32; 3],
    tex: u8,
    shade: f32,
) {
    // Degenerate: nothing to draw. Both ends have to be flat before it is
    // skipped — a wall that is zero-height at one end and two units at the other
    // is a real triangle across a slope.
    if top0 <= bottom0 && top1 <= bottom1 {
        return;
    }
    let (x0, y0) = (vx0 as f32, vy0 as f32);
    let (x1, y1) = (vx1 as f32, vy1 as f32);
    b.quad(
        [x0, bottom0, y0],
        [x1, bottom1, y1],
        [x1, top1, y1],
        [x0, top0, y0],
        normal,
        tex,
        shade,
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::MapInfo;
    use crate::world::{SEMISOLID, SOLID, SPACE};

    const PLANES: [&str; 9] = [
        "type", "floor", "ceil", "wtex", "ftex", "ctex", "vdelta", "utex", "tag",
    ];

    fn build(ssize: i32, edit: impl FnOnce(&mut Vec<Vec<u8>>)) -> World {
        let n = (ssize * ssize) as usize;
        let mut planes = vec![
            vec![SOLID; n],
            vec![0u8; n],
            vec![16u8; n],
            vec![1u8; n],
            vec![2u8; n],
            vec![3u8; n],
            vec![0u8; n],
            vec![4u8; n],
            vec![0u8; n],
        ];
        edit(&mut planes);
        let info = MapInfo {
            ssize,
            cubic_size: n,
            plane_order: PLANES.iter().map(|s| s.to_string()).collect(),
            ..Default::default()
        };
        World::new(info, &planes.concat()).unwrap()
    }

    /// Every vertex of the mesh, as (x, height, y) triples.
    fn verts(m: &MeshData) -> Vec<[f32; 3]> {
        m.positions
            .chunks_exact(3)
            .map(|c| [c[0], c[1], c[2]])
            .collect()
    }

    #[test]
    fn a_world_of_solid_rock_has_no_geometry() {
        let m = build_world_mesh(&build(8, |_| {}));
        assert_eq!(m.triangles, 0);
    }

    #[test]
    fn one_open_cell_gets_a_floor_a_ceiling_and_four_walls() {
        // Six quads = twelve triangles, and each quad carries 6 vertices.
        let w = build(8, |p| p[0][w_index(8, 3, 3)] = SPACE);
        let m = build_world_mesh(&w);
        assert_eq!(m.triangles, 12);
        assert_eq!(m.normals.len(), m.positions.len());
        assert_eq!(m.colors.len(), m.positions.len());
    }

    #[test]
    fn a_wall_between_two_open_cells_of_equal_height_is_not_drawn() {
        let w = build(8, |p| {
            p[0][w_index(8, 3, 3)] = SPACE;
            p[0][w_index(8, 4, 3)] = SPACE;
        });
        let m = build_world_mesh(&w);
        // Two cells: 2 floors + 2 ceilings + 6 outward walls (the shared face is
        // open on both sides and flat, so neither side emits it).
        assert_eq!(m.triangles, 2 * 2 * 2 + 6 * 2);
    }

    #[test]
    fn a_step_up_is_emitted_once_from_the_lower_side() {
        // Two open cells side by side, identical — the shared face is flat and
        // open on both sides, so neither emits anything there.
        let flat = build_world_mesh(&build(8, |p| {
            p[0][w_index(8, 3, 3)] = SPACE;
            p[0][w_index(8, 4, 3)] = SPACE;
        }));
        // The same pair with the neighbour's floor raised.
        let stepped = build_world_mesh(&build(8, |p| {
            p[0][w_index(8, 3, 3)] = SPACE;
            p[0][w_index(8, 4, 3)] = SPACE;
            p[1][w_index(8, 4, 3)] = 4;
        }));
        // Exactly **one** quad appears: the step, drawn from the lower side.
        // Emitting from both sides would add two and leave each step in the map
        // z-fighting with its own twin — which looks like a texture bug, not a
        // mesher bug, and is why this is worth pinning to the triangle.
        assert_eq!(
            stepped.triangles,
            flat.triangles + 2,
            "a step is one quad, not zero and not two"
        );
        // And it spans the right heights: bottom at our floor, top at theirs.
        let face: Vec<f32> = verts(&stepped)
            .into_iter()
            .filter(|v| v[0] == 4.0 && v[2] > 3.0 && v[2] < 4.0)
            .map(|v| v[1])
            .collect();
        assert!(
            face.is_empty(),
            "the face sits on grid vertices, not inside a cell"
        );
        let heights: Vec<f32> = verts(&stepped)
            .into_iter()
            .filter(|v| v[0] == 4.0)
            .map(|v| v[1])
            .collect();
        assert!(heights.contains(&0.0), "the step starts at our floor");
        assert!(heights.contains(&4.0), "and stops at theirs");
    }

    #[test]
    fn an_overhang_uses_utex_not_wtex() {
        // The classic subtle-wrong-wall bug. `utex` is 4 in this fixture and
        // `wtex` is 1, and they produce different colours, so the mesh itself
        // says which was used.
        let w = build(8, |p| {
            p[0][w_index(8, 3, 3)] = SPACE;
            p[0][w_index(8, 4, 3)] = SPACE;
            p[2][w_index(8, 4, 3)] = 8; // neighbour's ceiling is lower
        });
        let m = build_world_mesh(&w);
        let expected = tex_color(4, SHADE_WALL_X);
        let wrong = tex_color(1, SHADE_WALL_X);
        let colors: Vec<[f32; 3]> = m
            .colors
            .chunks_exact(3)
            .map(|c| [c[0], c[1], c[2]])
            .collect();
        assert!(
            colors.contains(&expected),
            "the overhang should be tinted with utex"
        );
        assert_ne!(expected, wrong, "the fixture must distinguish them");
    }

    #[test]
    fn a_semisolid_neighbour_walls_a_cell_off_like_solid_rock() {
        let w = build(8, |p| {
            p[0][w_index(8, 3, 3)] = SPACE;
            p[0][w_index(8, 4, 3)] = SEMISOLID;
        });
        let m = build_world_mesh(&w);
        assert_eq!(m.triangles, 12, "still floor + ceiling + four walls");
    }

    #[test]
    fn a_heightfield_meets_its_neighbour_with_no_seam() {
        // Two open cells, one a floor heightfield. The shared edge's two vertices
        // must come out at the same heights from both cells, or a crack opens
        // that you can see the void through.
        let w = build(16, |p| {
            for (x, y) in [(3, 3), (4, 3)] {
                p[0][w_index(16, x, y)] = SPACE;
            }
            p[0][w_index(16, 3, 3)] = FHF;
            p[1][w_index(16, 3, 3)] = 8;
            p[1][w_index(16, 4, 3)] = 8;
            p[6][w_index(16, 4, 3)] = 12; // delta owned by the vertex cell
            p[6][w_index(16, 4, 4)] = 12;
        });
        // The shared edge is at x = 4, vertices (4,3) and (4,4).
        let from_left = (w.corner_floor(3, 3, 4, 3), w.corner_floor(3, 3, 4, 4));
        // The neighbour is flat, so its own edge sits at its base height. What
        // must not happen is the *heightfield* side disagreeing with itself
        // between the wall it emits and the floor it emits.
        assert_eq!(from_left.0, 8.0 - 3.0);
        assert_eq!(from_left.1, 8.0 - 3.0);

        let m = build_world_mesh(&w);
        assert!(m.triangles > 0);
        // No vertex may land between the two heights: any value strictly inside
        // that gap is a torn seam.
        for v in verts(&m) {
            assert!(
                !(v[1] > 5.0001 && v[1] < 7.9999),
                "vertex at height {} is inside the seam",
                v[1]
            );
        }
    }

    #[test]
    fn the_solid_border_emits_no_outward_faces() {
        // A cell at the very edge, open. Its outward neighbours are out of
        // bounds, which counts as solid — so it is walled, not open to the void.
        let w = build(8, |p| p[0][w_index(8, 0, 0)] = SPACE);
        let m = build_world_mesh(&w);
        assert_eq!(m.triangles, 12);
        for v in verts(&m) {
            assert!(v[0] >= 0.0 && v[2] >= 0.0, "no geometry outside the grid");
        }
    }

    fn w_index(ssize: i32, x: i32, y: i32) -> usize {
        (y * ssize + x) as usize
    }
}
