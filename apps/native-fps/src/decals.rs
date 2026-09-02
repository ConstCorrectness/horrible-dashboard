//! Bullet marks: where the shots went, still there when you look back.
//!
//! The browser's `decals.ts`, in this client's terms. Both hold a fixed ring of
//! marks, both take the surface from the wire rather than deriving it, and
//! `browser_parity.rs` pins the cap, the lifetime and the six face normals.
//!
//! ## The normal comes off the wire
//!
//! A mark has to lie flat on the surface it is on, which means knowing which
//! face was hit. `weapons.raycast_world_face` on the server already knows it at
//! the instant its walk returns, and puts one small integer per pellet into the
//! `shot` fx. Deriving it here would be a copy of the world ray whose only job
//! is to agree with the server about the exact point the server chose — and half
//! a cube of disagreement puts the mark inside the wall, invisible.
//!
//! ## Why it draws into the volume pass
//!
//! A decal is alpha-blended, must be occluded by anything in front of it, and
//! must not write depth (marks overlap constantly, and a written depth makes the
//! newer one z-fight the older). Those are exactly the three properties the
//! translucent volume pipeline is built for — the same one water and smoke use —
//! so this needs no pipeline, no bind group and no shader of its own.
//!
//! **It shares that pass's vertex budget**, which is the one thing to watch: a
//! wall covered in marks must not silently evict the smoke. `MAX_MARKS` is small
//! enough that it cannot (128 marks is 768 vertices against 65536), and the
//! pool is written first so that if the budget ever does bite it is the
//! newest-arriving effects that overflow rather than the world's memory of being
//! shot at.

use crate::trace::{FACE_NONE, FACE_NORMALS};

/// How many marks the world remembers. The 129th retires the 1st.
pub const MAX_MARKS: usize = 128;
/// How long one mark lasts before it starts fading, in seconds.
pub const DECAL_LIFE: f32 = 22.0;
/// How long the fade at the end of that life takes.
pub const DECAL_FADE: f32 = 4.0;
/// How far off the surface a mark sits, in cube units. A decal coplanar with its
/// wall z-fights, which reads as flicker rather than as a bug.
pub const DECAL_LIFT: f32 = 0.012;
/// The mark's width, in cube units. A cube is roughly 36cm, so this is ~7cm.
pub const DECAL_SIZE: f32 = 0.2;

/// The crater's colour. Near-black rather than black, so a mark on a dark
/// surface is still a mark and not a hole in the render.
const MARK_COLOR: [f32; 3] = [0.102, 0.102, 0.102];

/// One impact, in cube coordinates.
#[derive(Debug, Clone, Copy)]
struct Mark {
    at: [f32; 3],
    face: usize,
    age: f32,
    live: bool,
}

impl Default for Mark {
    fn default() -> Mark {
        Mark {
            at: [0.0; 3],
            face: 0,
            age: 0.0,
            live: false,
        }
    }
}

/// How opaque a mark is, `age` seconds after it was made.
///
/// Flat for most of its life and then fading, rather than decaying from the
/// start: a mark that begins fading immediately is never quite legible, and
/// legibility over a whole magazine is the entire point now that the spray is a
/// pattern to be learned.
pub fn decal_opacity(age: f32) -> f32 {
    if age <= DECAL_LIFE - DECAL_FADE {
        1.0
    } else if age >= DECAL_LIFE {
        0.0
    } else {
        (DECAL_LIFE - age) / DECAL_FADE
    }
}

/// A fixed ring of marks. Nothing is allocated per shot and the cap holds by
/// construction rather than by a trim that has to run after every push.
pub struct DecalPool {
    marks: [Mark; MAX_MARKS],
    next: usize,
}

impl Default for DecalPool {
    fn default() -> DecalPool {
        DecalPool {
            marks: [Mark::default(); MAX_MARKS],
            next: 0,
        }
    }
}

impl DecalPool {
    /// Record one impact.
    ///
    /// A `face` of [`FACE_NONE`] — a body hit, or a shot that ran out of range —
    /// is silently ignored: there is no surface to mark. Checked here rather
    /// than at the call site so every caller has one fewer chance to index
    /// `FACE_NORMALS` with `-1`.
    pub fn mark(&mut self, at: [f32; 3], face: i32) {
        if face == FACE_NONE || face < 0 || face as usize >= FACE_NORMALS.len() {
            return;
        }
        self.marks[self.next] = Mark {
            at,
            face: face as usize,
            age: 0.0,
            live: true,
        };
        self.next = (self.next + 1) % MAX_MARKS;
    }

