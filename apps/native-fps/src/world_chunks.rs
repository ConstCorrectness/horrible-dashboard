//! The world, cut into cells: frustum-culled and drawn nearest first.
//!
//! The map is one static vertex buffer, and it used to be one draw: every
//! triangle in the map through the vertex shader every frame, in whatever order
//! the exporter wrote them, so a wall behind you was shaded and a floor behind a
//! wall was shaded and *then* covered. Measured on an iGPU at Low, the frame was
//! fill-bound — half the pixels was half the GPU time — which is what overdraw
//! looks like from the outside.
//!
//! So at load the triangles are **reordered** — not copied, not indexed — so each
//! cell's triangles are one contiguous range of the same buffer, and each frame:
//!
//! 1. every cell's box is tested against the view frustum's four side planes;
//! 2. the survivors are sorted nearest first, so the depth test rejects what is
//!    behind them before the fragment shader runs rather than after;
//! 3. each is one `draw` of its range. Same pipeline, same bind groups, so a
//!    draw costs a command and nothing else.
//!
//! What it does **not** change is what is visible. A cell is drawn whole or not
//! at all, a triangle belongs to the cell its centroid is in, and the cell's box
//! is grown to hold every triangle it was given — so a triangle that crosses
//! into view is drawn even when its centroid's cell is "elsewhere". Culling a
//! box that is on screen would be a hole in the map, which in this game is a
//! wall hack; the tests hold the box to its triangles for that reason.
//!
//! The shadow map is baked from the same buffer, and the bake does not care
//! what order the triangles are in.

use std::ops::Range;

use glam::{Mat4, Vec3, Vec4};

use crate::renderer::Vertex;

/// The cell edge, in cubes. Small enough that turning round culls most of the
/// map, big enough that a map is a hundred-odd draws rather than thousands.
pub const CELL: f32 = 24.0;

/// One cell's triangles: a range of the vertex buffer and the box around them.
#[derive(Clone, Debug, PartialEq)]
pub struct Chunk {
    pub vertices: Range<u32>,
    pub min: Vec3,
    pub max: Vec3,
}

/// Reorder `vertices` (a triangle list) so each cell's triangles are contiguous,
/// and return the cells. Nothing is added or dropped: the result is a
/// permutation of the input's triangles. A trailing partial triangle, which no
/// draw would use either, is left where it is at the end.
pub fn partition(vertices: &mut Vec<Vertex>, cell: f32) -> Vec<Chunk> {
    let triangles = vertices.len() / 3;
    if triangles == 0 {
        return Vec::new();
    }
    let cell_of = |t: usize| -> (i32, i32, i32) {
        let c = (Vec3::from(vertices[t * 3].position)
            + Vec3::from(vertices[t * 3 + 1].position)
            + Vec3::from(vertices[t * 3 + 2].position))
            / 3.0;
        // A NaN centroid (a degenerate exporter triangle) lands in cell 0
        // rather than panicking: `as` saturates NaN to 0.
        (
            (c.x / cell).floor() as i32,
            (c.y / cell).floor() as i32,
            (c.z / cell).floor() as i32,
        )
    };
    let mut order: Vec<((i32, i32, i32), u32)> =
        (0..triangles).map(|t| (cell_of(t), t as u32)).collect();
    // Stable, so a cell keeps the exporter's order within it — what it was
    // drawn in before, which is what keeps coplanar decals over their walls.
    order.sort_by_key(|(key, _)| *key);

    let tail = vertices.split_off(triangles * 3);
    let source = std::mem::take(vertices);
    vertices.reserve(source.len() + tail.len());

    let mut chunks: Vec<Chunk> = Vec::new();
    let mut current: Option<(i32, i32, i32)> = None;
    for (key, t) in order {
        let first = vertices.len() as u32;
        let tri = &source[t as usize * 3..t as usize * 3 + 3];
        vertices.extend_from_slice(tri);
        let (lo, hi) = tri.iter().fold(
            (Vec3::splat(f32::INFINITY), Vec3::splat(f32::NEG_INFINITY)),
            |(lo, hi), v| {
                let p = Vec3::from(v.position);
                (lo.min(p), hi.max(p))
            },
        );
        if current == Some(key) {
            let last = chunks.last_mut().expect("a current cell has a chunk");
            last.vertices.end = first + 3;
            last.min = last.min.min(lo);
            last.max = last.max.max(hi);
        } else {
            current = Some(key);
            chunks.push(Chunk {
                vertices: first..first + 3,
                min: lo,
                max: hi,
            });
        }
    }
    vertices.extend(tail);
    chunks
}

