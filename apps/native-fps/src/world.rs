//! The cube world: nine byte planes, plus the height rules everything else reads.
//!
//! A port of `packages/core/src/modules/hassault/world.ts`. Cube 1 worlds are a
//! flat grid of columns — each cell has a type, a floor and a ceiling height,
//! texture ids and a vertex delta — not a BSP, which is the whole reason a
//! from-scratch renderer is tractable at all.
//!
//! **Why this is a third copy.** The rules already exist in TypeScript (the
//! browser client) and in Python (`backend/modules/hassault/physics.py`, because
//! an authoritative server has to simulate). This is the third, and duplication
//! here fails in the nastiest way available: nothing throws, the geometry is
//! simply *slightly* different from what the server believes, and you get shots
//! that miss things you are looking at. The repo's existing answer is a shared
//! fixture both suites replay — `packages/core/src/modules/hassault/__tests__/
//! physics-vectors.json` — and this crate replays it too (see `tests/`).
//!
//! Coordinates: the grid is `(x, y)` with `z` as height. The renderer is y-up,
//! so world space maps as `render.x = cube.x`, `render.y = height`,
//! `render.z = cube.y`. One cube is one world unit.

use crate::api::MapInfo;

// Cube types — the on-disk encoding, from AssaultCube's `world.h`.
pub const SOLID: u8 = 0;
#[allow(dead_code)] // Part of the format; the mesher treats it as open.
pub const CORNER: u8 = 1;
pub const FHF: u8 = 2; // floor heightfield
pub const CHF: u8 = 3; // ceiling heightfield
#[allow(dead_code)]
pub const SPACE: u8 = 4;
pub const SEMISOLID: u8 = 5;

/// Two cubes from the edge of the world are always solid (`MINBORD` in `world.h`).
#[allow(dead_code)]
pub const MINBORD: i32 = 2;

/// Player dimensions, from AssaultCube's `entity.h` defaults.
#[allow(dead_code)]
pub const PLAYER_RADIUS: f32 = 1.1;
#[allow(dead_code)] // B2 puts the camera here; B3 traces shots from it.
pub const PLAYER_EYE_HEIGHT: f32 = 4.5;
#[allow(dead_code)]
pub const PLAYER_ABOVE_EYE: f32 = 0.7;

#[derive(Debug)]
pub enum WorldError {
    /// Fewer bytes than `cubic_size * plane_order.len()`.
    ShortPayload { got: usize, expected: usize },
    /// The backend named a plane in `plane_order` that this build does not know.
    UnknownPlane(String),
    /// `plane_order` did not contain a plane this code needs.
    MissingPlane(&'static str),
}

impl std::fmt::Display for WorldError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WorldError::ShortPayload { got, expected } => {
                write!(f, "cube payload is {got} bytes, expected {expected}")
            }
            WorldError::UnknownPlane(name) => {
                write!(f, "server sent an unknown cube plane {name:?}")
            }
            WorldError::MissingPlane(name) => {
                write!(f, "server's plane_order has no {name:?} plane")
            }
        }
    }
}

impl std::error::Error for WorldError {}

/// Every plane this client reads. Order here is **not** the wire order — the
/// server reports that in `plane_order`, and slicing by our own list instead is
/// exactly how the two sides silently drift.
const KNOWN_PLANES: [&str; 9] = [
    "type", "floor", "ceil", "wtex", "ftex", "ctex", "vdelta", "utex", "tag",
];

#[derive(Debug)]
pub struct World {
    pub info: MapInfo,
    pub ssize: i32,
    pub cell_type: Vec<u8>,
    /// Signed: a floor can sit below zero.
    pub floor: Vec<i8>,
    pub ceil: Vec<i8>,
    pub wtex: Vec<u8>,
    pub ftex: Vec<u8>,
    pub ctex: Vec<u8>,
    pub vdelta: Vec<u8>,
    pub utex: Vec<u8>,
    #[allow(dead_code)]
    pub tag: Vec<u8>,
}

