//! True 3D World Representation and glTF/Procedural Level Loader.
//!
//! Provides arbitrary 3D polygonal level geometry (multi-tier arenas, catwalks,
//! ramps, stairs, and cover) for the native client, matching `world3d.ts`.

use crate::api::{ItemRow, MapInfo};
use crate::geometry::MeshData;
use rapier3d::prelude::*;

#[derive(Debug, Clone, Copy)]
pub struct SpawnPoint {
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub yaw: f32,
    pub team: i32, // 0 = CLA, 1 = RVSF
}

#[derive(Debug, Clone)]
pub struct WorldBounds {
    pub min: [f32; 3],
    pub max: [f32; 3],
    pub center: [f32; 3],
    pub extent: f32,
}

pub struct World3D {
    pub info: MapInfo,
    pub bounds: WorldBounds,
    /// Vertex positions in render space (x, z as height, y).
    pub render_positions: Vec<f32>,
    pub render_normals: Vec<f32>,
    pub render_colors: Vec<f32>,
    pub render_uvs: Vec<f32>,
    pub triangles: usize,
    /// Collision geometry in game space (x, y, z as height).
    pub col_vertices: Vec<Point<Real>>,
    pub col_indices: Vec<[u32; 3]>,
    pub spawns: Vec<SpawnPoint>,
    pub items: Vec<ItemRow>,
    pub waterlevel: f32,
}

impl World3D {
    pub fn to_mesh_data(&self) -> MeshData {
        MeshData {
            positions: self.render_positions.clone(),
            normals: self.render_normals.clone(),
            colors: self.render_colors.clone(),
            triangles: self.triangles,
        }
    }
}

struct FacilityBuilder {
    render_positions: Vec<f32>,
    render_normals: Vec<f32>,
    render_colors: Vec<f32>,
    render_uvs: Vec<f32>,
    col_vertices: Vec<Point<Real>>,
    col_indices: Vec<[u32; 3]>,
}

impl FacilityBuilder {
    fn new() -> Self {
        Self {
            render_positions: Vec::new(),
            render_normals: Vec::new(),
            render_colors: Vec::new(),
            render_uvs: Vec::new(),
            col_vertices: Vec::new(),
            col_indices: Vec::new(),
        }
    }

    fn add_triangle(
        &mut self,
        p0: [f32; 3],
        p1: [f32; 3],
        p2: [f32; 3],
        color: [f32; 3],
        is_collider: bool,
    ) {
        // Game coords [x, y, z] -> render coords [x, z, y]
        let t0 = [p0[0], p0[2], p0[1]];
        let t1 = [p1[0], p1[2], p1[1]];
        let t2 = [p2[0], p2[2], p2[1]];

        // Normal in render space
        let v_a = [t1[0] - t0[0], t1[1] - t0[1], t1[2] - t0[2]];
        let v_b = [t2[0] - t0[0], t2[1] - t0[1], t2[2] - t0[2]];
        let cross = [
            v_a[1] * v_b[2] - v_a[2] * v_b[1],
            v_a[2] * v_b[0] - v_a[0] * v_b[2],
            v_a[0] * v_b[1] - v_a[1] * v_b[0],
        ];
        let len_sq = cross[0] * cross[0] + cross[1] * cross[1] + cross[2] * cross[2];
        if len_sq < 1e-8 {
            return; // Skip degenerate triangle
        }
        let len = len_sq.sqrt();
        let norm = [cross[0] / len, cross[1] / len, cross[2] / len];

        self.render_positions.extend_from_slice(&t0);
        self.render_positions.extend_from_slice(&t1);
        self.render_positions.extend_from_slice(&t2);
        for _ in 0..3 {
            self.render_normals.extend_from_slice(&norm);
            self.render_colors.extend_from_slice(&color);
        }
        self.render_uvs.extend_from_slice(&[0.0, 0.0, 1.0, 0.0, 1.0, 1.0]);

        if is_collider {
            let base_idx = self.col_vertices.len() as u32;
            self.col_vertices.push(point![p0[0], p0[1], p0[2]]);
            self.col_vertices.push(point![p1[0], p1[1], p1[2]]);
            self.col_vertices.push(point![p2[0], p2[1], p2[2]]);
            self.col_indices.push([base_idx, base_idx + 1, base_idx + 2]);
        }
    }

    fn add_quad(
        &mut self,
        p0: [f32; 3],
        p1: [f32; 3],
        p2: [f32; 3],
        p3: [f32; 3],
        color: [f32; 3],
        is_collider: bool,
    ) {
        self.add_triangle(p0, p1, p2, color, is_collider);
        self.add_triangle(p0, p2, p3, color, is_collider);
    }