/// The four side planes of a view-projection, each `n·p + d >= 0` inside.
///
/// Near and far are left out on purpose. The side planes all pass through the
/// eye, and together they already require clip `w >= |x|, |y|`, which is "in
/// front of the eye" — so nothing behind the camera survives them. And the far
/// plane is far enough that testing it would never cull anything on a map, while
/// testing it *wrongly* (a projection whose depth runs the other way) would cull
/// everything.
pub fn side_planes(view_proj: Mat4) -> [Vec4; 4] {
    let r0 = view_proj.row(0);
    let r1 = view_proj.row(1);
    let r3 = view_proj.row(3);
    [r3 + r0, r3 - r0, r3 + r1, r3 - r1]
}

/// Whether any of the box can be inside all four planes. Conservative: a box
/// that is outside but near a frustum corner can pass, which costs a draw; a
/// box that is inside never fails, which is the half that matters.
pub fn box_visible(planes: &[Vec4; 4], min: Vec3, max: Vec3) -> bool {
    planes.iter().all(|p| {
        // The box corner furthest along the plane's normal.
        let corner = Vec3::new(
            if p.x >= 0.0 { max.x } else { min.x },
            if p.y >= 0.0 { max.y } else { min.y },
            if p.z >= 0.0 { max.z } else { min.z },
        );
        p.truncate().dot(corner) + p.w >= 0.0
    })
}

/// The cells to draw this frame, nearest first, into `out` as indices into
/// `chunks`. `scratch` is kept by the caller so a frame allocates nothing once
/// warm.
///
/// Not merged into fewer, longer ranges even where two neighbours are adjacent
/// in the buffer: each cell is drawn as its own instance so it can carry its
/// own light mask (`light_masks`).
pub fn visible(
    chunks: &[Chunk],
    view_proj: Mat4,
    eye: Vec3,
    scratch: &mut Vec<(f32, u32)>,
    out: &mut Vec<u32>,
) {
    scratch.clear();
    out.clear();
    let planes = side_planes(view_proj);
    for (i, c) in chunks.iter().enumerate() {
        if box_visible(&planes, c.min, c.max) {
            // Distance to the box, not its centre: the cell you are standing
            // in is distance 0 and goes first, however big it is.
            let nearest = eye.clamp(c.min, c.max);
            scratch.push((eye.distance_squared(nearest), i as u32));
        }
    }
    scratch.sort_unstable_by(|a, b| a.0.total_cmp(&b.0));
    out.extend(scratch.iter().map(|&(_, i)| i));
}

/// Every light on, for anything drawn as instance 0: bodies, the view model,
/// and whatever else shares the world pipeline without being a cell.
pub const ALL_LIGHTS: [u32; 2] = [u32::MAX, u32::MAX];