impl World {
    /// Adopt the nine planes out of one `GET /maps/{name}/cubes` body.
    ///
    /// Sliced by the order **the server reported**, never a hardcoded list. The
    /// TS client does the same, and for the same reason: `plane_order` exists
    /// precisely so adding a plane server-side does not silently reinterpret
    /// every byte after it as some other field.
    pub fn new(info: MapInfo, cubes: &[u8]) -> Result<World, WorldError> {
        let n = info.cubic_size;
        let expected = n * info.plane_order.len();
        if cubes.len() < expected {
            return Err(WorldError::ShortPayload {
                got: cubes.len(),
                expected,
            });
        }
        for name in &info.plane_order {
            if !KNOWN_PLANES.contains(&name.as_str()) {
                return Err(WorldError::UnknownPlane(name.clone()));
            }
        }
        let plane = |name: &'static str| -> Result<&[u8], WorldError> {
            let idx = info
                .plane_order
                .iter()
                .position(|p| p == name)
                .ok_or(WorldError::MissingPlane(name))?;
            Ok(&cubes[idx * n..idx * n + n])
        };
        // `floor` and `ceil` are **signed**. Reading them unsigned turns every
        // below-zero floor into a value around 250 and lifts those cells into
        // orbit — silently, since nothing about the bytes says which they are.
        let signed = |bytes: &[u8]| bytes.iter().map(|&b| b as i8).collect::<Vec<i8>>();
        Ok(World {
            ssize: info.ssize,
            cell_type: plane("type")?.to_vec(),
            floor: signed(plane("floor")?),
            ceil: signed(plane("ceil")?),
            wtex: plane("wtex")?.to_vec(),
            ftex: plane("ftex")?.to_vec(),
            ctex: plane("ctex")?.to_vec(),
            vdelta: plane("vdelta")?.to_vec(),
            utex: plane("utex")?.to_vec(),
            tag: plane("tag")?.to_vec(),
            info,
        })
    }

    /// Flat index of a cell, matching the engine's `SWS(w,x,y,s)` macro.
    #[inline]
    pub fn index(&self, x: i32, y: i32) -> usize {
        (y * self.ssize + x) as usize
    }

    #[inline]
    pub fn in_bounds(&self, x: i32, y: i32) -> bool {
        x >= 0 && y >= 0 && x < self.ssize && y < self.ssize
    }

    /// Whether a cell blocks movement and hides its neighbours' faces.
    ///
    /// Out of bounds counts as **solid**: the engine guarantees a solid border,
    /// and treating the outside as open would both let a player walk off the map
    /// and make the border cells emit outward faces nobody can ever see.
    pub fn is_solid(&self, x: i32, y: i32) -> bool {
        if !self.in_bounds(x, y) {
            return true;
        }
        let t = self.cell_type[self.index(x, y)];
        t == SOLID || t == SEMISOLID
    }

    #[allow(dead_code)]
    pub fn type_at(&self, x: i32, y: i32) -> u8 {
        if self.in_bounds(x, y) {
            self.cell_type[self.index(x, y)]
        } else {
            SOLID
        }
    }

    /// The raw vertex delta stored at a grid vertex (clamped at the far edge).
    pub fn vdelta_at(&self, vx: i32, vy: i32) -> f32 {
        let cx = vx.clamp(0, self.ssize - 1);
        let cy = vy.clamp(0, self.ssize - 1);
        self.vdelta[self.index(cx, cy)] as f32
    }

    /// The floor height of cell `(x, y)` at one of its corners.
    ///
    /// **The split is the trap.** The base height comes from *the cell*; the
    /// delta comes from *the cell owning that corner vertex*. `physics.cpp:287`
    /// shows both halves — it starts at `s->floor` and subtracts an average of
    /// the four corner cells' deltas. Taking the base from the corner cell as
    /// well tears adjacent heightfields apart at every seam, and it does it
    /// quietly: the geometry still builds, it just has cracks in it.
    ///
    /// A non-heightfield cell ignores deltas entirely, so flat and sloped cells
    /// share one code path.
    pub fn corner_floor(&self, x: i32, y: i32, vx: i32, vy: i32) -> f32 {
        if !self.in_bounds(x, y) {
            return 0.0;
        }
        let i = self.index(x, y);
        let base = self.floor[i] as f32;
        if self.cell_type[i] == FHF {
            base - self.vdelta_at(vx, vy) / 4.0
        } else {
            base
        }
    }

    pub fn corner_ceil(&self, x: i32, y: i32, vx: i32, vy: i32) -> f32 {
        if !self.in_bounds(x, y) {
            return 0.0;
        }
        let i = self.index(x, y);
        let base = self.ceil[i] as f32;
        if self.cell_type[i] == CHF {
            base + self.vdelta_at(vx, vy) / 4.0
        } else {
            base
        }
    }

    /// The floor height a body standing in this cell rests on.
    ///
    /// Averaged across the cell's four corners — `(sum of four vdeltas) / 16`,
    /// which is `physics.cpp:287`. **The divisor differs from `corner_floor`'s on
    /// purpose**: this is the average of four `vdelta/4` terms, not a different
    /// constant. Using `/4` here sinks the player into every slope.
    pub fn floor_at(&self, x: i32, y: i32) -> f32 {
        if !self.in_bounds(x, y) {
            return 0.0;
        }
        let i = self.index(x, y);
        let mut f = self.floor[i] as f32;
        if self.cell_type[i] == FHF {
            f -= self.corner_delta_sum(x, y) / 16.0;
        }
        f
    }

    #[allow(dead_code)]
    pub fn ceil_at(&self, x: i32, y: i32) -> f32 {
        if !self.in_bounds(x, y) {
            return 0.0;
        }
        let i = self.index(x, y);
        let mut c = self.ceil[i] as f32;
        if self.cell_type[i] == CHF {
            c += self.corner_delta_sum(x, y) / 16.0;
        }
        c
    }

    fn corner_delta_sum(&self, x: i32, y: i32) -> f32 {
        let d = |cx: i32, cy: i32| {
            if self.in_bounds(cx, cy) {
                self.vdelta[self.index(cx, cy)] as f32
            } else {
                0.0
            }
        };
        d(x, y) + d(x + 1, y) + d(x, y + 1) + d(x + 1, y + 1)
    }

    /// Player spawn points, optionally for one team (`attrs[1]`: 0 CLA, 1 RVSF).
    #[allow(dead_code)] // B3 resolves a spawn against `floor_at`, as the server does.
    pub fn spawns(&self, team: Option<i32>) -> Vec<&crate::api::Entity> {
        self.info
            .entities
            .iter()
            .filter(|e| e.name == "playerstart")
            .filter(|e| match team {
                None => true,
                Some(t) => e.attrs.get(1).copied() == Some(t),
            })
            .collect()
    }
}