    fn add_floor(
        &mut self,
        min_x: f32,
        min_y: f32,
        max_x: f32,
        max_y: f32,
        z: f32,
        color: [f32; 3],
        is_collider: bool,
    ) {
        // Top face pointing strictly UP (+y in render space, norm = [0, 1, 0])
        self.add_quad(
            [min_x, max_y, z],
            [max_x, max_y, z],
            [max_x, min_y, z],
            [min_x, min_y, z],
            color,
            is_collider,
        );
    }

    fn add_box(
        &mut self,
        min_x: f32,
        min_y: f32,
        min_z: f32,
        max_x: f32,
        max_y: f32,
        max_z: f32,
        color: [f32; 3],
    ) {
        // Floor
        self.add_quad(
            [min_x, min_y, min_z],
            [max_x, min_y, min_z],
            [max_x, max_y, min_z],
            [min_x, max_y, min_z],
            color,
            true,
        );
        // Ceiling / Top
        self.add_quad(
            [min_x, max_y, max_z],
            [max_x, max_y, max_z],
            [max_x, min_y, max_z],
            [min_x, min_y, max_z],
            color,
            true,
        );
        // North
        self.add_quad(
            [max_x, max_y, min_z],
            [max_x, max_y, max_z],
            [min_x, max_y, max_z],
            [min_x, max_y, min_z],
            color,
            true,
        );
        // South
        self.add_quad(
            [min_x, min_y, min_z],
            [min_x, min_y, max_z],
            [max_x, min_y, max_z],
            [max_x, min_y, min_z],
            color,
            true,
        );
        // East
        self.add_quad(
            [max_x, min_y, min_z],
            [max_x, min_y, max_z],
            [max_x, max_y, max_z],
            [max_x, max_y, min_z],
            color,
            true,
        );
        // West
        self.add_quad(
            [min_x, max_y, min_z],
            [min_x, max_y, max_z],
            [min_x, min_y, max_z],
            [min_x, min_y, min_z],
            color,
            true,
        );
    }

    fn add_ramp(
        &mut self,
        min_x: f32,
        min_y: f32,
        z0: f32,
        max_x: f32,
        max_y: f32,
        z1: f32,
        color: [f32; 3],
    ) {
        let min_z = z0.min(z1);

        // 1. Slope Quad (wound so normal points UP into the sky, norm.y > 0)
        self.add_quad(
            [min_x, min_y, z0],
            [min_x, max_y, z1],
            [max_x, max_y, z1],
            [max_x, min_y, z0],
            color,
            true,
        );

        // 2. West side wall (x = min_x, outward normal norm.x < 0)
        self.add_triangle(
            [min_x, min_y, z0],
            [min_x, min_y, min_z],
            [min_x, max_y, z1],
            color,
            true,
        );
        self.add_triangle(
            [min_x, min_y, min_z],
            [min_x, max_y, min_z],
            [min_x, max_y, z1],
            color,
            true,
        );

        // 3. East side wall (x = max_x, outward normal norm.x > 0)
        self.add_triangle(
            [max_x, min_y, min_z],
            [max_x, min_y, z0],
            [max_x, max_y, z1],
            color,
            true,
        );
        self.add_triangle(
            [max_x, max_y, min_z],
            [max_x, min_y, min_z],
            [max_x, max_y, z1],
            color,
            true,
        );

        // 4. Back vertical wall if elevated above min_z
        if z1 > z0 && z1 > min_z {
            self.add_quad(
                [min_x, max_y, min_z],
                [max_x, max_y, min_z],
                [max_x, max_y, z1],
                [min_x, max_y, z1],
                color,
                true,
            );
        } else if z0 > z1 && z0 > min_z {
            self.add_quad(
                [max_x, min_y, min_z],
                [min_x, min_y, min_z],
                [min_x, min_y, z0],
                [max_x, min_y, z0],
                color,
                true,
            );
        }
    }
}

