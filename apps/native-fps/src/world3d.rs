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

    fn add_box_ex(
        &mut self,
        min_x: f32,
        min_y: f32,
        min_z: f32,
        max_x: f32,
        max_y: f32,
        max_z: f32,
        color: [f32; 3],
        is_collider: bool,
    ) {
        // Floor
        self.add_quad(
            [min_x, min_y, min_z],
            [max_x, min_y, min_z],
            [max_x, max_y, min_z],
            [min_x, max_y, min_z],
            color,
            is_collider,
        );
        // Ceiling / Top
        self.add_quad(
            [min_x, max_y, max_z],
            [max_x, max_y, max_z],
            [max_x, min_y, max_z],
            [min_x, min_y, max_z],
            color,
            is_collider,
        );
        // North
        self.add_quad(
            [max_x, max_y, min_z],
            [max_x, max_y, max_z],
            [min_x, max_y, max_z],
            [min_x, max_y, min_z],
            color,
            is_collider,
        );
        // South
        self.add_quad(
            [min_x, min_y, min_z],
            [min_x, min_y, max_z],
            [max_x, min_y, max_z],
            [max_x, min_y, min_z],
            color,
            is_collider,
        );
        // East
        self.add_quad(
            [max_x, min_y, min_z],
            [max_x, min_y, max_z],
            [max_x, max_y, max_z],
            [max_x, max_y, min_z],
            color,
            is_collider,
        );
        // West
        self.add_quad(
            [min_x, max_y, min_z],
            [min_x, max_y, max_z],
            [min_x, min_y, max_z],
            [min_x, min_y, min_z],
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
        self.add_box_ex(min_x, min_y, min_z, max_x, max_y, max_z, color, true);
    }

    fn add_cylinder(
        &mut self,
        cx: f32,
        cy: f32,
        min_z: f32,
        max_z: f32,
        radius: f32,
        segments: usize,
        color: [f32; 3],
        is_collider: bool,
    ) {
        let seg = segments.max(6);
        let step = std::f32::consts::TAU / (seg as f32);
        for i in 0..seg {
            let a0 = (i as f32) * step;
            let a1 = ((i + 1) as f32) * step;
            let x0 = cx + radius * a0.cos();
            let y0 = cy + radius * a0.sin();
            let x1 = cx + radius * a1.cos();
            let y1 = cy + radius * a1.sin();

            // Wall quad (faces outwards)
            self.add_quad(
                [x0, y0, min_z],
                [x1, y1, min_z],
                [x1, y1, max_z],
                [x0, y0, max_z],
                color,
                is_collider,
            );

            // Top disc (facing +z up)
            self.add_triangle(
                [cx, cy, max_z],
                [x1, y1, max_z],
                [x0, y0, max_z],
                color,
                is_collider,
            );

            // Bottom disc (facing -z down)
            self.add_triangle(
                [cx, cy, min_z],
                [x0, y0, min_z],
                [x1, y1, min_z],
                color,
                is_collider,
            );
        }
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

    // Architectural Color Palette
    let col_perimeter = [0.24, 0.25, 0.28];
    let col_asphalt = [0.18, 0.18, 0.20];
    let col_road_yellow = [0.88, 0.74, 0.12];
    let col_road_white = [0.88, 0.88, 0.90];
    let col_sidewalk = [0.55, 0.53, 0.50];
    let col_curb = [0.38, 0.36, 0.34];
    let col_facade = [0.65, 0.62, 0.58];
    let col_marble_floor = [0.82, 0.80, 0.76];
    let col_marble_border = [0.28, 0.29, 0.32];
    let col_column_base = [0.75, 0.73, 0.70];
    let col_column_shaft = [0.86, 0.84, 0.80];
    let col_gold = [0.92, 0.78, 0.24];
    let col_brass = [0.85, 0.72, 0.22];
    let col_mahogany = [0.38, 0.20, 0.12];
    let col_counter_top = [0.48, 0.28, 0.16];
    let col_glass = [0.65, 0.80, 0.88];
    let col_velvet = [0.68, 0.10, 0.14];
    let col_leather = [0.28, 0.18, 0.12];
    let col_steel = [0.32, 0.34, 0.38];
    let col_steel_bright = [0.58, 0.62, 0.66];
    let col_hazard_yellow = [0.90, 0.80, 0.10];
    let col_hazard_black = [0.12, 0.12, 0.14];
    let col_money = [0.24, 0.54, 0.28];
    let col_carpet = [0.46, 0.14, 0.18];
    let col_atm_blue = [0.15, 0.45, 0.85];
    let col_atm_screen = [0.20, 0.70, 0.95];
    let col_plant = [0.16, 0.42, 0.18];
    let col_police_red = [0.88, 0.12, 0.12];
    let col_police_blue = [0.12, 0.32, 0.88];
    let col_swat_navy = [0.14, 0.18, 0.25];
    let col_ceiling_beam = [0.46, 0.44, 0.40];
    let col_chdr_lamp = [1.0, 0.94, 0.70];
    let col_monitor = [0.12, 0.14, 0.16];
    let col_monitor_screen = [0.22, 0.38, 0.48];

    // 1. Perimeter Walls (64x64 bounds, height = 14)
    b.add_quad([4.0, 4.0, 0.0], [60.0, 4.0, 0.0], [60.0, 4.0, 14.0], [4.0, 4.0, 14.0], col_perimeter, true); // South street
    b.add_quad([60.0, 60.0, 0.0], [4.0, 60.0, 0.0], [4.0, 60.0, 14.0], [60.0, 60.0, 14.0], col_perimeter, true); // North rear
    b.add_quad([60.0, 4.0, 0.0], [60.0, 60.0, 0.0], [60.0, 60.0, 14.0], [60.0, 4.0, 14.0], [0.26, 0.27, 0.30], true); // East
    b.add_quad([4.0, 60.0, 0.0], [4.0, 4.0, 0.0], [4.0, 4.0, 14.0], [4.0, 60.0, 14.0], [0.26, 0.27, 0.30], true); // West

    // 2. Ground Floors (Street Asphalt vs Bank Marble Floor)
    // Street asphalt (y: 4.0..18.0)
    b.add_floor(4.0, 4.0, 60.0, 18.0, 0.0, col_asphalt, true);
    // Double solid yellow center divider stripe along y = 10.0
    b.add_floor(4.0, 9.85, 60.0, 9.95, 0.01, col_road_yellow, false);
    b.add_floor(4.0, 10.05, 60.0, 10.15, 0.01, col_road_yellow, false);
    // White pedestrian crosswalk stripes leading straight into bank entrance
    for i in 0..5 {
        let sx = 27.5 + (i as f32) * 2.0;
        b.add_floor(sx, 7.0, sx + 1.2, 13.5, 0.01, col_road_white, false);
    }

    // Sidewalk concrete curb (y: 14.0..18.0)
    b.add_box(4.0, 14.0, 0.0, 60.0, 18.0, 0.2, col_sidewalk);
    b.add_box(4.0, 13.8, 0.0, 60.0, 14.0, 0.25, col_curb);

    // Street infrastructure & furniture
    // Street lamps
    for lx in [12.0, 52.0] {
        b.add_cylinder(lx, 6.0, 0.0, 5.5, 0.12, 8, [0.22, 0.24, 0.22], false);
        b.add_box_ex(lx - 0.2, 5.8, 5.3, lx + 0.2, 7.5, 5.6, [0.22, 0.24, 0.22], false);
        b.add_box_ex(lx - 0.35, 7.0, 5.0, lx + 0.35, 7.6, 5.4, col_chdr_lamp, false);
    }
    // Red fire hydrants
    for hx in [8.0, 54.0] {
        b.add_cylinder(hx, 13.0, 0.0, 0.85, 0.22, 8, [0.85, 0.15, 0.15], true);
        b.add_box_ex(hx - 0.35, 12.8, 0.45, hx + 0.35, 13.2, 0.65, [0.85, 0.15, 0.15], false);
    }
    // Sidewalk tree planters with lush green hedges (soft tactical cover)
    for px in [14.0, 48.0] {
        b.add_box(px, 14.5, 0.2, px + 2.6, 17.5, 0.6, col_curb);
        b.add_box(px + 0.2, 14.7, 0.6, px + 2.4, 17.3, 1.6, col_plant);
    }

    // Main bank marble floor (y: 18.0..46.0)
    b.add_floor(4.0, 18.0, 60.0, 46.0, 0.0, col_marble_floor, true);
    // Dark marble borders around lobby perimeter and columns
    b.add_floor(4.0, 18.0, 60.0, 19.0, 0.01, col_marble_border, false);
    b.add_floor(4.0, 45.0, 60.0, 46.0, 0.01, col_marble_border, false);
    b.add_floor(4.0, 19.0, 5.0, 45.0, 0.01, col_marble_border, false);
    b.add_floor(59.0, 19.0, 60.0, 45.0, 0.01, col_marble_border, false);

    // Rear offices floor (y: 46.0..60.0)
    b.add_floor(4.0, 46.0, 40.0, 60.0, 0.0, [0.58, 0.56, 0.52], true);
    // Vault steel floor (x: 40.0..60.0, y: 46.0..60.0)
    b.add_floor(40.0, 46.0, 60.0, 60.0, 0.0, col_steel, true);

    // 3. Bank Exterior Facade Wall (y: 18.0..22.0) with 3 Entrances
    b.add_box(4.0, 18.0, 0.0, 8.0, 22.0, 14.0, col_facade);
    b.add_box(14.0, 18.0, 0.0, 28.0, 22.0, 14.0, col_facade);
    b.add_box(36.0, 18.0, 0.0, 50.0, 22.0, 14.0, col_facade);
    b.add_box(56.0, 18.0, 0.0, 60.0, 22.0, 14.0, col_facade);

    // Outdoor 24/7 ATM Wall Unit on west facade
    b.add_box_ex(9.5, 17.6, 0.4, 12.5, 18.0, 2.4, col_steel, true);
    b.add_box_ex(9.8, 17.55, 1.2, 12.2, 17.6, 1.8, col_atm_screen, false);
    b.add_box_ex(10.2, 17.55, 0.8, 11.8, 17.6, 1.0, col_steel_bright, false);
    b.add_box_ex(9.2, 17.4, 2.4, 12.8, 17.6, 2.8, col_atm_blue, false); // "24/7 ATM" sign

    // Neoclassical Columns flanking Grand Entrance
    for cx in [26.0, 36.0] {
        b.add_box(cx, 16.0, 0.0, cx + 2.0, 18.0, 0.8, col_column_base);
        b.add_cylinder(cx + 1.0, 17.0, 0.8, 12.5, 0.9, 12, col_column_shaft, true);
        b.add_box(cx - 0.2, 15.8, 12.5, cx + 2.2, 18.2, 14.0, col_column_base);
    }
    // Entrance Stone Canopy (Key Skill Jump platform at z = 4.2..4.8)
    b.add_box(25.0, 15.0, 4.2, 39.0, 18.5, 4.8, [0.68, 0.66, 0.62]);
    // Classical Triangular Pediment / Gable above entrance canopy
    b.add_triangle([24.0, 17.8, 4.8], [40.0, 17.8, 4.8], [32.0, 17.8, 8.2], col_facade, true);
    b.add_triangle([40.0, 18.2, 4.8], [24.0, 18.2, 4.8], [32.0, 18.2, 8.2], col_facade, true);
    // Gold embossed bank architrave banner
    b.add_box_ex(26.0, 17.6, 5.0, 38.0, 17.8, 5.8, col_brass, false);

    // Brass-trimmed Grand Double Doors in entrance portal
    b.add_box_ex(29.0, 18.1, 0.0, 35.0, 18.4, 3.8, col_mahogany, false);
    b.add_box_ex(29.5, 18.0, 0.4, 31.8, 18.2, 3.4, col_glass, false);
    b.add_box_ex(32.2, 18.0, 0.4, 34.5, 18.2, 3.4, col_glass, false);

    // 4. Street Cover & Tactical Skill Jump Props
    // Armored SWAT / Cash Van (Hood = 1.5, Roof = 2.8)
    b.add_box(18.0, 10.0, 0.0, 22.0, 12.5, 1.5, col_swat_navy); // Hood
    b.add_box(18.0, 12.5, 0.0, 22.0, 16.0, 2.8, [0.12, 0.16, 0.24]); // Cab & Roof
    // Van wheels
    for wy in [11.0, 14.5] {
        b.add_box_ex(17.6, wy, 0.0, 18.0, wy + 1.2, 0.75, [0.08, 0.08, 0.09], true);
        b.add_box_ex(22.0, wy, 0.0, 22.4, wy + 1.2, 0.75, [0.08, 0.08, 0.09], true);
    }
    // Van smoked windshield
    b.add_box_ex(18.2, 12.4, 1.5, 21.8, 12.7, 2.4, [0.22, 0.32, 0.42], false);
    // Van emergency strobe lightbar
    b.add_box_ex(18.5, 13.5, 2.8, 20.0, 14.0, 3.05, col_police_red, false);
    b.add_box_ex(20.0, 13.5, 2.8, 21.5, 14.0, 3.05, col_police_blue, false);
    // Steel bullbar push bumper
    b.add_box_ex(17.8, 9.7, 0.3, 22.2, 10.0, 0.9, col_steel_bright, true);

    // Police Patrol Cruiser
    b.add_box(42.0, 10.0, 0.0, 46.0, 14.0, 1.6, [0.18, 0.24, 0.35]);
    // Cruiser siren lightbar
    b.add_box_ex(42.5, 11.8, 1.6, 44.0, 12.3, 1.85, col_police_red, false);
    b.add_box_ex(44.0, 11.8, 1.6, 45.5, 12.3, 1.85, col_police_blue, false);

    // Concrete Jersey Barrier with warning hazard top
    b.add_box(30.0, 8.0, 0.0, 34.0, 10.0, 1.2, [0.50, 0.48, 0.46]);
    b.add_box_ex(30.0, 8.0, 1.15, 34.0, 10.0, 1.25, col_hazard_yellow, false);

    // 5. Grand Banking Hall (Site B) - Neoclassical Pillars, Coffered Ceiling, Teller Island
    // 4 Grand Marble Pillars in Lobby
    for (px, py) in [(22.0, 24.0), (40.0, 24.0), (22.0, 40.0), (40.0, 40.0)] {
        b.add_box(px, py, 0.0, px + 2.0, py + 2.0, 0.8, col_column_base);
        b.add_cylinder(px + 1.0, py + 1.0, 0.8, 12.5, 0.88, 12, col_column_shaft, true);
        b.add_box(px - 0.2, py - 0.2, 12.5, px + 2.2, py + 2.2, 14.0, col_column_base);
    }

    // Classical Coffered Ceiling Beams (creates grand banking hall depth)
    for bx in [22.0, 32.0, 42.0] {
        b.add_box_ex(bx - 0.4, 18.0, 13.0, bx + 0.4, 46.0, 14.0, col_ceiling_beam, false);
    }
    for by in [24.0, 32.0, 40.0] {
        b.add_box_ex(4.0, by - 0.4, 13.0, 60.0, by + 0.4, 14.0, col_ceiling_beam, false);
    }

    // Grand Central Chandelier
    b.add_cylinder(32.0, 32.0, 11.5, 12.0, 2.2, 8, col_brass, false);
    b.add_cylinder(32.0, 32.0, 10.8, 11.5, 1.4, 8, col_chdr_lamp, false);

    // Large Gold Wall-Mounted Bank Clock above entrance
    b.add_cylinder(32.0, 18.2, 7.5, 9.0, 0.85, 12, col_brass, false);
    b.add_cylinder(32.0, 18.15, 7.6, 8.9, 0.75, 12, [0.95, 0.95, 0.95], false);

    // Teller Counter Island (Base = 1.4, Partitions = 3.4 for skill jump)
    b.add_box(28.0, 32.0, 0.0, 36.0, 35.0, 1.4, col_mahogany);
    // Countertop polished lip
    b.add_box_ex(27.8, 31.8, 1.35, 36.2, 35.2, 1.45, col_counter_top, true);
    // Bulletproof Glass partitions with transaction speak-holes
    b.add_box(28.5, 32.2, 1.4, 31.5, 32.6, 3.4, col_glass);
    b.add_box(32.5, 32.2, 1.4, 35.5, 32.6, 3.4, col_glass);
    // 4 Teller Computer Terminals on counter
    for tx in [29.0, 30.5, 33.0, 34.5] {
        b.add_box_ex(tx, 33.2, 1.45, tx + 0.6, 33.6, 1.95, col_monitor, false);
        b.add_box_ex(tx + 0.05, 33.15, 1.5, tx + 0.55, 33.2, 1.9, col_monitor_screen, false);
    }

    // Customer Queue Stanchions (Polished brass posts with crimson velvet ropes)
    for qx in [28.0, 30.0, 32.0, 34.0, 36.0] {
        b.add_cylinder(qx, 28.5, 0.0, 0.9, 0.08, 6, col_brass, false);
        b.add_cylinder(qx, 28.5, 0.85, 0.95, 0.12, 6, col_brass, false); // top ball
    }
    b.add_box_ex(28.0, 28.45, 0.7, 36.0, 28.55, 0.8, col_velvet, false); // velvet rope

    // Center Customer Island Writing Desks
    for dx in [23.0, 39.0] {
        b.add_box(dx, 27.5, 0.0, dx + 2.0, 29.5, 1.0, col_mahogany);
        b.add_box_ex(dx + 0.2, 28.0, 1.0, dx + 1.8, 29.0, 1.15, col_brass, false); // slip tray
    }

    // Free-standing Indoor ATM Kiosks along west wall
    for ay in [26.0, 34.0] {
        b.add_box(4.8, ay, 0.0, 6.2, ay + 1.4, 2.1, col_steel);
        b.add_box_ex(6.0, ay + 0.2, 1.2, 6.25, ay + 1.2, 1.8, col_atm_screen, false);
        b.add_box_ex(4.8, ay, 2.0, 6.2, ay + 1.4, 2.3, col_atm_blue, false);
    }

    // Waiting Lounge Area (East wall): Executive leather sofa & coffee table
    b.add_box(57.0, 27.0, 0.0, 59.2, 33.0, 0.85, col_leather);
    b.add_box_ex(58.5, 27.0, 0.85, 59.5, 33.0, 1.4, col_leather, false); // backrest
    b.add_box(54.5, 28.5, 0.0, 56.2, 31.5, 0.5, col_glass); // coffee table

    // Potted Ficus/Palm Trees in Lobby Corners
    for (px, py) in [(6.0, 20.0), (58.0, 20.0), (6.0, 44.0)] {
        b.add_cylinder(px, py, 0.0, 0.7, 0.55, 8, col_column_base, false);
        b.add_cylinder(px, py, 0.7, 2.4, 0.9, 8, col_plant, false);
    }

    // Tactical Bomb Site B Floor Stencil
    b.add_floor(30.0, 24.5, 34.0, 27.5, 0.015, [0.85, 0.20, 0.15], false);

    // 6. Executive Mezzanine & Balconies (z = 5.0)
    // West Balcony & Railing
    b.add_floor(14.0, 38.0, 20.0, 45.0, 5.0, col_marble_floor, true);
    b.add_floor(15.5, 38.0, 18.5, 45.0, 5.01, col_carpet, false); // Royal burgundy runner
    b.add_box(19.8, 38.0, 5.0, 20.2, 45.0, 6.1, col_brass); // Brass railing
    b.add_box_ex(19.9, 38.0, 5.2, 20.1, 45.0, 6.0, col_glass, false); // Glass balustrade
    b.add_ramp(14.0, 32.0, 0.0, 18.0, 38.0, 5.0, [0.48, 0.44, 0.40]);  // West stairs
    // Fire escape connection to exterior
    b.add_box(14.0, 18.0, 4.8, 18.0, 22.0, 5.0, [0.35, 0.33, 0.30]);

    // East Balcony & Railing
    b.add_floor(44.0, 38.0, 50.0, 45.0, 5.0, col_marble_floor, true);
    b.add_floor(45.5, 38.0, 48.5, 45.0, 5.01, col_carpet, false);
    b.add_box(43.8, 38.0, 5.0, 44.2, 45.0, 6.1, col_brass); // Brass railing
    b.add_box_ex(43.9, 38.0, 5.2, 44.1, 45.0, 6.0, col_glass, false);
    b.add_ramp(46.0, 32.0, 0.0, 50.0, 38.0, 5.0, [0.48, 0.44, 0.40]);  // East stairs

    // 7. Dividing Wall between Lobby and Rear Bank (y: 46.0..49.0)
    b.add_box(4.0, 46.0, 0.0, 10.0, 49.0, 14.0, [0.50, 0.48, 0.45]);
    b.add_box(16.0, 46.0, 0.0, 28.0, 49.0, 14.0, [0.50, 0.48, 0.45]);
    b.add_box(36.0, 46.0, 0.0, 48.0, 49.0, 14.0, [0.50, 0.48, 0.45]);
    b.add_box(54.0, 46.0, 0.0, 60.0, 49.0, 14.0, [0.50, 0.48, 0.45]);

    // Security Surveillance Desk in security checkpoint
    b.add_box(32.0, 46.5, 0.0, 35.5, 48.5, 1.1, col_steel);
    b.add_box_ex(32.2, 47.0, 1.1, 35.3, 47.4, 1.8, col_monitor, false);
    b.add_box_ex(32.3, 47.35, 1.15, 35.2, 47.4, 1.75, col_monitor_screen, false);

    // 8. The Vault (Site A - x: 40.0..60.0, y: 49.0..60.0)
    b.add_box(39.0, 49.0, 0.0, 41.0, 58.0, 10.0, col_steel);
    // Vault Entrance Portal with Hazard Warning Stripes
    b.add_box(45.0, 49.0, 0.0, 46.5, 51.0, 8.0, col_hazard_yellow);
    b.add_box_ex(45.1, 48.8, 0.5, 46.4, 49.0, 7.5, col_hazard_black, false);

    // Massive Round Vault Blast Door (semi-open portal)
    b.add_box(46.5, 48.5, 0.0, 50.5, 49.5, 7.5, col_steel); // Main door block for collision
    // Circular door detail
    b.add_cylinder(48.5, 49.0, 1.0, 6.5, 2.0, 12, col_steel_bright, false);
    // Central 4-spoke handwheel & lock mechanism
    b.add_cylinder(48.5, 48.6, 3.2, 4.2, 0.55, 8, col_brass, false);
    b.add_box_ex(47.6, 48.5, 3.65, 49.4, 48.65, 3.85, col_brass, false);
    b.add_box_ex(48.4, 48.5, 2.9, 48.6, 48.65, 4.6, col_brass, false);

    // Safety Deposit Lockers lining Vault Walls
    b.add_box(56.5, 51.0, 0.0, 58.0, 58.0, 8.0, col_steel_bright);
    b.add_box(41.0, 56.5, 0.0, 56.5, 58.0, 8.0, col_steel_bright);
    // Locker face rows detail
    for lz in [1.5, 3.0, 4.5, 6.0] {
        b.add_box_ex(41.5, 56.4, lz, 56.0, 56.5, lz + 0.1, [0.25, 0.27, 0.30], false);
    }

    // Cash & Gold Bullion Pallets (Cover inside Vault)
    b.add_box(44.0, 53.0, 0.0, 47.0, 56.0, 1.8, col_gold); // Gold bullion pallets
    // Stepped gold pyramid effect
    b.add_box_ex(44.3, 53.3, 1.8, 46.7, 55.7, 2.3, col_gold, false);

    // Wire-Mesh Money Carts with Stacks of Green Currency
    b.add_box(51.0, 52.0, 0.0, 53.5, 54.5, 1.4, col_steel);
    b.add_box_ex(51.2, 52.2, 0.3, 53.3, 54.3, 1.35, col_money, false); // Cash bundles

    // HVAC Unit & Overhead Air Duct (for Jump Shooting into Vault)
    b.add_box(50.0, 44.0, 0.0, 53.0, 46.0, 1.8, [0.25, 0.32, 0.28]);
    b.add_box(49.0, 44.0, 3.4, 53.0, 48.0, 4.2, col_steel_bright);

    // Tactical Bomb Site A Floor Stencil
    b.add_floor(44.0, 50.5, 48.0, 53.5, 0.015, [0.85, 0.20, 0.15], false);

    // 9. Staff Offices / Breakroom (Defender territory)
    // Bank Manager's Executive Desk
    b.add_box(14.0, 51.0, 0.0, 18.0, 54.0, 1.2, col_mahogany);
    b.add_box_ex(15.2, 52.0, 1.2, 16.8, 53.0, 1.6, col_monitor, false); // Manager's PC
    b.add_box(13.0, 52.0, 0.0, 14.0, 53.5, 1.4, col_leather); // Executive chair
    // Breakroom table & water cooler
    b.add_box(26.0, 51.0, 0.0, 30.0, 54.0, 1.1, [0.45, 0.40, 0.35]);
    b.add_cylinder(31.0, 53.0, 0.0, 1.0, 0.3, 8, [0.90, 0.90, 0.90], true); // Water cooler base
    b.add_cylinder(31.0, 53.0, 1.0, 1.7, 0.26, 8, col_atm_screen, false); // Blue water bottle

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

/// Create the competitive tactical "Assault" (`hd_assault`) 3D arena.
///
/// Authentic CS:GO cs_assault industrial warehouse recreation:
/// 1. CT Spawn & Approach: Highway overpass (z = 8..9), street with yellow lines, SWAT van, train tracks & boxcar, containers.
/// 2. Warehouse Structure: Corrugated steel hangar, front & rear garage doors, rear alley with climbable ladder.
/// 3. The Vents: Enclosed rooftop crawlable ductwork with dual drops into catwalk and hostage office.
/// 4. Main Warehouse Interior: High trusses, elevated catwalks (z = 4.2) with ground clearance underneath, semi-truck & trailer, forklift.
/// 5. Back Office / Hostage Room: 2-story office with panoramic glass observation windows overlooking the floor, CCTV monitors, desks.
struct ScaledFacilityBuilder<'a> {
    inner: &'a mut FacilityBuilder,
    scale: f32,
}

impl<'a> ScaledFacilityBuilder<'a> {
    fn add_quad(
        &mut self,
        p0: [f32; 3],
        p1: [f32; 3],
        p2: [f32; 3],
        p3: [f32; 3],
        color: [f32; 3],
        is_collider: bool,
    ) {
        let s = self.scale;
        self.inner.add_quad(
            [p0[0] * s, p0[1] * s, p0[2] * s],
            [p1[0] * s, p1[1] * s, p1[2] * s],
            [p2[0] * s, p2[1] * s, p2[2] * s],
            [p3[0] * s, p3[1] * s, p3[2] * s],
            color,
            is_collider,
        );
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
        let s = self.scale;
        self.inner.add_floor(
            min_x * s,
            min_y * s,
            max_x * s,
            max_y * s,
            z * s,
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
        let s = self.scale;
        self.inner.add_box(
            min_x * s,
            min_y * s,
            min_z * s,
            max_x * s,
            max_y * s,
            max_z * s,
            color,
        );
    }

    fn add_box_ex(
        &mut self,
        min_x: f32,
        min_y: f32,
        min_z: f32,
        max_x: f32,
        max_y: f32,
        max_z: f32,
        color: [f32; 3],
        is_collider: bool,
    ) {
        let s = self.scale;
        self.inner.add_box_ex(
            min_x * s,
            min_y * s,
            min_z * s,
            max_x * s,
            max_y * s,
            max_z * s,
            color,
            is_collider,
        );
    }

    fn add_cylinder(
        &mut self,
        cx: f32,
        cy: f32,
        min_z: f32,
        max_z: f32,
        radius: f32,
        segments: usize,
        color: [f32; 3],
        is_collider: bool,
    ) {
        let s = self.scale;
        self.inner.add_cylinder(
            cx * s,
            cy * s,
            min_z * s,
            max_z * s,
            radius * s,
            segments,
            color,
            is_collider,
        );
    }

    fn add_ramp(
        &mut self,
        min_x: f32,
        min_y: f32,
        min_z: f32,
        max_x: f32,
        max_y: f32,
        max_z: f32,
        color: [f32; 3],
    ) {
        let s = self.scale;
        self.inner.add_ramp(
            min_x * s,
            min_y * s,
            min_z * s,
            max_x * s,
            max_y * s,
            max_z * s,
            color,
        );
    }
}

pub fn create_procedural_assault_3d(info: MapInfo) -> World3D {
    let mut builder = FacilityBuilder::new();
    {
        let mut b = ScaledFacilityBuilder {
            inner: &mut builder,
            scale: 10.0,
        };

    // CS:GO Industrial Palette
    let col_asphalt = [0.20, 0.20, 0.22];
    let col_road_yellow = [0.88, 0.72, 0.15];
    let col_road_white = [0.88, 0.88, 0.88];
    let col_gravel = [0.38, 0.36, 0.34];
    let col_concrete = [0.55, 0.53, 0.50];
    let col_bridge_pier = [0.58, 0.57, 0.54];
    let col_bridge_deck = [0.45, 0.44, 0.42];
    let col_warehouse_wall = [0.38, 0.40, 0.44];
    let col_warehouse_roof = [0.32, 0.33, 0.35];
    let col_warehouse_floor = [0.46, 0.45, 0.43];
    let col_catwalk = [0.28, 0.30, 0.32];
    let col_railing_yellow = [0.85, 0.70, 0.12];
    let col_hazard_yellow = [0.90, 0.75, 0.10];
    let col_hazard_black = [0.12, 0.12, 0.14];
    let col_vent_sheet = [0.45, 0.48, 0.50];
    let col_train_red = [0.52, 0.20, 0.16];
    let col_train_steel = [0.24, 0.25, 0.27];
    let col_swat_navy = [0.12, 0.16, 0.24];
    let col_container_blue = [0.16, 0.32, 0.55];
    let col_container_red = [0.65, 0.22, 0.18];
    let col_container_green = [0.18, 0.40, 0.26];
    let col_crate = [0.60, 0.45, 0.30];
    let col_glass = [0.68, 0.82, 0.92];
    let col_office_floor = [0.36, 0.38, 0.42];
    let col_desk = [0.38, 0.32, 0.26];
    let col_monitor = [0.12, 0.14, 0.16];
    let col_cctv_screen = [0.22, 0.60, 0.38];
    let col_ladder = [0.75, 0.75, 0.80];
    let col_forklift = [0.85, 0.65, 0.10];

    // 1. Perimeter Walls (64x64 bounds, height = 14)
    b.add_quad([4.0, 4.0, 0.0], [60.0, 4.0, 0.0], [60.0, 4.0, 14.0], [4.0, 4.0, 14.0], col_concrete, true);
    b.add_quad([60.0, 60.0, 0.0], [4.0, 60.0, 0.0], [4.0, 60.0, 14.0], [60.0, 60.0, 14.0], col_concrete, true);
    b.add_quad([60.0, 4.0, 0.0], [60.0, 60.0, 0.0], [60.0, 60.0, 14.0], [60.0, 4.0, 14.0], [0.30, 0.32, 0.35], true);
    b.add_quad([4.0, 60.0, 0.0], [4.0, 4.0, 0.0], [4.0, 4.0, 14.0], [4.0, 60.0, 14.0], [0.30, 0.32, 0.35], true);

    // 2. Ground Floors (Street Asphalt vs Rail Yard vs Warehouse Sealed Concrete)
    // Street asphalt (y: 4..24, x: 4..42)
    b.add_floor(4.0, 4.0, 42.0, 24.0, 0.0, col_asphalt, true);
    // Double solid yellow highway lines along y = 12
    b.add_floor(4.0, 11.85, 42.0, 11.95, 0.01, col_road_yellow, false);
    b.add_floor(4.0, 12.05, 42.0, 12.15, 0.01, col_road_yellow, false);
    // White pedestrian crosswalk stripes
    for i in 0..6 {
        let sx = 26.0 + (i as f32) * 2.0;
        b.add_floor(sx, 16.0, sx + 1.2, 22.0, 0.01, col_road_white, false);
    }
    // Sidewalk curb (y: 18..20): Human-scale curb height 0.15m..0.2m (scale * 0.015..0.02)
    b.add_box(4.0, 18.0, 0.0, 42.0, 20.0, 0.015, col_concrete);
    b.add_box(4.0, 17.8, 0.0, 42.0, 18.0, 0.02, [0.42, 0.40, 0.38]);
    // Smooth pedestrian curb cut ramps along crosswalks
    b.add_ramp(25.0, 17.0, 0.0, 39.0, 18.5, 0.02, col_concrete);

    // Rail yard gravel bed (x: 42..60, y: 4..24)
    b.add_floor(42.0, 4.0, 60.0, 24.0, 0.0, col_gravel, true);
    // Steel train tracks running North-South
    for rx in [49.0, 51.0, 55.0, 57.0] {
        b.add_box_ex(rx - 0.08, 4.0, 0.0, rx + 0.08, 24.0, 0.15, col_train_steel, false);
    }
    // Wooden rail ties
    for ty in (5..24).step_by(2) {
        let ty_f = ty as f32;
        b.add_box_ex(48.5, ty_f - 0.2, 0.0, 57.5, ty_f + 0.2, 0.08, [0.32, 0.24, 0.16], false);
    }

    // Rear alley ground (x: 4..60, y: 54..60)
    b.add_floor(4.0, 54.0, 60.0, 60.0, 0.0, col_asphalt, true);
    // West alley ground (x: 4..10, y: 24..54)
    b.add_floor(4.0, 24.0, 10.0, 54.0, 0.0, col_concrete, true);
    // East rail spur ground (x: 54..60, y: 24..54)
    b.add_floor(54.0, 24.0, 60.0, 54.0, 0.0, col_gravel, true);

    // 3. Elevated Highway Overpass Bridge (Suspended above CT Spawn at z = 8.0..9.0)
    // Highway bridge concrete deck slab
    b.add_box(4.0, 6.0, 8.0, 60.0, 14.0, 9.0, col_bridge_deck);
    // Guardrails on highway overpass edges
    b.add_box(4.0, 5.8, 9.0, 60.0, 6.2, 10.0, col_concrete);
    b.add_box(4.0, 13.8, 9.0, 60.0, 14.2, 10.0, col_concrete);
    // 4 Massive Cylindrical Bridge Support Piers (cx: 12, 26, 40, 54, cy = 10, z: 0..8)
    for px in [12.0, 26.0, 40.0, 54.0] {
        b.add_cylinder(px, 10.0, 0.0, 8.0, 1.1, 12, col_bridge_pier, true);
    }
    // Overhead green highway road sign attached to bridge
    b.add_box_ex(22.0, 14.05, 6.8, 32.0, 14.25, 8.0, [0.12, 0.48, 0.25], false);

    // 4. CT Spawn Props: Tactical SWAT Van & Rail Yard Boxcar
    // SWAT Van (x: 18..22, y: 10..16)
    b.add_box(18.0, 10.0, 0.0, 22.0, 12.5, 1.5, col_swat_navy); // Hood
    b.add_box(18.0, 12.5, 0.0, 22.0, 16.0, 2.8, [0.10, 0.14, 0.22]); // Cab & Roof
    // Wheels & Bullbar
    for wy in [11.0, 14.5] {
        b.add_box_ex(17.6, wy, 0.0, 18.0, wy + 1.2, 0.75, [0.08, 0.08, 0.09], true);
        b.add_box_ex(22.0, wy, 0.0, 22.4, wy + 1.2, 0.75, [0.08, 0.08, 0.09], true);
    }
    b.add_box_ex(18.2, 12.4, 1.5, 21.8, 12.7, 2.4, col_glass, false); // Windshield
    b.add_box_ex(18.5, 13.5, 2.8, 20.0, 14.0, 3.05, [0.85, 0.15, 0.15], false); // Red siren
    b.add_box_ex(20.0, 13.5, 2.8, 21.5, 14.0, 3.05, [0.15, 0.25, 0.85], false); // Blue siren
    b.add_box_ex(17.8, 9.7, 0.3, 22.2, 10.0, 0.9, [0.70, 0.72, 0.75], true); // Bullbar

    // Freight Train Boxcar in Rail Yard (x: 48..56, y: 8..20, z: 0..3.6)
    b.add_box(48.0, 8.0, 0.0, 56.0, 20.0, 0.8, col_train_steel); // Steel chassis & bogies
    // Hollow Boxcar Interior: Floor at z = 0.8, Walls to z = 3.6, Roof at z = 3.6
    b.add_box(48.0, 8.0, 0.8, 48.4, 20.0, 3.6, col_train_red); // West wall
    b.add_box(55.6, 8.0, 0.8, 56.0, 20.0, 3.6, col_train_red); // East wall
    b.add_box(48.0, 7.8, 0.8, 56.0, 8.2, 3.6, col_train_red);  // South wall
    b.add_box(48.0, 19.8, 0.8, 56.0, 20.2, 3.6, col_train_red); // North wall
    b.add_box(48.0, 8.0, 3.5, 56.0, 20.0, 3.7, col_train_steel); // Boxcar roof
    // Open sliding door on West wall (middle open from y: 12.5..15.5)
    // Boxcar inner floor is walkable platform at z = 0.8!

    // Stacked Shipping Containers in Rail Yard
    b.add_box(44.0, 20.0, 0.0, 50.0, 23.5, 2.8, col_container_blue);
    b.add_box(50.5, 20.0, 0.0, 56.5, 23.5, 2.8, col_container_red);
    b.add_box(46.0, 20.0, 2.8, 52.0, 23.5, 5.6, col_container_green); // Upper stacked container

    // 5. The Main Warehouse Shell (x: 10..54, y: 24..54, z: 0..8.0)
    // Warehouse sealed concrete floor
    b.add_floor(10.0, 24.0, 54.0, 54.0, 0.0, col_warehouse_floor, true);

    // Exterior Walls:
    // South Wall (Front Facade with giant Garage Shutter Door):
    b.add_box(10.0, 23.5, 0.0, 26.0, 24.5, 8.0, col_warehouse_wall); // West of door
    b.add_box(38.0, 23.5, 0.0, 54.0, 24.5, 8.0, col_warehouse_wall); // East of door
    b.add_box(26.0, 23.5, 4.5, 38.0, 24.5, 8.0, col_warehouse_wall); // Above door
    // Front Rolling Shutter (raised with 2.8m opening into warehouse!)
    b.add_box(26.0, 23.8, 2.8, 38.0, 24.2, 4.5, col_hazard_yellow);
    b.add_box_ex(26.0, 23.7, 2.7, 38.0, 24.3, 2.85, col_hazard_black, false);

    // North Wall (Rear Facade with Rear Garage Door & Ladder):
    b.add_box(10.0, 53.5, 0.0, 38.0, 54.5, 8.0, col_warehouse_wall);
    b.add_box(48.0, 53.5, 0.0, 54.0, 54.5, 8.0, col_warehouse_wall);
    b.add_box(38.0, 53.5, 4.2, 48.0, 54.5, 8.0, col_warehouse_wall);
    // Rear Rolling Shutter (raised with 2.6m opening into back alley!)
    b.add_box(38.0, 53.8, 2.6, 48.0, 54.2, 4.2, col_train_steel);

    // West Wall (with side service door at y: 34..36):
    b.add_box(9.5, 24.0, 0.0, 10.5, 34.0, 8.0, col_warehouse_wall);
    b.add_box(9.5, 36.0, 0.0, 10.5, 54.0, 8.0, col_warehouse_wall);
    b.add_box(9.5, 34.0, 2.4, 10.5, 36.0, 8.0, col_warehouse_wall);

    // East Wall (Solid warehouse wall facing rail spur):
    b.add_box(53.5, 24.0, 0.0, 54.5, 54.0, 8.0, col_warehouse_wall);

    // Rear Exterior Ladder (Climb from rear alley onto warehouse roof!)
    // Rails & Rungs at x = 14.0, y = 54.2, z: 0.0..8.2
    b.add_cylinder(13.6, 54.2, 0.0, 8.4, 0.04, 6, col_ladder, true);
    b.add_cylinder(14.4, 54.2, 0.0, 8.4, 0.04, 6, col_ladder, true);
    for rz in 1..17 {
        let rz_f = (rz as f32) * 0.5;
        b.add_box(13.6, 54.15, rz_f - 0.03, 14.4, 54.25, rz_f + 0.03, col_ladder);
    }

    // 6. Rooftop (z = 8.0) & Skylights
    b.add_floor(10.0, 24.0, 54.0, 54.0, 8.0, col_warehouse_roof, true);
    // Rooftop parapet safety wall
    b.add_box(9.6, 23.6, 8.0, 54.4, 24.0, 8.9, col_concrete);
    b.add_box(9.6, 54.0, 8.0, 54.4, 54.4, 8.9, col_concrete);
    b.add_box(9.6, 24.0, 8.0, 10.0, 54.0, 8.9, col_concrete);
    b.add_box(54.0, 24.0, 8.0, 54.4, 54.0, 8.9, col_concrete);

    // Rooftop industrial HVAC chiller units
    b.add_box(20.0, 26.0, 8.0, 24.0, 30.0, 9.8, [0.35, 0.38, 0.42]);
    b.add_box(44.0, 44.0, 8.0, 48.0, 48.0, 9.8, [0.35, 0.38, 0.42]);
    // Large skylights peering down into hangar floor
    b.add_floor(28.0, 32.0, 36.0, 40.0, 8.02, col_glass, false);

    // 7. The Iconic Ventilation System ("The Vents" - Crouch/Crawlable 3D Ducts)
    // Vent Intake Housing on roof near top of exterior ladder (x: 14..17, y: 50..53)
    b.add_box(14.0, 50.0, 8.0, 17.0, 53.0, 9.4, col_vent_sheet);
    // Hollow open mouth of the vent (cut into south face at y = 50.0)
    // Duct internal floor: z = 8.0, internal ceiling: z = 9.4, width: 1.4m

    // Main Vent Trunk running South (x: 14.8..16.2, y: 36.0..50.0):
    // Floor, Ceiling, West and East Walls
    b.add_box(14.7, 36.0, 7.95, 16.3, 50.0, 8.05, col_vent_sheet); // Vent floor
    b.add_box(14.7, 36.0, 9.35, 16.3, 50.0, 9.45, col_vent_sheet); // Vent ceiling
    b.add_box(14.7, 36.0, 8.0, 14.8, 50.0, 9.4, col_vent_sheet);   // West wall
    b.add_box(16.2, 36.0, 8.0, 16.3, 50.0, 9.4, col_vent_sheet);   // East wall

    // Vent Branch 1: Turns East at y = 36.0 to drop onto Catwalk
    b.add_box(16.3, 35.3, 7.95, 24.0, 36.7, 8.05, col_vent_sheet);
    b.add_box(16.3, 35.3, 9.35, 24.0, 36.7, 9.45, col_vent_sheet);
    b.add_box(16.3, 35.3, 8.0, 24.0, 35.4, 9.4, col_vent_sheet);
    b.add_box(16.3, 36.6, 8.0, 24.0, 36.7, 9.4, col_vent_sheet);
    // Vertical drop shaft hole at x: 23..25, y: 35..37 (drops straight down onto catwalk at z = 4.2!)
    b.add_box(22.8, 35.3, 4.2, 23.0, 36.7, 8.0, col_vent_sheet);
    b.add_box(24.8, 35.3, 4.2, 25.0, 36.7, 8.0, col_vent_sheet);

    // Vent Branch 2: Turns East at y = 48.0 into Hostage Office ceiling
    b.add_box(16.3, 47.3, 7.95, 20.0, 48.7, 8.05, col_vent_sheet);
    b.add_box(16.3, 47.3, 9.35, 20.0, 48.7, 9.45, col_vent_sheet);
    b.add_box(16.3, 47.3, 8.0, 20.0, 47.4, 9.4, col_vent_sheet);
    b.add_box(16.3, 48.6, 8.0, 20.0, 48.7, 9.4, col_vent_sheet);
    // Vertical drop shaft hole at x: 19..21, y: 47..49 (drops straight down into Hostage Room at z = 4.2!)
    b.add_box(18.8, 47.3, 4.2, 19.0, 48.7, 8.0, col_vent_sheet);
    b.add_box(20.8, 47.3, 4.2, 21.0, 48.7, 8.0, col_vent_sheet);

    // 8. Elevated Industrial Catwalk System (z = 4.2)
    // West Catwalk: x: 11..15, y: 26..46, z = 4.2
    b.add_floor(11.0, 26.0, 15.0, 46.0, 4.2, col_catwalk, true);
    // Catwalk thickness slab
    b.add_box(11.0, 26.0, 4.15, 15.0, 46.0, 4.2, col_catwalk);
    // Catwalk Safety Handrail along open East edge (x = 15.0, y: 26..46, z: 4.2..5.3)
    b.add_box(14.85, 26.0, 4.2, 15.05, 46.0, 5.3, col_railing_yellow);

    // South Catwalk: x: 15..36, y: 25..28, z = 4.2
    b.add_floor(15.0, 25.0, 36.0, 28.0, 4.2, col_catwalk, true);
    b.add_box(15.0, 25.0, 4.15, 36.0, 28.0, 4.2, col_catwalk);
    // Handrail along open North edge (y = 28.0, x: 15..36, z: 4.2..5.3)
    b.add_box(15.0, 27.85, 4.2, 36.0, 28.05, 5.3, col_railing_yellow);

    // Industrial Steel Stairs connecting ground floor to Catwalk (x: 11..14, y: 26..32, z: 0..4.2)
    b.add_ramp(11.0, 26.0, 0.0, 14.0, 32.0, 4.2, col_catwalk);

    // Catwalk Support Pillars down to warehouse floor (radius = 0.15)
    for (cx, cy) in [(14.8, 34.0), (14.8, 42.0), (24.0, 27.8), (34.0, 27.8)] {
        b.add_cylinder(cx, cy, 0.0, 4.2, 0.15, 8, col_train_steel, true);
    }

    // 9. Warehouse Floor Props: 18-Wheeler Semi-Truck & Forklift
    // Semi-Truck Cab (x: 30..34, y: 28..32, z: 0..2.8)
    b.add_box(30.0, 28.0, 0.0, 34.0, 32.0, 2.8, [0.75, 0.18, 0.18]);
    b.add_box_ex(30.2, 27.8, 1.4, 33.8, 28.1, 2.4, col_glass, false); // Front windshield
    // Flatbed Trailer (x: 29..35, y: 32..42, z: 0..1.5)
    b.add_box(29.0, 32.0, 0.0, 35.0, 42.0, 1.5, col_train_steel);
    // Cargo loaded on flatbed trailer: Stacked tactical wooden crates (z: 1.5..3.0)
    b.add_box(29.5, 33.0, 1.5, 34.5, 41.0, 3.0, col_crate);

    // Industrial Forklift (x: 42..45, y: 32..35, z: 0..2.2)
    b.add_box(42.0, 32.0, 0.0, 45.0, 35.0, 1.4, col_forklift);
    b.add_box_ex(42.2, 32.2, 1.4, 44.8, 34.8, 2.2, [0.15, 0.15, 0.15], false); // Roll-cage
    b.add_box_ex(41.4, 31.8, 0.0, 41.8, 34.8, 1.8, col_train_steel, true); // Front mast & forks

    // Stacked Shipping Containers inside warehouse along East wall (x: 46..52, y: 38..48)
    b.add_box(46.0, 38.0, 0.0, 52.0, 48.0, 2.8, col_container_blue);

    // 10. The 2-Story Back Office / Hostage Room (x: 11..34, y: 46..54)
    // Lower Floor Workshop / Storage (z: 0..4.2)
    // Upper Floor Hostage Office (z: 4.2..7.8)
    b.add_floor(11.0, 46.0, 34.0, 54.0, 4.2, col_office_floor, true); // Office Floor slab

    // Dividing Wall between Office and Warehouse Hangar (y = 46.0, x: 11..34):
    b.add_box(11.0, 45.8, 0.0, 34.0, 46.2, 4.2, col_concrete); // Ground wall
    b.add_box(11.0, 45.8, 4.2, 14.0, 46.2, 7.8, col_concrete); // Upper West wall
    b.add_box(28.0, 45.8, 4.2, 34.0, 46.2, 7.8, col_concrete); // Upper East wall
    b.add_box(14.0, 45.8, 7.2, 28.0, 46.2, 7.8, col_concrete); // Header above window
    // Panoramic Observation Glass Window (x: 14..28, y = 46.0, z: 4.2..7.2)
    b.add_box(14.0, 45.9, 4.2, 28.0, 46.1, 7.2, col_glass);

    // Interior Staircase inside workshop leading up to Hostage Office (x: 30..34, y: 46..52, z: 0..4.2)
    b.add_ramp(30.0, 46.0, 0.0, 33.5, 52.0, 4.2, col_concrete);

    // Hostage Office Furniture & Props
    // Security Monitoring Desk with CCTV Screens
    b.add_box(14.0, 51.5, 4.2, 18.0, 53.0, 5.2, col_desk);
    b.add_box_ex(14.5, 52.2, 5.2, 17.5, 52.8, 5.9, col_monitor, false);
    b.add_box_ex(14.6, 52.15, 5.25, 17.4, 52.2, 5.85, col_cctv_screen, false); // CCTV feed

    // Hostage Chairs (Site A)
    for (hx, hy) in [(22.0, 52.0), (25.0, 52.0), (22.0, 49.0), (25.0, 49.0)] {
        b.add_box(hx - 0.35, hy - 0.35, 4.2, hx + 0.35, hy + 0.35, 4.9, [0.45, 0.25, 0.18]);
    }

    // 11. Tactical Bomb Site Floor Decals
    // Site A: Hostage Office floor
    b.add_floor(21.0, 48.0, 26.0, 53.0, 4.22, [0.88, 0.20, 0.15], false);
    // Site B: Warehouse floor next to semi-truck
    b.add_floor(30.0, 36.0, 35.0, 41.0, 0.02, [0.88, 0.20, 0.15], false);
    }

    let s = 10.0;
    let spawns = vec![
        SpawnPoint { x: 16.0 * s, y: 8.0 * s, z: 0.0, yaw: 0.0, team: 0 },
        SpawnPoint { x: 24.0 * s, y: 8.0 * s, z: 0.0, yaw: 0.0, team: 0 },
        SpawnPoint { x: 32.0 * s, y: 8.0 * s, z: 0.0, yaw: 0.0, team: 0 },
        SpawnPoint { x: 40.0 * s, y: 8.0 * s, z: 0.0, yaw: 0.0, team: 0 },
        SpawnPoint { x: 16.0 * s, y: 50.0 * s, z: 4.2 * s, yaw: 180.0, team: 1 },
        SpawnPoint { x: 22.0 * s, y: 50.0 * s, z: 4.2 * s, yaw: 180.0, team: 1 },
        SpawnPoint { x: 28.0 * s, y: 50.0 * s, z: 4.2 * s, yaw: 180.0, team: 1 },
        SpawnPoint { x: 38.0 * s, y: 48.0 * s, z: 0.0, yaw: 180.0, team: 1 },
    ];

    let items = vec![
        ItemRow { id: 1, kind: "health".into(), x: 18.0 * s, y: 12.0 * s, z: 0.0 },
        ItemRow { id: 2, kind: "health".into(), x: 52.0 * s, y: 14.0 * s, z: 0.0 },
        ItemRow { id: 3, kind: "health".into(), x: 16.0 * s, y: 52.0 * s, z: 4.2 * s },
        ItemRow { id: 4, kind: "armour".into(), x: 26.0 * s, y: 36.0 * s, z: 0.0 },
        ItemRow { id: 5, kind: "armour".into(), x: 32.0 * s, y: 50.0 * s, z: 4.2 * s },
        ItemRow { id: 6, kind: "ammo_assault".into(), x: 22.0 * s, y: 10.0 * s, z: 0.0 },
        ItemRow { id: 7, kind: "ammo_assault".into(), x: 44.0 * s, y: 32.0 * s, z: 0.0 },
        ItemRow { id: 8, kind: "ammo_sniper".into(), x: 20.0 * s, y: 14.0 * s, z: 2.8 * s },
        ItemRow { id: 9, kind: "clips".into(), x: 32.0 * s, y: 20.0 * s, z: 0.0 },
        ItemRow { id: 10, kind: "grenade".into(), x: 14.0 * s, y: 38.0 * s, z: 4.2 * s },
    ];

    let bounds = WorldBounds {
        min: [4.0 * s, 4.0 * s, 0.0],
        max: [60.0 * s, 60.0 * s, 14.0 * s],
        center: [32.0 * s, 32.0 * s, 5.0 * s],
        extent: 40.0 * s,
    };

    let triangles = builder.render_positions.len() / 9;

    World3D {
        info,
        bounds,
        render_positions: builder.render_positions,
        render_normals: builder.render_normals,
        render_colors: builder.render_colors,
        render_uvs: builder.render_uvs,
        triangles,
        col_vertices: builder.col_vertices,
        col_indices: builder.col_indices,
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
        "hd_assault" => create_procedural_assault_3d(info),
        _ => create_procedural_facility_3d(info),
    }
}