    /// Every pellet of one shot, skipping the ones that found no surface.
    ///
    /// An empty `faces` marks nothing at all — a shooter whose backend predates
    /// the field leaves no marks, which is degraded rather than wrong. Guessing
    /// a face would put every one of them on the same arbitrary axis.
    pub fn shot(&mut self, ends: &[[f32; 3]], faces: &[i32]) {
        for (i, end) in ends.iter().enumerate() {
            self.mark(*end, faces.get(i).copied().unwrap_or(FACE_NONE));
        }
    }

    /// Age every live mark, retiring the ones that have faded out.
    pub fn update(&mut self, dt: f32) {
        for mark in self.marks.iter_mut() {
            if !mark.live {
                continue;
            }
            mark.age += dt;
            if mark.age >= DECAL_LIFE {
                mark.live = false;
            }
        }
    }

    /// Forget everything. A new map is a new world, and marks left over from the
    /// last one would hang in mid-air.
    pub fn clear(&mut self) {
        self.marks = [Mark::default(); MAX_MARKS];
        self.next = 0;
    }

    /// How many marks are currently on the world.
    pub fn len(&self) -> usize {
        self.marks.iter().filter(|m| m.live).count()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Two triangles per mark, lifted off their surface and squared to it.
    ///
    /// `push` takes the same `(position, color, alpha)` shape the volume pass
    /// wants; the caller supplies it so this module needs no renderer type and
    /// stays unit-testable headless, exactly as `effects.rs` does.
    ///
    /// **Positions come out in render coordinates**, like every other producer
    /// feeding this pass (`effects.rs`, `nades.rs`, `water.rs`). The wire, the
    /// face normals and the quad's own arithmetic are all cube space — `x`, `y`
    /// horizontal and `z` up — and the renderer is y-up, so the mapping happens
    /// once, here, at the push. Emitting cube coordinates instead does not fail:
    /// every mark simply lands with its height and its depth swapped, which
    /// buries it inside the geometry and reads as decals not working at all.
    pub fn quads(&self, mut push: impl FnMut([f32; 3], [f32; 3], f32)) {
        for mark in self.marks.iter() {
            if !mark.live {
                continue;
            }
            let alpha = decal_opacity(mark.age);
            if alpha <= 0.0 {
                continue;
            }
            let n = FACE_NORMALS[mark.face];
            let (u, v) = basis(n);
            let h = DECAL_SIZE * 0.5;
            let centre = [
                mark.at[0] + n[0] * DECAL_LIFT,
                mark.at[1] + n[1] * DECAL_LIFT,
                mark.at[2] + n[2] * DECAL_LIFT,
            ];
            let corner = |su: f32, sv: f32| {
                [
                    centre[0] + u[0] * su * h + v[0] * sv * h,
                    centre[1] + u[1] * su * h + v[1] * sv * h,
                    centre[2] + u[2] * su * h + v[2] * sv * h,
                ]
            };
            let a = corner(-1.0, -1.0);
            let b = corner(1.0, -1.0);
            let c = corner(1.0, 1.0);
            let d = corner(-1.0, 1.0);
            // Two triangles. Wound the same way for both, so a mark is visible
            // from the side its surface faces and from nowhere else — which is
            // what stops a floor mark showing through the ceiling below it.
            //
            // Cube (x, y, z-up) → render (x, height, y), the same mapping
            // `nades.rs` and `effects.rs` apply at their own push.
            for p in [a, b, c, a, c, d] {
                push([p[0], p[2], p[1]], MARK_COLOR, alpha);
            }
        }
    }
}

/// Two unit vectors spanning the plane a normal is perpendicular to.
///
/// The axis-aligned case only, because a face normal is always an axis: picking
/// the two remaining axes is exact and needs no cross product, no normalisation
/// and no degenerate case to guard.
fn basis(n: [f32; 3]) -> ([f32; 3], [f32; 3]) {
    if n[0] != 0.0 {
        ([0.0, 1.0, 0.0], [0.0, 0.0, 1.0])
    } else if n[1] != 0.0 {
        ([1.0, 0.0, 0.0], [0.0, 0.0, 1.0])
    } else {
        ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::trace::{FACE_NX, FACE_PZ};

    #[test]
    fn a_body_hit_leaves_no_mark() {
        // The wall behind the body was never reached, and a mark on it would be
        // a lie about where the shot went.
        let mut pool = DecalPool::default();
        pool.mark([1.0, 2.0, 3.0], FACE_NONE);
        assert!(pool.is_empty());
    }

    #[test]
    fn an_out_of_range_face_is_refused_rather_than_indexed() {
        // `FACE_NONE` is negative so this cannot happen by accident, but a
        // malformed or newer server is not something to panic on.
        let mut pool = DecalPool::default();
        pool.mark([0.0; 3], 99);
        pool.mark([0.0; 3], -7);
        assert!(pool.is_empty());
    }

    #[test]
    fn a_shot_with_no_faces_marks_nothing() {
        // An older backend says nothing about surfaces. Marking nothing is
        // degraded; guessing a face would put every mark on the same axis.
        let mut pool = DecalPool::default();
        pool.shot(&[[1.0, 1.0, 1.0], [2.0, 2.0, 2.0]], &[]);
        assert!(pool.is_empty());
    }

    #[test]
    fn each_pellet_gets_its_own_face() {
        let mut pool = DecalPool::default();
        pool.shot(
            &[[1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [3.0, 0.0, 0.0]],
            &[FACE_NX, FACE_NONE, FACE_PZ],
        );
        assert_eq!(pool.len(), 2);
    }

    #[test]
    fn the_ring_retires_the_oldest_rather_than_growing() {
        let mut pool = DecalPool::default();
        for i in 0..MAX_MARKS * 3 {
            pool.mark([i as f32, 0.0, 0.0], FACE_NX);
        }
        assert_eq!(pool.len(), MAX_MARKS);
    }

    #[test]
    fn a_mark_stays_legible_and_then_goes() {
        assert_eq!(decal_opacity(0.0), 1.0);
        assert_eq!(decal_opacity(DECAL_LIFE - DECAL_FADE), 1.0);
        assert!((decal_opacity(DECAL_LIFE - DECAL_FADE * 0.5) - 0.5).abs() < 1e-5);
        assert_eq!(decal_opacity(DECAL_LIFE), 0.0);

        let mut pool = DecalPool::default();
        pool.mark([0.0; 3], FACE_NX);
        pool.update(DECAL_LIFE + 0.1);
        assert!(pool.is_empty());
    }

    #[test]
    fn a_mark_comes_out_in_render_coordinates() {
        // The one thing every other producer feeding the volume pass does and
        // this one did not: cube (x, y, z-up) → render (x, height, y). Without
        // it a mark's height and depth are swapped, so it is drawn inside the
        // map instead of on it — and every other assertion here still passed,
        // because they were all written in cube space too.
        let mut pool = DecalPool::default();
        pool.mark([10.0, 20.0, 30.0], FACE_PZ);
        let mut out = Vec::new();
        pool.quads(|p, _, _| out.push(p));
        assert_eq!(out.len(), 6);
        for p in out {
            // x is untouched, the mark's own height (30 + the lift) is render y,
            // and the cube's y is render z.
            assert!((p[0] - 10.0).abs() <= DECAL_SIZE, "x moved: {p:?}");
            assert!((p[1] - (30.0 + DECAL_LIFT)).abs() < 1e-4, "height: {p:?}");
            assert!((p[2] - 20.0).abs() <= DECAL_SIZE, "depth: {p:?}");
        }
    }

    #[test]
    fn a_mark_is_lifted_off_its_own_surface() {
        // Coplanar with the wall it is on, a decal z-fights — which reads as
        // flicker rather than as a bug, so it is never reported as one.
        // Render y, not cube z: this reads a mark on a *floor*, whose height is
        // the axis it is lifted along.
        let mut pool = DecalPool::default();
        pool.mark([5.0, 5.0, 0.0], FACE_PZ);
        let mut heights = Vec::new();
        pool.quads(|p, _, _| heights.push(p[1]));
        assert_eq!(heights.len(), 6);
        for z in heights {
            assert!((z - DECAL_LIFT).abs() < 1e-6, "mark sat at {z}");
        }
    }

    #[test]
    fn a_mark_lies_flat_in_its_own_face() {
        // A mark on a wall must have no extent along that wall's normal, or it
        // is a box rather than a decal.
        let mut pool = DecalPool::default();
        pool.mark([5.0, 5.0, 3.0], FACE_NX);
        let mut xs = Vec::new();
        pool.quads(|p, _, _| xs.push(p[0]));
        let lo = xs.iter().copied().fold(f32::MAX, f32::min);
        let hi = xs.iter().copied().fold(f32::MIN, f32::max);
        assert!((hi - lo).abs() < 1e-6, "the mark has depth: {lo} to {hi}");
    }

    #[test]
    fn a_mark_is_the_size_it_says_it_is() {
        let mut pool = DecalPool::default();
        pool.mark([5.0, 5.0, 0.0], FACE_PZ);
        let mut xs = Vec::new();
        pool.quads(|p, _, _| xs.push(p[0]));
        let lo = xs.iter().copied().fold(f32::MAX, f32::min);
        let hi = xs.iter().copied().fold(f32::MIN, f32::max);
        assert!((hi - lo - DECAL_SIZE).abs() < 1e-6);
    }

    #[test]
    fn a_new_map_forgets_the_last_one() {
        let mut pool = DecalPool::default();
        pool.mark([0.0; 3], FACE_NX);
        pool.clear();
        assert!(pool.is_empty());
    }
}