/// Create the inaugural "Deadzone Facility" 3D multi-tier arena.
pub fn create_procedural_facility_3d(info: MapInfo) -> World3D {
    let mut b = FacilityBuilder::new();

    // 1. Perimeter Walls (height 14)
    b.add_quad([0.0, 0.0, 0.0], [64.0, 0.0, 0.0], [64.0, 0.0, 14.0], [0.0, 0.0, 14.0], [0.22, 0.24, 0.28], true);
    b.add_quad([64.0, 64.0, 0.0], [0.0, 64.0, 0.0], [0.0, 64.0, 14.0], [64.0, 64.0, 14.0], [0.22, 0.24, 0.28], true);
    b.add_quad([64.0, 0.0, 0.0], [64.0, 64.0, 0.0], [64.0, 64.0, 14.0], [64.0, 0.0, 14.0], [0.25, 0.27, 0.31], true);
    b.add_quad([0.0, 64.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 14.0], [0.0, 64.0, 14.0], [0.25, 0.27, 0.31], true);

    // 2. Ground Floor (z = 0) with center opening
    b.add_floor(0.0, 0.0, 20.0, 64.0, 0.0, [0.32, 0.34, 0.36], true);
    b.add_floor(44.0, 0.0, 64.0, 64.0, 0.0, [0.32, 0.34, 0.36], true);
    b.add_floor(20.0, 0.0, 44.0, 20.0, 0.0, [0.30, 0.32, 0.35], true);
    b.add_floor(20.0, 44.0, 44.0, 64.0, 0.0, [0.30, 0.32, 0.35], true);

    // 3. Lower Coolant Pit (z = -5)
    b.add_floor(20.0, 20.0, 44.0, 44.0, -5.0, [0.18, 0.22, 0.26], true);
    // Pit walls facing inward into pit
    b.add_quad([20.0, 20.0, -5.0], [44.0, 20.0, -5.0], [44.0, 20.0, 0.0], [20.0, 20.0, 0.0], [0.28, 0.30, 0.34], true);
    b.add_quad([44.0, 44.0, -5.0], [20.0, 44.0, -5.0], [20.0, 44.0, 0.0], [44.0, 44.0, 0.0], [0.28, 0.30, 0.34], true);
    b.add_quad([44.0, 20.0, -5.0], [44.0, 44.0, -5.0], [44.0, 44.0, 0.0], [44.0, 20.0, 0.0], [0.28, 0.30, 0.34], true);
    b.add_quad([20.0, 44.0, -5.0], [20.0, 20.0, -5.0], [20.0, 20.0, 0.0], [20.0, 44.0, 0.0], [0.28, 0.30, 0.34], true);

    // Ramps into Pit
    b.add_ramp(28.0, 14.0, 0.0, 36.0, 20.0, -5.0, [0.42, 0.38, 0.32]);
    b.add_ramp(28.0, 44.0, -5.0, 36.0, 50.0, 0.0, [0.42, 0.38, 0.32]);

    // 4. Catwalk & Mezzanine (z = 6)
    b.add_floor(0.0, 0.0, 64.0, 6.0, 6.0, [0.48, 0.46, 0.44], true);
    b.add_floor(0.0, 58.0, 64.0, 64.0, 6.0, [0.48, 0.46, 0.44], true);
    b.add_floor(0.0, 6.0, 6.0, 58.0, 6.0, [0.46, 0.44, 0.42], true);
    b.add_floor(58.0, 6.0, 64.0, 58.0, 6.0, [0.46, 0.44, 0.42], true);
    // Catwalk bridge
    b.add_floor(26.0, 6.0, 38.0, 58.0, 6.0, [0.52, 0.50, 0.46], true);

    // Connecting Ramps
    b.add_ramp(2.0, 10.0, 0.0, 6.0, 26.0, 6.0, [0.45, 0.40, 0.35]);
    b.add_ramp(58.0, 38.0, 6.0, 62.0, 54.0, 0.0, [0.45, 0.40, 0.35]);

    // 5. Tactical Cover Blocks
    b.add_box(10.0, 12.0, 0.0, 14.0, 16.0, 3.0, [0.60, 0.48, 0.28]);
    b.add_box(50.0, 48.0, 0.0, 54.0, 52.0, 3.0, [0.60, 0.48, 0.28]);
    b.add_box(12.0, 48.0, 0.0, 16.0, 52.0, 3.5, [0.38, 0.42, 0.46]);
    b.add_box(48.0, 12.0, 0.0, 52.0, 16.0, 3.5, [0.38, 0.42, 0.46]);

    // Pillars
    b.add_box(24.0, 22.0, 0.0, 26.0, 24.0, 6.0, [0.25, 0.28, 0.32]);
    b.add_box(38.0, 22.0, 0.0, 40.0, 24.0, 6.0, [0.25, 0.28, 0.32]);
    b.add_box(24.0, 40.0, 0.0, 26.0, 42.0, 6.0, [0.25, 0.28, 0.32]);
    b.add_box(38.0, 40.0, 0.0, 40.0, 42.0, 6.0, [0.25, 0.28, 0.32]);

    let spawns = vec![
        SpawnPoint { x: 10.0, y: 8.0, z: 0.0, yaw: 0.0, team: 0 },
        SpawnPoint { x: 32.0, y: 4.0, z: 6.0, yaw: 0.0, team: 0 },
        SpawnPoint { x: 54.0, y: 8.0, z: 0.0, yaw: 315.0, team: 0 },
        SpawnPoint { x: 32.0, y: 10.0, z: 0.0, yaw: 0.0, team: 0 },
        SpawnPoint { x: 10.0, y: 56.0, z: 0.0, yaw: 180.0, team: 1 },
        SpawnPoint { x: 32.0, y: 60.0, z: 6.0, yaw: 180.0, team: 1 },
        SpawnPoint { x: 54.0, y: 56.0, z: 0.0, yaw: 225.0, team: 1 },
        SpawnPoint { x: 32.0, y: 54.0, z: 0.0, yaw: 180.0, team: 1 },
    ];

    let items = vec![
        ItemRow { id: 1, kind: "ammo_assault".into(), x: 32.0, y: 32.0, z: 6.0 },
        ItemRow { id: 2, kind: "armour".into(), x: 32.0, y: 32.0, z: -5.0 },
        ItemRow { id: 3, kind: "health".into(), x: 8.0, y: 32.0, z: 0.0 },
        ItemRow { id: 4, kind: "health".into(), x: 56.0, y: 32.0, z: 0.0 },
        ItemRow { id: 5, kind: "ammo_sniper".into(), x: 4.0, y: 4.0, z: 6.0 },
        ItemRow { id: 6, kind: "ammo_sniper".into(), x: 60.0, y: 60.0, z: 6.0 },
    ];

    let bounds = WorldBounds {
        min: [0.0, 0.0, -5.0],
        max: [64.0, 64.0, 14.0],
        center: [32.0, 32.0, 4.5],
        extent: 42.0,
    };

    let triangles = b.render_positions.len() / 9;

    World3D {
        info,
        bounds,
        render_positions: b.render_positions,
        render_normals: b.render_normals,
        render_colors: b.render_colors,
        render_uvs: b.render_uvs,
        triangles,
        col_vertices: b.col_vertices,
        col_indices: b.col_indices,
        spawns,
        items,
        waterlevel: -3.5,
    }
}