/// Which of this frame's point lights can reach each visible cell, as a 64-bit
/// mask per instance: `out[0]` is `ALL_LIGHTS`, and cell `i` is `out[i + 1]`.
/// `lights` is the uniform's slots in order — `(position, range)`.
///
/// **Exact, not an approximation.** The shader windows a light's attenuation
/// to zero at its range (`point_attenuation`) and skips it outright at
/// `distance >= range`; so a light whose sphere does not reach a cell's box
/// adds exactly nothing to any fragment in it, and leaving its bit clear only
/// saves the shader from working that out per pixel. Cells not visible this
/// frame keep whatever they had: they are not drawn.
pub fn light_masks(
    chunks: &[Chunk],
    visible: &[u32],
    lights: impl Iterator<Item = (Vec3, f32)> + Clone,
    out: &mut Vec<[u32; 2]>,
) {
    out.resize(chunks.len() + 1, ALL_LIGHTS);
    out[0] = ALL_LIGHTS;
    for &i in visible {
        let c = &chunks[i as usize];
        let mut mask = [0u32; 2];
        for (slot, (position, range)) in lights.clone().enumerate().take(64) {
            if position.distance_squared(position.clamp(c.min, c.max)) < range * range {
                mask[slot / 32] |= 1 << (slot % 32);
            }
        }
        out[i as usize + 1] = mask;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vertex(p: [f32; 3]) -> Vertex {
        Vertex {
            position: p,
            normal: [0.0, 1.0, 0.0],
            color: [1.0; 3],
            uv: [0.0; 2],
            material: 0.0,
            ao: 1.0,
            bounce: crate::renderer::NO_BOUNCE,
        }
    }

    fn tri(x: f32, y: f32, z: f32) -> [Vertex; 3] {
        [
            vertex([x, y, z]),
            vertex([x + 1.0, y, z]),
            vertex([x, y, z + 1.0]),
        ]
    }

    fn key(v: &[Vertex]) -> Vec<[u32; 9]> {
        let mut out: Vec<[u32; 9]> = v
            .chunks_exact(3)
            .map(|t| {
                let mut k = [0u32; 9];
                for (i, vx) in t.iter().enumerate() {
                    for j in 0..3 {
                        k[i * 3 + j] = vx.position[j].to_bits();
                    }
                }
                k
            })
            .collect();
        out.sort();
        out
    }

    fn world() -> Vec<Vertex> {
        let mut v = Vec::new();
        for (x, y, z) in [
            (1.0, 0.0, 1.0),
            (100.0, 0.0, 1.0),
            (2.0, 0.0, 2.0),
            (1.0, 30.0, 1.0),
            (100.0, 0.0, 100.0),
            (3.0, 0.0, 3.0),
            // Straddles the x = 24 boundary: centroid in cell 0, reaching cell 1.
            (23.5, 0.0, 5.0),
        ] {
            v.extend(tri(x, y, z));
        }
        v
    }

    #[test]
    fn partitioning_is_a_permutation_of_whole_triangles() {
        let before = world();
        let mut after = before.clone();
        let chunks = partition(&mut after, CELL);
        assert_eq!(key(&before), key(&after));
        // Every triangle is in exactly one chunk, and the chunks tile the buffer.
        let mut next = 0;
        for c in &chunks {
            assert_eq!(c.vertices.start, next);
            assert_eq!(c.vertices.len() % 3, 0);
            next = c.vertices.end;
        }
        assert_eq!(next as usize, after.len());
        // (1,0,1), (2,0,2), (3,0,3) and the straddler share a cell.
        assert_eq!(chunks.len(), 4);
    }

    #[test]
    fn a_cells_box_holds_every_triangle_it_was_given() {
        // The no-holes guarantee. A box smaller than its triangles would let a
        // wall on screen be culled.
        let mut v = world();
        let chunks = partition(&mut v, CELL);
        for c in &chunks {
            for vx in &v[c.vertices.start as usize..c.vertices.end as usize] {
                let p = Vec3::from(vx.position);
                assert!(
                    p.cmpge(c.min).all() && p.cmple(c.max).all(),
                    "{p} outside {c:?}"
                );
            }
        }
        // The straddler's far corner reaches past the cell edge, and so does the box.
        assert!(chunks.iter().any(|c| c.max.x >= 24.5));
    }

    #[test]
    fn a_trailing_partial_triangle_is_kept_at_the_end() {
        let mut v = world();
        v.push(vertex([7.0, 7.0, 7.0]));
        let chunks = partition(&mut v, CELL);
        assert_eq!(v.last().unwrap().position, [7.0, 7.0, 7.0]);
        assert_eq!(chunks.last().unwrap().vertices.end as usize, v.len() - 1);
    }

    fn camera(eye: Vec3, forward: Vec3) -> Mat4 {
        let proj = Mat4::perspective_rh(80f32.to_radians(), 16.0 / 9.0, 0.1, 2000.0);
        proj * Mat4::look_to_rh(eye, forward, Vec3::Y)
    }

    #[test]
    fn a_box_behind_the_eye_is_culled_and_one_ahead_is_kept() {
        let planes = side_planes(camera(Vec3::ZERO, Vec3::X));
        assert!(box_visible(
            &planes,
            Vec3::new(10.0, -1.0, -1.0),
            Vec3::new(12.0, 1.0, 1.0)
        ));
        assert!(!box_visible(
            &planes,
            Vec3::new(-12.0, -1.0, -1.0),
            Vec3::new(-10.0, 1.0, 1.0)
        ));
        // Off to the side, outside an 80° view.
        assert!(!box_visible(
            &planes,
            Vec3::new(1.0, -1.0, 30.0),
            Vec3::new(2.0, 1.0, 32.0)
        ));
        // Surrounding the eye: always drawn.
        assert!(box_visible(&planes, Vec3::splat(-5.0), Vec3::splat(5.0)));
    }

    #[test]
    fn visible_cells_come_nearest_first() {
        let mut v = world();
        let chunks = partition(&mut v, CELL);
        let mut scratch = Vec::new();
        let mut out = Vec::new();
        // At the origin, looking down +x and +z: everything but the cell straight
        // up out of view is ahead.
        let eye = Vec3::new(0.5, 1.0, 0.5);
        visible(
            &chunks,
            camera(eye, Vec3::new(1.0, 0.0, 1.0).normalize()),
            eye,
            &mut scratch,
            &mut out,
        );
        let dist = |&i: &u32| {
            let c = &chunks[i as usize];
            eye.distance(eye.clamp(c.min, c.max))
        };
        let d: Vec<f32> = out.iter().map(dist).collect();
        assert!(!d.is_empty());
        assert!(d.windows(2).all(|w| w[0] <= w[1]), "{d:?}");
        // Looking the other way, nothing far ahead of the old view survives.
        visible(
            &chunks,
            camera(eye, Vec3::new(-1.0, 0.0, -1.0).normalize()),
            eye,
            &mut scratch,
            &mut out,
        );
        let drawn: usize = out.iter().map(|&i| chunks[i as usize].vertices.len()).sum();
        assert!(drawn < v.len());
    }

    #[test]
    fn a_cell_lists_only_the_lights_that_reach_it() {
        let mut v = world();
        let chunks = partition(&mut v, CELL);
        let near = chunks
            .iter()
            .position(|c| c.min.x < 5.0 && c.max.y < 5.0)
            .unwrap() as u32;
        let far = chunks
            .iter()
            .position(|c| c.min.x > 90.0 && c.max.z < 5.0)
            .unwrap() as u32;
        // Slot 0 sits in the near cell; slot 1 is far from everything; slot 33
        // (the high word) reaches the far cell by exactly its range's worth.
        let mut lights = vec![(Vec3::new(500.0, 500.0, 500.0), 1.0); 34];
        lights[0] = (Vec3::new(2.0, 0.5, 2.0), 3.0);
        lights[33] = (Vec3::new(95.0, 0.0, 1.0), 5.5);
        let mut out = Vec::new();
        light_masks(&chunks, &[near, far], lights.iter().copied(), &mut out);
        assert_eq!(out.len(), chunks.len() + 1);
        assert_eq!(
            out[0], ALL_LIGHTS,
            "instance 0 is everything that is not a cell"
        );
        assert_eq!(out[near as usize + 1], [1, 0]);
        assert_eq!(out[far as usize + 1], [0, 1 << 1]);
    }

    #[test]
    fn a_light_exactly_at_range_is_left_out_as_the_shader_does() {
        // The shader skips `distance >= range`, so a box touching the sphere's
        // surface gets nothing from it — and the mask agrees rather than
        // spending the shader's time on it.
        let chunk = Chunk {
            vertices: 0..3,
            min: Vec3::ZERO,
            max: Vec3::ONE,
        };
        let mut out = Vec::new();
        let at = |range: f32| [(Vec3::new(3.0, 0.5, 0.5), range)].into_iter();
        light_masks(std::slice::from_ref(&chunk), &[0], at(2.0), &mut out);
        assert_eq!(out[1], [0, 0]);
        light_masks(std::slice::from_ref(&chunk), &[0], at(2.001), &mut out);
        assert_eq!(out[1], [1, 0]);
    }
}