/// Test worlds, shared across the crate's suites.
///
/// Lifted out of `mod tests` when the radar needed one: a builder private to
/// one test module gets copy-pasted into the next, and two builders that were
/// meant to be the same world are two worlds that quietly differ.
#[cfg(test)]
impl World {
    /// A world of `ssize`² cells, all open.
    pub fn test_open(ssize: i32) -> World {
        World::test_build(ssize, |_| {})
    }

    /// An open interior inside a solid border — the shape every real map has,
    /// because a map open at the edge is one you can walk out of.
    pub fn test_box(ssize: i32) -> World {
        World::test_build(ssize, |planes| {
            for y in 0..ssize {
                for x in 0..ssize {
                    if x == 0 || y == 0 || x == ssize - 1 || y == ssize - 1 {
                        planes[0][(y * ssize + x) as usize] = SOLID;
                    }
                }
            }
        })
    }

    /// Built plane by plane **in the wire order**, so a test world is assembled
    /// the same way a real one is read.
    pub fn test_build(ssize: i32, mut edit: impl FnMut(&mut Vec<Vec<u8>>)) -> World {
        let n = (ssize * ssize) as usize;
        // type, floor, ceil, wtex, ftex, ctex, vdelta, utex, tag
        let mut planes = vec![
            vec![SPACE; n],
            vec![0u8; n],
            vec![16u8; n],
            vec![0u8; n],
            vec![0u8; n],
            vec![0u8; n],
            vec![0u8; n],
            vec![0u8; n],
            vec![0u8; n],
        ];
        edit(&mut planes);
        let bytes: Vec<u8> = planes.concat();
        let info = crate::api::MapInfo {
            name: "t".into(),
            ssize,
            cubic_size: n,
            plane_order: KNOWN_PLANES.iter().map(|s| s.to_string()).collect(),
            entities: vec![],
            ..Default::default()
        };
        World::new(info, &bytes).expect("world")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::MapInfo;

    /// A 4×4 world we can hand-check. See `World::test_build`.
    fn build(ssize: i32, edit: impl FnMut(&mut Vec<Vec<u8>>)) -> World {
        World::test_build(ssize, edit)
    }

    #[test]
    fn a_short_payload_is_refused_rather_than_read_past_the_end() {
        let info = MapInfo {
            ssize: 4,
            cubic_size: 16,
            plane_order: KNOWN_PLANES.iter().map(|s| s.to_string()).collect(),
            ..Default::default()
        };
        let err = World::new(info, &[0u8; 16 * 9 - 1]).unwrap_err();
        assert!(matches!(err, WorldError::ShortPayload { .. }));
    }

    #[test]
    fn planes_are_sliced_by_the_servers_order_not_ours() {
        // Same bytes, `floor` and `ceil` swapped in the declared order. If we
        // sliced by our own list we would read one as the other and never know.
        let n = 4;
        let mut planes = vec![vec![0u8; n]; 9];
        planes[1] = vec![7u8; n]; // second plane on the wire
        planes[2] = vec![9u8; n]; // third
        let bytes: Vec<u8> = planes.concat();
        let order = [
            "type", "ceil", "floor", "wtex", "ftex", "ctex", "vdelta", "utex", "tag",
        ];
        let info = MapInfo {
            ssize: 2,
            cubic_size: n,
            plane_order: order.iter().map(|s| s.to_string()).collect(),
            ..Default::default()
        };
        let w = World::new(info, &bytes).unwrap();
        assert_eq!(w.ceil[0], 7, "second wire plane was declared as ceil");
        assert_eq!(w.floor[0], 9, "third wire plane was declared as floor");
    }

    #[test]
    fn an_unknown_plane_is_an_error_not_a_guess() {
        let n = 4;
        let order = [
            "type", "floor", "ceil", "wtex", "ftex", "ctex", "vdelta", "utex", "lighting",
        ];
        let info = MapInfo {
            ssize: 2,
            cubic_size: n,
            plane_order: order.iter().map(|s| s.to_string()).collect(),
            ..Default::default()
        };
        let err = World::new(info, &vec![0u8; n * 9]).unwrap_err();
        assert!(matches!(err, WorldError::UnknownPlane(_)));
    }

    #[test]
    fn floors_are_signed() {
        // 0xF0 is -16, not 240. Read unsigned, this cell's floor ends up above
        // its own ceiling and the cell turns inside out.
        let w = build(4, |p| p[1][5] = 0xF0);
        assert_eq!(w.floor[5], -16);
        assert_eq!(w.floor_at(1, 1), -16.0);
    }

    #[test]
    fn out_of_bounds_is_solid() {
        let w = build(4, |_| {});
        assert!(w.is_solid(-1, 0));
        assert!(w.is_solid(0, -1));
        assert!(w.is_solid(4, 0));
        assert!(!w.is_solid(1, 1));
    }

    #[test]
    fn semisolid_is_solid_too() {
        let w = build(4, |p| p[0][5] = SEMISOLID);
        assert!(w.is_solid(1, 1));
    }

    #[test]
    fn the_base_comes_from_the_cell_and_the_delta_from_the_corner() {
        // Cell (1,1) is a floor heightfield at floor 8. Its neighbour at (2,1)
        // carries the delta for the shared corner vertex (2,1).
        let w = build(4, |p| {
            p[0][5] = FHF;
            p[1][5] = 8;
            p[6][6] = 12; // vdelta at cell (2,1)
        });
        // Corner (1,1) has no delta: base only.
        assert_eq!(w.corner_floor(1, 1, 1, 1), 8.0);
        // Corner (2,1) takes 12/4 = 3 off the *cell's* base of 8.
        assert_eq!(w.corner_floor(1, 1, 2, 1), 5.0);
        // Getting this backwards would read cell (2,1)'s own floor (0) as the
        // base and produce -3, tearing the seam open by 8 units.
        assert_ne!(w.corner_floor(1, 1, 2, 1), -3.0);
    }

    #[test]
    fn a_flat_cell_ignores_deltas_entirely() {
        let w = build(4, |p| {
            p[1][5] = 8; // floor, but type stays SPACE
            p[6][6] = 12;
        });
        assert_eq!(w.corner_floor(1, 1, 2, 1), 8.0);
        assert_eq!(w.floor_at(1, 1), 8.0);
    }

    #[test]
    fn standing_height_averages_four_corners_over_sixteen() {
        // One corner carries a delta of 12. Per-vertex that corner drops by 3
        // (12/4); the height a body *stands* on drops by 12/16 = 0.75.
        let w = build(4, |p| {
            p[0][5] = FHF;
            p[1][5] = 8;
            p[6][6] = 12;
        });
        assert_eq!(w.corner_floor(1, 1, 2, 1), 5.0);
        assert_eq!(w.floor_at(1, 1), 8.0 - 0.75);
        // The classic bug: reusing /4 here sinks the body 3 units into the slope.
        assert_ne!(w.floor_at(1, 1), 5.0);
    }

    #[test]
    fn a_ceiling_heightfield_grows_upward() {
        let w = build(4, |p| {
            p[0][5] = CHF;
            p[2][5] = 16;
            p[6][5] = 8;
        });
        // Ceilings *add* their delta where floors subtract theirs.
        assert_eq!(w.corner_ceil(1, 1, 1, 1), 18.0);
        assert_eq!(w.ceil_at(1, 1), 16.0 + 8.0 / 16.0);
    }
}