/// Create the Combat Arms inspired "Junk Flea" 3D multi-tier junkyard CQB arena.
pub fn create_procedural_junk_flea_3d(info: MapInfo) -> World3D {
    let mut b = FacilityBuilder::new();

    // 1. Perimeter Concrete/Steel Walls (height = 14)
    b.add_quad([4.0, 4.0, 0.0], [60.0, 4.0, 0.0], [60.0, 4.0, 14.0], [4.0, 4.0, 14.0], [0.24, 0.22, 0.20], true); // South
    b.add_quad([60.0, 60.0, 0.0], [4.0, 60.0, 0.0], [4.0, 60.0, 14.0], [60.0, 60.0, 14.0], [0.24, 0.22, 0.20], true); // North
    b.add_quad([60.0, 4.0, 0.0], [60.0, 60.0, 0.0], [60.0, 60.0, 14.0], [60.0, 4.0, 14.0], [0.26, 0.24, 0.22], true); // East
    b.add_quad([4.0, 60.0, 0.0], [4.0, 4.0, 0.0], [4.0, 4.0, 14.0], [4.0, 60.0, 14.0], [0.26, 0.24, 0.22], true); // West

    // 2. Ground Floor (z = 0) with Trench cutouts at x in [16..22] and [42..48], y in [20..44]
    b.add_floor(4.0, 4.0, 16.0, 60.0, 0.0, [0.35, 0.33, 0.30], true); // West strip
    b.add_floor(48.0, 4.0, 60.0, 60.0, 0.0, [0.35, 0.33, 0.30], true); // East strip
    b.add_floor(22.0, 4.0, 42.0, 60.0, 0.0, [0.33, 0.31, 0.28], true); // Center strip

    // North & South ground connectors across trenches
    b.add_floor(16.0, 4.0, 22.0, 14.0, 0.0, [0.33, 0.31, 0.28], true);
    b.add_floor(16.0, 50.0, 22.0, 60.0, 0.0, [0.33, 0.31, 0.28], true);
    b.add_floor(42.0, 4.0, 48.0, 14.0, 0.0, [0.33, 0.31, 0.28], true);
    b.add_floor(42.0, 50.0, 48.0, 60.0, 0.0, [0.33, 0.31, 0.28], true);

    // 3. Subterranean Trenches (z = -2.0)
    // West Trench
    b.add_floor(16.0, 20.0, 22.0, 44.0, -2.0, [0.20, 0.18, 0.16], true);
    b.add_quad([16.0, 20.0, -2.0], [16.0, 44.0, -2.0], [16.0, 44.0, 0.0], [16.0, 20.0, 0.0], [0.28, 0.26, 0.24], true);
    b.add_quad([22.0, 44.0, -2.0], [22.0, 20.0, -2.0], [22.0, 20.0, 0.0], [22.0, 44.0, 0.0], [0.28, 0.26, 0.24], true);
    // Ramps into West Trench
    b.add_ramp(16.0, 14.0, 0.0, 22.0, 20.0, -2.0, [0.40, 0.36, 0.32]);
    b.add_ramp(16.0, 44.0, -2.0, 22.0, 50.0, 0.0, [0.40, 0.36, 0.32]);

    // East Trench
    b.add_floor(42.0, 20.0, 48.0, 44.0, -2.0, [0.20, 0.18, 0.16], true);
    b.add_quad([42.0, 20.0, -2.0], [42.0, 44.0, -2.0], [42.0, 44.0, 0.0], [42.0, 20.0, 0.0], [0.28, 0.26, 0.24], true);
    b.add_quad([48.0, 44.0, -2.0], [48.0, 20.0, -2.0], [48.0, 20.0, 0.0], [48.0, 44.0, 0.0], [0.28, 0.26, 0.24], true);
    // Ramps into East Trench
    b.add_ramp(42.0, 14.0, 0.0, 48.0, 20.0, -2.0, [0.40, 0.36, 0.32]);
    b.add_ramp(42.0, 44.0, -2.0, 48.0, 50.0, 0.0, [0.40, 0.36, 0.32]);

    // 4. Large Shipping Containers (z in [0, 3.2])
    b.add_box(26.0, 20.0, 0.0, 38.0, 26.0, 3.2, [0.22, 0.38, 0.52]); // Center South Blue Container
    b.add_box(26.0, 38.0, 0.0, 38.0, 44.0, 3.2, [0.55, 0.25, 0.22]); // Center North Red Container
    b.add_box(8.0, 24.0, 0.0, 14.0, 40.0, 3.2, [0.38, 0.40, 0.30]);  // West Khaki Container
    b.add_box(50.0, 24.0, 0.0, 56.0, 40.0, 3.2, [0.58, 0.36, 0.20]); // East Weathered Orange Container

    // 5. Tactical Crates & Cover
    b.add_box(20.0, 12.0, 0.0, 24.0, 16.0, 1.8, [0.52, 0.42, 0.26]); // South yard crate
    b.add_box(40.0, 48.0, 0.0, 44.0, 52.0, 1.8, [0.52, 0.42, 0.26]); // North yard crate
    b.add_box(30.0, 30.0, 0.0, 34.0, 34.0, 1.4, [0.34, 0.38, 0.42]); // Center scrap crate

    // 6. High Steel Catwalk Bridge (z = 6.4)
    b.add_floor(30.0, 14.0, 34.0, 50.0, 6.4, [0.46, 0.44, 0.40], true);
    // Catwalk Access Ramps
    b.add_ramp(30.0, 8.0, 0.0, 34.0, 14.0, 6.4, [0.44, 0.40, 0.36]); // South ramp up
    b.add_ramp(30.0, 50.0, 6.4, 34.0, 56.0, 0.0, [0.44, 0.40, 0.36]); // North ramp down

    // Bridge Support Pillars
    b.add_box(29.0, 20.0, 0.0, 31.0, 22.0, 6.4, [0.26, 0.28, 0.30]);
    b.add_box(33.0, 20.0, 0.0, 35.0, 22.0, 6.4, [0.26, 0.28, 0.30]);
    b.add_box(29.0, 42.0, 0.0, 31.0, 44.0, 6.4, [0.26, 0.28, 0.30]);
    b.add_box(33.0, 42.0, 0.0, 35.0, 44.0, 6.4, [0.26, 0.28, 0.30]);

    let spawns = vec![
        SpawnPoint { x: 16.0, y: 12.0, z: 0.0, yaw: 0.0, team: 0 },
        SpawnPoint { x: 48.0, y: 12.0, z: 0.0, yaw: 0.0, team: 0 },
        SpawnPoint { x: 32.0, y: 10.0, z: 0.0, yaw: 0.0, team: 0 },
        SpawnPoint { x: 32.0, y: 15.0, z: 0.0, yaw: 0.0, team: 0 },
        SpawnPoint { x: 16.0, y: 52.0, z: 0.0, yaw: 180.0, team: 1 },
        SpawnPoint { x: 48.0, y: 52.0, z: 0.0, yaw: 180.0, team: 1 },
        SpawnPoint { x: 32.0, y: 54.0, z: 0.0, yaw: 180.0, team: 1 },
        SpawnPoint { x: 32.0, y: 49.0, z: 0.0, yaw: 180.0, team: 1 },
    ];

    let items = vec![
        ItemRow { id: 1, kind: "armour".into(), x: 32.0, y: 32.0, z: 0.0 },
        ItemRow { id: 2, kind: "health".into(), x: 19.0, y: 32.0, z: -2.0 },
        ItemRow { id: 3, kind: "health".into(), x: 45.0, y: 32.0, z: -2.0 },
        ItemRow { id: 4, kind: "ammo_assault".into(), x: 22.0, y: 12.0, z: 0.0 },
        ItemRow { id: 5, kind: "ammo_assault".into(), x: 42.0, y: 12.0, z: 0.0 },
        ItemRow { id: 6, kind: "ammo_sniper".into(), x: 22.0, y: 52.0, z: 0.0 },
        ItemRow { id: 7, kind: "ammo_sniper".into(), x: 42.0, y: 52.0, z: 0.0 },
        ItemRow { id: 8, kind: "grenade".into(), x: 12.0, y: 32.0, z: 0.0 },
        ItemRow { id: 9, kind: "clips".into(), x: 52.0, y: 32.0, z: 0.0 },
        ItemRow { id: 10, kind: "armour".into(), x: 32.0, y: 20.0, z: 0.0 },
    ];

    let bounds = WorldBounds {
        min: [4.0, 4.0, -2.5],
        max: [60.0, 60.0, 14.0],
        center: [32.0, 32.0, 5.75],
        extent: 38.0,
    };

    let triangles = b.render_positions.len() / 9;

    World3D {
        info,
        bounds,
        render_positions: b.render_positions,
        render_normals: b.render_normals,
        render_colors: b.render_colors,
        render_uvs: b.render_uvs,
        triangles,
        col_vertices: b.col_vertices,
        col_indices: b.col_indices,
        spawns,
        items,
        waterlevel: -5.0,
    }
}

/// Create the competitive tactical "The Bank" (`hd_bank`) 3D arena.
/// Features a neoclassical banking hall, fortified vault, mezzanine, street approach,
/// and high-skill movement shortcuts (SWAT van hood/roof skill jump, counter to balcony skips,
/// and jump-shooting vantage points).
pub fn create_procedural_bank_3d(info: MapInfo) -> World3D {
    let mut b = FacilityBuilder::new();

    // 1. Perimeter Walls (64x64 bounds, height = 14)
    b.add_quad([4.0, 4.0, 0.0], [60.0, 4.0, 0.0], [60.0, 4.0, 14.0], [4.0, 4.0, 14.0], [0.24, 0.25, 0.28], true); // South street
    b.add_quad([60.0, 60.0, 0.0], [4.0, 60.0, 0.0], [4.0, 60.0, 14.0], [60.0, 60.0, 14.0], [0.24, 0.25, 0.28], true); // North rear
    b.add_quad([60.0, 4.0, 0.0], [60.0, 60.0, 0.0], [60.0, 60.0, 14.0], [60.0, 4.0, 14.0], [0.26, 0.27, 0.30], true); // East
    b.add_quad([4.0, 60.0, 0.0], [4.0, 4.0, 0.0], [4.0, 4.0, 14.0], [4.0, 60.0, 14.0], [0.26, 0.27, 0.30], true); // West

    // 2. Ground Floors (Street Asphalt vs Bank Marble Floor)
    // Street asphalt (y: 4.0..18.0)
    b.add_floor(4.0, 4.0, 60.0, 18.0, 0.0, [0.20, 0.20, 0.22], true);
    // Sidewalk concrete curb (y: 14.0..18.0)
    b.add_box(4.0, 14.0, 0.0, 60.0, 18.0, 0.2, [0.48, 0.46, 0.44]);
    // Main bank marble floor (y: 18.0..46.0)
    b.add_floor(4.0, 18.0, 60.0, 46.0, 0.0, [0.72, 0.70, 0.66], true);
    // Rear offices floor (y: 46.0..60.0)
    b.add_floor(4.0, 46.0, 40.0, 60.0, 0.0, [0.58, 0.56, 0.52], true);
    // Vault steel floor (x: 40.0..60.0, y: 46.0..60.0)
    b.add_floor(40.0, 46.0, 60.0, 60.0, 0.0, [0.28, 0.30, 0.34], true);

    // 3. Bank Exterior Facade Wall (y: 18.0..22.0) with 3 Entrances
    // Solid sections
    b.add_box(4.0, 18.0, 0.0, 8.0, 22.0, 14.0, [0.55, 0.52, 0.48]);
    b.add_box(14.0, 18.0, 0.0, 28.0, 22.0, 14.0, [0.55, 0.52, 0.48]);
    b.add_box(36.0, 18.0, 0.0, 50.0, 22.0, 14.0, [0.55, 0.52, 0.48]);
    b.add_box(56.0, 18.0, 0.0, 60.0, 22.0, 14.0, [0.55, 0.52, 0.48]);
    // Neoclassical Columns flanking Grand Entrance
    b.add_box(26.0, 16.0, 0.0, 28.0, 18.0, 12.0, [0.82, 0.80, 0.78]);
    b.add_box(36.0, 16.0, 0.0, 38.0, 18.0, 12.0, [0.82, 0.80, 0.78]);
    // Entrance Stone Canopy (Key Skill Jump platform at z = 4.2)
    b.add_box(25.0, 15.0, 4.2, 39.0, 18.5, 4.8, [0.68, 0.66, 0.62]);

    // 4. Street Cover & Tactical Skill Jump Props
    // Armored SWAT Van (Hood = 1.5, Roof = 2.8)
    b.add_box(18.0, 10.0, 0.0, 22.0, 12.5, 1.5, [0.15, 0.20, 0.28]); // Hood
    b.add_box(18.0, 12.5, 0.0, 22.0, 16.0, 2.8, [0.12, 0.16, 0.24]); // Cab & Roof
    // Police Cruiser / Patrol Barricade
    b.add_box(42.0, 10.0, 0.0, 46.0, 14.0, 1.6, [0.18, 0.24, 0.35]);
    // Concrete Jersey Barrier
    b.add_box(30.0, 8.0, 0.0, 34.0, 10.0, 1.2, [0.50, 0.48, 0.46]);

    // 5. Grand Banking Hall (Site B) - Marble Pillars & Teller Counter
    // Neoclassical Marble Pillars
    b.add_box(22.0, 24.0, 0.0, 24.0, 26.0, 14.0, [0.78, 0.76, 0.72]);
    b.add_box(40.0, 24.0, 0.0, 42.0, 26.0, 14.0, [0.78, 0.76, 0.72]);
    b.add_box(22.0, 40.0, 0.0, 24.0, 42.0, 14.0, [0.78, 0.76, 0.72]);
    b.add_box(40.0, 40.0, 0.0, 42.0, 42.0, 14.0, [0.78, 0.76, 0.72]);
    // Teller Counter Island (Base = 1.4, Partitions = 3.6 for skill jump)
    b.add_box(28.0, 32.0, 0.0, 36.0, 35.0, 1.4, [0.45, 0.30, 0.18]);
    b.add_box(28.5, 32.2, 1.4, 31.5, 32.6, 3.4, [0.65, 0.75, 0.85]);
    b.add_box(32.5, 32.2, 1.4, 35.5, 32.6, 3.4, [0.65, 0.75, 0.85]);

    // 6. Executive Mezzanine & Balconies (z = 5.0)
    // West Balcony & Railing
    b.add_floor(14.0, 38.0, 20.0, 45.0, 5.0, [0.55, 0.52, 0.48], true);
    b.add_box(19.8, 38.0, 5.0, 20.2, 45.0, 6.1, [0.65, 0.58, 0.32]); // Brass railing
    b.add_ramp(14.0, 32.0, 0.0, 18.0, 38.0, 5.0, [0.48, 0.44, 0.40]);  // West stairs
    // Fire escape connection to exterior
    b.add_box(14.0, 18.0, 4.8, 18.0, 22.0, 5.0, [0.35, 0.33, 0.30]);
    // East Balcony & Railing
    b.add_floor(44.0, 38.0, 50.0, 45.0, 5.0, [0.55, 0.52, 0.48], true);
    b.add_box(43.8, 38.0, 5.0, 44.2, 45.0, 6.1, [0.65, 0.58, 0.32]); // Brass railing
    b.add_ramp(46.0, 32.0, 0.0, 50.0, 38.0, 5.0, [0.48, 0.44, 0.40]);  // East stairs

    // 7. Dividing Wall between Lobby and Rear Bank (y: 46.0..49.0)
    b.add_box(4.0, 46.0, 0.0, 10.0, 49.0, 14.0, [0.50, 0.48, 0.45]);
    b.add_box(16.0, 46.0, 0.0, 28.0, 49.0, 14.0, [0.50, 0.48, 0.45]);
    b.add_box(36.0, 46.0, 0.0, 48.0, 49.0, 14.0, [0.50, 0.48, 0.45]);
    b.add_box(54.0, 46.0, 0.0, 60.0, 49.0, 14.0, [0.50, 0.48, 0.45]);

    // 8. The Vault (Site A - x: 40.0..60.0, y: 49.0..60.0)
    // West Vault Partition Wall
    b.add_box(39.0, 49.0, 0.0, 41.0, 58.0, 10.0, [0.28, 0.30, 0.34]);
    // Massive Reinforced Vault Blast Door (semi-open portal)
    b.add_box(45.0, 49.0, 0.0, 46.5, 51.0, 8.0, [0.22, 0.24, 0.28]); // Portal frame
    b.add_box(46.5, 48.5, 0.0, 50.5, 49.5, 7.5, [0.35, 0.38, 0.44]); // Heavy round door slab
    // Safety Deposit Lockers
    b.add_box(56.5, 51.0, 0.0, 58.0, 58.0, 8.0, [0.60, 0.58, 0.52]);
    b.add_box(41.0, 56.5, 0.0, 56.5, 58.0, 8.0, [0.60, 0.58, 0.52]);
    // Cash & Gold Bullion Pallets (Cover inside Vault)
    b.add_box(44.0, 53.0, 0.0, 47.0, 56.0, 1.8, [0.72, 0.62, 0.25]);
    // HVAC Unit & Overhead Air Duct (for Jump Shooting into Vault)
    b.add_box(50.0, 44.0, 0.0, 53.0, 46.0, 1.8, [0.25, 0.32, 0.28]);
    b.add_box(49.0, 44.0, 3.4, 53.0, 48.0, 4.2, [0.45, 0.48, 0.50]);

    // 9. Staff Offices / Breakroom (Defender territory)
    b.add_box(14.0, 51.0, 0.0, 18.0, 54.0, 1.5, [0.42, 0.36, 0.30]);
    b.add_box(26.0, 51.0, 0.0, 30.0, 54.0, 1.5, [0.42, 0.36, 0.30]);

    let spawns = vec![
        SpawnPoint { x: 12.0, y: 8.0, z: 0.0, yaw: 0.0, team: 0 },
        SpawnPoint { x: 24.0, y: 8.0, z: 0.0, yaw: 0.0, team: 0 },
        SpawnPoint { x: 36.0, y: 8.0, z: 0.0, yaw: 0.0, team: 0 },
        SpawnPoint { x: 48.0, y: 8.0, z: 0.0, yaw: 0.0, team: 0 },
        SpawnPoint { x: 10.0, y: 55.0, z: 0.0, yaw: 180.0, team: 1 },
        SpawnPoint { x: 20.0, y: 55.0, z: 0.0, yaw: 180.0, team: 1 },
        SpawnPoint { x: 32.0, y: 55.0, z: 0.0, yaw: 180.0, team: 1 },
        SpawnPoint { x: 36.0, y: 51.0, z: 0.0, yaw: 180.0, team: 1 },
    ];

    let items = vec![
        ItemRow { id: 1, kind: "health".into(), x: 10.0, y: 14.0, z: 0.0 },
        ItemRow { id: 2, kind: "health".into(), x: 54.0, y: 14.0, z: 0.0 },
        ItemRow { id: 3, kind: "health".into(), x: 10.0, y: 51.0, z: 0.0 },
        ItemRow { id: 4, kind: "armour".into(), x: 32.0, y: 28.0, z: 0.0 },
        ItemRow { id: 5, kind: "armour".into(), x: 52.0, y: 53.0, z: 0.0 },
        ItemRow { id: 6, kind: "ammo_assault".into(), x: 20.0, y: 12.0, z: 0.0 },
        ItemRow { id: 7, kind: "ammo_assault".into(), x: 44.0, y: 12.0, z: 0.0 },
        ItemRow { id: 8, kind: "ammo_sniper".into(), x: 16.0, y: 40.0, z: 5.0 },
        ItemRow { id: 9, kind: "clips".into(), x: 32.0, y: 22.0, z: 0.0 },
        ItemRow { id: 10, kind: "grenade".into(), x: 52.0, y: 28.0, z: 0.0 },
    ];

    let bounds = WorldBounds {
        min: [4.0, 4.0, 0.0],
        max: [60.0, 60.0, 14.0],
        center: [32.0, 32.0, 5.0],
        extent: 40.0,
    };

    let triangles = b.render_positions.len() / 9;

    World3D {
        info,
        bounds,
        render_positions: b.render_positions,
        render_normals: b.render_normals,
        render_colors: b.render_colors,
        render_uvs: b.render_uvs,
        triangles,
        col_vertices: b.col_vertices,
        col_indices: b.col_indices,
        spawns,
        items,
        waterlevel: -100.0,
    }
}

/// Universal 3D Arena Factory: selects appropriate procedural 3D map generator.
pub fn create_world_3d(info: MapInfo) -> World3D {
    match info.name.as_str() {
        "hd_junkflea" => create_procedural_junk_flea_3d(info),
        "hd_bank" => create_procedural_bank_3d(info),
        _ => create_procedural_facility_3d(info),
    }
}
