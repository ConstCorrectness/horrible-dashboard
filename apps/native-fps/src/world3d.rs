//! True 3D World Representation and glTF/Procedural Level Loader.
//!
//! Provides arbitrary 3D polygonal level geometry (multi-tier arenas, catwalks,
//! ramps, stairs, and cover) for the native client, matching `world3d.ts`.

use crate::api::{ItemRow, MapInfo};
use crate::geometry::MeshData;
use crate::textures3d::MaterialKind;
use glam::{Mat4, Quat, Vec3};
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

/// Unnormalised normal of a triangle; only its dominant axis is used.
pub fn face_normal(a: [f32; 3], b: [f32; 3], c: [f32; 3]) -> [f32; 3] {
    let u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    let v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
    [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
}

/// Planar UV for a GLB vertex in *render* space (x east, y up, z north),
/// projected along the face normal's dominant axis with ties broken like the
/// shader's `detail_uv`. A port of `glb-surfaces.ts` `planarUv`, pinned by
/// `glb-surface-vectors.json`.
pub fn glb_planar_uv(p: [f32; 3], n: [f32; 3], scale: f32) -> [f32; 2] {
    let (ax, ay, az) = (n[0].abs(), n[1].abs(), n[2].abs());
    if ay >= ax && ay >= az {
        [p[0] / scale, p[2] / scale]
    } else if ax >= az {
        [p[2] / scale, p[1] / scale]
    } else {
        [p[0] / scale, p[1] / scale]
    }
}

pub fn compute_planar_uv(p: [f32; 3], norm: [f32; 3], scale: f32) -> [f32; 2] {
    let s = if scale <= 0.0 { 4.0 } else { scale };
    if norm[1].abs() > 0.6 {
        [p[0] / s, p[1] / s]
    } else if norm[0].abs() > 0.6 {
        [p[1] / s, p[2] / s]
    } else {
        [p[0] / s, p[2] / s]
    }
}

#[derive(Debug, Clone)]
pub struct BreakableWindow {
    pub id: String,
    pub name: String,
    pub center: [f32; 3],
    pub normal: [f32; 3],
    pub shattered: bool,
    pub col_vertices: Vec<Point<Real>>,
    pub col_indices: Vec<[u32; 3]>,
}

impl BreakableWindow {
    pub fn shatter(&mut self) {
        self.shattered = true;
    }
}

pub fn is_breakable_window_node(name: &str) -> bool {
    let lower = name.to_lowercase();
    (lower.contains("window") && lower.contains("glass"))
        || (lower.contains("breakable") && lower.contains("glass"))
        || (lower.contains("curtain") && lower.contains("glass"))
        || lower.contains("window_glass")
}

pub struct World3D {
    pub info: MapInfo,
    pub bounds: WorldBounds,
    /// Vertex positions in render space (x, z as height, y).
    pub render_positions: Vec<f32>,
    pub render_normals: Vec<f32>,
    pub render_colors: Vec<f32>,
    pub render_uvs: Vec<f32>,
    pub render_materials: Vec<f32>,
    pub triangles: usize,
    /// Collision geometry in game space (x, y, z as height).
    pub col_vertices: Vec<Point<Real>>,
    pub col_indices: Vec<[u32; 3]>,
    pub spawns: Vec<SpawnPoint>,
    pub items: Vec<ItemRow>,
    pub waterlevel: f32,
    pub windows: std::collections::HashMap<String, BreakableWindow>,
}

impl World3D {
    pub fn to_mesh_data(&self) -> MeshData {
        MeshData {
            positions: self.render_positions.clone(),
            normals: self.render_normals.clone(),
            colors: self.render_colors.clone(),
            uvs: self.render_uvs.clone(),
            materials: self.render_materials.clone(),
            triangles: self.triangles,
        }
    }
}

struct FacilityBuilder {
    render_positions: Vec<f32>,
    render_normals: Vec<f32>,
    render_colors: Vec<f32>,
    render_uvs: Vec<f32>,
    render_materials: Vec<f32>,
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
            render_materials: Vec::new(),
            col_vertices: Vec::new(),
            col_indices: Vec::new(),
        }
    }

    fn add_triangle_mat(
        &mut self,
        p0: [f32; 3],
        p1: [f32; 3],
        p2: [f32; 3],
        material: MaterialKind,
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
            self.render_materials.push(material as u8 as f32);
        }

        let uv0 = compute_planar_uv(p0, norm, material.tile_scale());
        let uv1 = compute_planar_uv(p1, norm, material.tile_scale());
        let uv2 = compute_planar_uv(p2, norm, material.tile_scale());
        self.render_uvs.extend_from_slice(&uv0);
        self.render_uvs.extend_from_slice(&uv1);
        self.render_uvs.extend_from_slice(&uv2);

        if is_collider {
            let base_idx = self.col_vertices.len() as u32;
            self.col_vertices.push(point![p0[0], p0[1], p0[2]]);
            self.col_vertices.push(point![p1[0], p1[1], p1[2]]);
            self.col_vertices.push(point![p2[0], p2[1], p2[2]]);
            self.col_indices.push([base_idx, base_idx + 1, base_idx + 2]);
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
        self.add_triangle_mat(p0, p1, p2, MaterialKind::None, color, is_collider);
    }

    fn add_quad_mat(
        &mut self,
        p0: [f32; 3],
        p1: [f32; 3],
        p2: [f32; 3],
        p3: [f32; 3],
        material: MaterialKind,
        color: [f32; 3],
        is_collider: bool,
    ) {
        self.add_triangle_mat(p0, p1, p2, material, color, is_collider);
        self.add_triangle_mat(p0, p2, p3, material, color, is_collider);
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
        self.add_quad_mat(p0, p1, p2, p3, MaterialKind::None, color, is_collider);
    }

    fn add_floor_mat(
        &mut self,
        min_x: f32,
        min_y: f32,
        max_x: f32,
        max_y: f32,
        z: f32,
        material: MaterialKind,
        color: [f32; 3],
        is_collider: bool,
    ) {
        // Top face pointing strictly UP (+y in render space, norm = [0, 1, 0])
        self.add_quad_mat(
            [min_x, max_y, z],
            [max_x, max_y, z],
            [max_x, min_y, z],
            [min_x, min_y, z],
            material,
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
        self.add_floor_mat(min_x, min_y, max_x, max_y, z, MaterialKind::None, color, is_collider);
    }

    fn add_box_ex_mat(
        &mut self,
        min_x: f32,
        min_y: f32,
        min_z: f32,
        max_x: f32,
        max_y: f32,
        max_z: f32,
        material: MaterialKind,
        color: [f32; 3],
        is_collider: bool,
    ) {
        // Floor
        self.add_quad_mat(
            [min_x, min_y, min_z],
            [max_x, min_y, min_z],
            [max_x, max_y, min_z],
            [min_x, max_y, min_z],
            material,
            color,
            is_collider,
        );
        // Ceiling / Top
        self.add_quad_mat(
            [min_x, max_y, max_z],
            [max_x, max_y, max_z],
            [max_x, min_y, max_z],
            [min_x, min_y, max_z],
            material,
            color,
            is_collider,
        );
        // North
        self.add_quad_mat(
            [max_x, max_y, min_z],
            [max_x, max_y, max_z],
            [min_x, max_y, max_z],
            [min_x, max_y, min_z],
            material,
            color,
            is_collider,
        );
        // South
        self.add_quad_mat(
            [min_x, min_y, min_z],
            [min_x, min_y, max_z],
            [max_x, min_y, max_z],
            [max_x, min_y, min_z],
            material,
            color,
            is_collider,
        );
        // East
        self.add_quad_mat(
            [max_x, min_y, min_z],
            [max_x, min_y, max_z],
            [max_x, max_y, max_z],
            [max_x, max_y, min_z],
            material,
            color,
            is_collider,
        );
        // West
        self.add_quad_mat(
            [min_x, max_y, min_z],
            [min_x, max_y, max_z],
            [min_x, min_y, max_z],
            [min_x, min_y, min_z],
            material,
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
        self.add_box_ex_mat(min_x, min_y, min_z, max_x, max_y, max_z, MaterialKind::None, color, is_collider);
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
        render_materials: b.render_materials,
        triangles,
        col_vertices: b.col_vertices,
        col_indices: b.col_indices,
        spawns,
        items,
        waterlevel: -3.5,
        windows: std::collections::HashMap::new(),
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
        render_materials: b.render_materials,
        triangles,
        col_vertices: b.col_vertices,
        col_indices: b.col_indices,
        spawns,
        items,
        waterlevel: -5.0,
        windows: std::collections::HashMap::new(),
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
        ItemRow { id: 6, kind: "ammo_assault".into(), x: 20.0, y: 11.0, z: 0.0 },
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
        render_materials: b.render_materials,
        triangles,
        col_vertices: b.col_vertices,
        col_indices: b.col_indices,
        spawns,
        items,
        waterlevel: -100.0,
        windows: std::collections::HashMap::new(),
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
pub fn create_procedural_assault_3d(info: MapInfo) -> World3D {
    let mut b = FacilityBuilder::new();

    // CS:GO Industrial Color Palette
    let col_asphalt = [0.15, 0.15, 0.16];
    let col_road_yellow = [0.88, 0.72, 0.15];
    let col_road_white = [0.88, 0.88, 0.88];
    let col_gravel = [0.38, 0.36, 0.34];
    let col_concrete = [0.55, 0.53, 0.50];
    let col_concrete_dark = [0.36, 0.35, 0.34];
    let _col_concrete_light = [0.65, 0.63, 0.60];
    let col_bridge_pier = [0.58, 0.57, 0.54];
    let col_bridge_deck = [0.45, 0.44, 0.42];
    let col_warehouse_wall = [0.38, 0.40, 0.44];
    let _col_warehouse_dark = [0.28, 0.30, 0.33];
    let col_warehouse_trim = [0.22, 0.23, 0.25];
    let col_warehouse_roof = [0.28, 0.29, 0.31];
    let col_warehouse_floor = [0.42, 0.42, 0.40];
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
    let col_glass_dark = [0.25, 0.35, 0.45];
    let col_office_floor = [0.36, 0.38, 0.42];
    let col_desk = [0.38, 0.32, 0.26];
    let col_monitor = [0.12, 0.14, 0.16];
    let col_cctv_screen = [0.22, 0.60, 0.38];
    let col_forklift = [0.85, 0.65, 0.10];
    let col_rubber = [0.08, 0.08, 0.09];
    let col_chrome = [0.82, 0.85, 0.88];
    let col_lamp_glow = [0.98, 0.92, 0.75];
    let col_dumpster_green = [0.15, 0.35, 0.20];

    // 1. Perimeter Boundary Walls (56x56 bounds: 4.0..60.0, height = 14.0m)
    b.add_quad([4.0, 4.0, 0.0], [60.0, 4.0, 0.0], [60.0, 4.0, 14.0], [4.0, 4.0, 14.0], col_concrete, true);
    b.add_quad([60.0, 60.0, 0.0], [4.0, 60.0, 0.0], [4.0, 60.0, 14.0], [60.0, 60.0, 14.0], col_concrete, true);
    b.add_quad([60.0, 4.0, 0.0], [60.0, 60.0, 0.0], [60.0, 60.0, 14.0], [60.0, 4.0, 14.0], [0.30, 0.32, 0.35], true);
    b.add_quad([4.0, 60.0, 0.0], [4.0, 4.0, 0.0], [4.0, 4.0, 14.0], [4.0, 60.0, 14.0], [0.30, 0.32, 0.35], true);

    // 2. Outside Street & Sidewalks (Human-scale 0.18m curb)
    // Road asphalt (y: 8.5..21.0, x: 4.0..60.0)
    b.add_floor(4.0, 8.5, 60.0, 21.0, 0.0, col_asphalt, true);
    // Double solid yellow center lines along y = 14.75
    b.add_floor(4.0, 14.65, 46.0, 14.75, 0.01, col_road_yellow, false);
    b.add_floor(4.0, 14.85, 46.0, 14.95, 0.01, col_road_yellow, false);
    // White pedestrian crosswalk stripes near CT spawn (x: 14.0..18.0)
    for i in 0..5 {
        let sx = 14.0 + (i as f32) * 0.9;
        b.add_floor(sx, 8.7, sx + 0.55, 20.8, 0.01, col_road_white, false);
    }
    // Cast-iron storm drain catch basins along the curb gutter
    for dx in [13.0, 25.0, 37.0] {
        b.add_box_ex(dx, 8.6, 0.005, dx + 1.2, 9.2, 0.015, [0.10, 0.10, 0.11], false);
        for bar in 0..4 {
            let bx = dx + 0.15 + (bar as f32) * 0.25;
            b.add_box_ex(bx, 8.65, 0.015, bx + 0.1, 9.15, 0.02, [0.25, 0.26, 0.28], false);
        }
    }
    // Cast-iron circular manhole cover in street
    b.add_cylinder(26.0, 16.0, 0.0, 0.015, 0.45, 12, [0.18, 0.19, 0.20], false);
    b.add_cylinder(26.0, 16.0, 0.015, 0.02, 0.35, 10, [0.24, 0.25, 0.26], false);

    // South sidewalk (CT spawn curb at z = 0.18m)
    b.add_box(4.0, 4.0, 0.0, 60.0, 8.5, 0.18, col_concrete);
    b.add_box(4.0, 8.35, 0.0, 60.0, 8.5, 0.19, col_concrete_dark); // Curb edge
    // North sidewalk (Warehouse apron curb at z = 0.18m)
    b.add_box(4.0, 21.0, 0.0, 46.0, 22.0, 0.18, col_concrete);
    b.add_box(4.0, 21.0, 0.0, 46.0, 21.15, 0.19, col_concrete_dark);
    // Smooth pedestrian curb cut ramps for seamless movement
    b.add_ramp(13.0, 8.0, 0.18, 19.0, 8.7, 0.0, col_concrete);
    b.add_ramp(23.0, 8.0, 0.18, 29.0, 8.7, 0.0, col_concrete);
    b.add_ramp(13.0, 20.8, 0.0, 19.0, 21.5, 0.18, col_concrete);

    // Street Furniture on Sidewalk
    // Commercial 6-yard steel dumpster in forest green
    b.add_box(10.0, 5.0, 0.18, 12.5, 7.2, 1.4, col_dumpster_green);
    b.add_box(9.95, 4.95, 1.4, 12.55, 7.25, 1.6, [0.10, 0.11, 0.12]); // Hinged plastic lids
    b.add_box(9.85, 5.8, 0.5, 10.0, 6.4, 0.8, [0.10, 0.11, 0.12]); // Fork pockets
    b.add_box(12.5, 5.8, 0.5, 12.65, 6.4, 0.8, [0.10, 0.11, 0.12]);
    // Stack of wooden shipping pallets with shrink-wrapped freight
    b.add_box(29.5, 5.2, 0.18, 31.8, 7.2, 0.5, col_crate);
    b.add_box(29.6, 5.3, 0.5, 31.7, 7.1, 1.4, [0.82, 0.80, 0.75]);
    // Red municipal fire hydrant
    b.add_cylinder(13.0, 8.0, 0.18, 0.85, 0.18, 8, [0.85, 0.12, 0.12], true);
    b.add_cylinder(13.0, 8.0, 0.85, 0.98, 0.12, 8, [0.85, 0.12, 0.12], false);
    // Streetlight pole with curved mast arm and luminaire
    b.add_cylinder(12.0, 7.8, 0.18, 6.5, 0.10, 8, col_train_steel, true);
    b.add_box_ex(12.0, 7.8, 6.3, 14.5, 8.0, 6.5, col_train_steel, false);
    b.add_box_ex(14.0, 7.7, 6.1, 14.6, 8.1, 6.3, col_lamp_glow, false);

    // 3. Highway Overpass (High clearance: Deck at z = 7.5m, out of CT line of sight)
    b.add_box(4.0, 4.0, 7.2, 60.0, 9.5, 7.5, col_bridge_deck);
    b.add_floor(4.0, 4.2, 60.0, 9.3, 7.51, col_asphalt, true);
    // Concrete safety guardrail along overpass edge
    b.add_box(4.0, 9.3, 7.5, 60.0, 9.6, 8.5, col_concrete);
    b.add_box(4.0, 4.0, 7.5, 60.0, 4.3, 8.5, col_concrete);
    // Support piers tucked against south perimeter wall
    for px in [10.0, 24.0, 38.0, 52.0] {
        b.add_cylinder(px, 5.0, 0.0, 7.2, 0.8, 12, col_bridge_pier, true);
        b.add_box(px - 1.0, 4.0, 6.8, px + 1.0, 9.5, 7.2, col_bridge_pier);
    }
    // Overhead green highway sign
    b.add_box(21.0, 9.6, 8.2, 27.0, 9.75, 9.4, [0.05, 0.42, 0.20]);
    b.add_box_ex(21.4, 9.76, 8.7, 26.6, 9.77, 8.85, col_road_white, false); // Sign text bar

    // 4. Ultra-Detailed SWAT Tactical Van (parked on street at x: 19.2..24.8, y: 12.8..15.2)
    // 4 Detailed Cylindrical Wheels with Rubber Tires & Chrome Rims
    let wx_front = 23.6;
    let wx_rear = 20.2;
    for &wx in &[wx_front, wx_rear] {
        // Left wheels (y ~ 12.75)
        b.add_cylinder(wx, 12.75, 0.0, 0.76, 0.38, 10, col_rubber, true);
        b.add_cylinder(wx, 12.65, 0.15, 0.61, 0.23, 8, col_chrome, false);
        b.add_cylinder(wx, 12.63, 0.34, 0.42, 0.08, 6, [0.15, 0.15, 0.16], false); // Lug hub
        // Right wheels (y ~ 15.25)
        b.add_cylinder(wx, 15.25, 0.0, 0.76, 0.38, 10, col_rubber, true);
        b.add_cylinder(wx, 15.35, 0.15, 0.61, 0.23, 8, col_chrome, false);
        b.add_cylinder(wx, 15.37, 0.34, 0.42, 0.08, 6, [0.15, 0.15, 0.16], false);
    }
    // Wheel well fender arches
    b.add_box(19.8, 12.7, 0.65, 20.6, 12.9, 0.85, col_warehouse_trim);
    b.add_box(23.2, 12.7, 0.65, 24.0, 12.9, 0.85, col_warehouse_trim);
    b.add_box(19.8, 15.1, 0.65, 20.6, 15.3, 0.85, col_warehouse_trim);
    b.add_box(23.2, 15.1, 0.65, 24.0, 15.3, 0.85, col_warehouse_trim);

    // Black steel undercarriage chassis & fuel tanks
    b.add_box(19.4, 13.0, 0.2, 24.6, 15.0, 0.5, [0.08, 0.08, 0.10]);
    // Front heavy tactical bullbar push bumper
    b.add_box(24.8, 12.8, 0.2, 25.1, 15.2, 0.55, [0.12, 0.13, 0.14]);
    b.add_box(24.85, 13.0, 0.55, 25.15, 13.2, 1.15, [0.12, 0.13, 0.14]); // Upright push bars
    b.add_box(24.85, 14.8, 0.55, 25.15, 15.0, 1.15, [0.12, 0.13, 0.14]);
    b.add_box(24.88, 13.0, 1.05, 25.12, 15.0, 1.15, [0.12, 0.13, 0.14]); // Crossbar
    // Front hood with beveled nose
    b.add_box(23.2, 13.0, 0.5, 24.8, 15.0, 1.3, col_swat_navy);
    b.add_box_ex(24.4, 13.4, 1.31, 24.7, 14.6, 1.33, [0.08, 0.08, 0.09], false); // Hood air vent
    // Headlights and amber turn indicators
    b.add_box(24.81, 13.1, 0.75, 24.85, 13.5, 0.95, [0.95, 0.95, 0.75]);
    b.add_box(24.81, 13.52, 0.78, 24.84, 13.7, 0.92, [0.95, 0.65, 0.10]);
    b.add_box(24.81, 14.5, 0.75, 24.85, 14.9, 0.95, [0.95, 0.95, 0.75]);
    b.add_box(24.81, 14.3, 0.78, 24.84, 14.48, 0.92, [0.95, 0.65, 0.10]);
    // Slanted windshield with black rubber gasket frame
    b.add_box_ex(22.7, 13.1, 1.3, 23.2, 14.9, 1.9, col_glass, false);
    b.add_box_ex(22.68, 13.08, 1.28, 23.22, 14.92, 1.32, [0.08, 0.08, 0.09], false); // Gasket bottom
    // Protruding side mirrors
    b.add_box_ex(23.0, 12.5, 1.5, 23.2, 12.9, 1.7, [0.08, 0.08, 0.09], false);
    b.add_box_ex(23.0, 15.1, 1.5, 23.2, 15.5, 1.7, [0.08, 0.08, 0.09], false);

    // Armored Cab and Troop Compartment
    b.add_box(19.2, 12.9, 0.5, 22.7, 15.1, 2.5, col_swat_navy);
    // Rear twin doors: vertical divide seam and handle
    b.add_box_ex(19.18, 13.98, 0.6, 19.21, 14.02, 2.3, [0.08, 0.08, 0.09], false); // Door divide seam
    b.add_box_ex(19.15, 13.85, 1.2, 19.20, 13.95, 1.3, col_chrome, false); // Door latch
    b.add_box_ex(19.18, 13.2, 1.5, 19.21, 13.8, 1.9, col_glass_dark, false); // Rear window slits
    b.add_box_ex(19.18, 14.2, 1.5, 19.21, 14.8, 1.9, col_glass_dark, false);
    // Rear diamond-plate step bumper
    b.add_box(18.8, 12.8, 0.25, 19.2, 15.2, 0.52, [0.12, 0.13, 0.14]);
    // Rear taillight clusters
    b.add_box_ex(19.18, 12.95, 0.7, 19.21, 13.15, 1.05, [0.88, 0.10, 0.10], false);
    b.add_box_ex(19.18, 14.85, 0.7, 19.21, 15.05, 1.05, [0.88, 0.10, 0.10], false);

    // Side windows and access door
    b.add_box_ex(21.8, 12.88, 1.6, 22.6, 12.92, 2.1, col_glass, false);
    b.add_box_ex(21.8, 15.08, 1.6, 22.6, 15.12, 2.1, col_glass, false);
    // White "SWAT / POLICE" side stripe panels
    b.add_box_ex(19.8, 12.88, 1.1, 22.2, 12.91, 1.35, [0.92, 0.92, 0.95], false);
    b.add_box_ex(19.8, 15.09, 1.1, 22.2, 15.12, 1.35, [0.92, 0.92, 0.95], false);
    // Roof Emergency Strobe Lightbar (Red & Blue flashing beacons)
    b.add_box(21.8, 13.2, 2.5, 22.4, 13.95, 2.72, [0.95, 0.08, 0.08]);
    b.add_box(21.8, 14.05, 2.5, 22.4, 14.8, 2.72, [0.08, 0.35, 0.95]);
    b.add_box(21.9, 13.95, 2.5, 22.3, 14.05, 2.70, col_chrome);

    // 5. Semi-Truck & Trailer backed into Bay 2 (x: 32.0..36.0, y: 15.0..25.0)
    // White Cab (y: 15.0..18.5)
    b.add_box(32.5, 15.0, 0.3, 35.5, 18.5, 3.2, [0.88, 0.88, 0.90]);
    // Chrome vertical exhaust stacks
    b.add_cylinder(32.3, 18.3, 1.2, 3.8, 0.08, 8, col_chrome, false);
    b.add_cylinder(35.7, 18.3, 1.2, 3.8, 0.08, 8, col_chrome, false);
    // Cargo Trailer (y: 18.5..25.0, z: 0.8..3.8)
    b.add_box(32.2, 18.5, 0.8, 35.8, 25.0, 3.8, [0.65, 0.67, 0.70]);

    // 6. East Rail Yard, Train Boxcar & Stacked Containers (x: 46.0..60.0, y: 10.0..58.0)
    b.add_floor(46.0, 10.0, 60.0, 58.0, 0.0, col_gravel, true);
    // Parallel train rails
    for tx in [49.0, 55.0] {
        b.add_box_ex(tx - 0.72, 10.0, 0.0, tx - 0.62, 58.0, 0.2, col_train_steel, false);
        b.add_box_ex(tx + 0.62, 10.0, 0.0, tx + 0.72, 58.0, 0.2, col_train_steel, false);
    }
    // Freight Boxcar on East track (x: 53.5..56.5, y: 32.0..46.0, z: 0.36..3.8)
    b.add_box(53.5, 32.0, 0.36, 56.5, 46.0, 3.8, col_train_red);
    b.add_box(53.4, 31.9, 3.8, 56.6, 46.1, 4.0, col_train_steel);
    // Steel ladder on boxcar side
    b.add_box(56.5, 33.0, 0.5, 56.7, 33.5, 3.8, col_catwalk);
    // Intermodal Shipping Containers
    b.add_box(47.0, 18.0, 0.0, 49.5, 28.0, 2.6, col_container_blue);
    b.add_box(47.0, 30.0, 0.0, 49.5, 40.0, 2.6, col_container_red);
    b.add_box(47.0, 22.0, 2.6, 49.5, 32.0, 5.2, col_container_green);

    // 7. Main Warehouse Shell (x: 8.0..46.0, y: 22.0..56.0, z: 0.0..9.0m)
    // Floor slab
    b.add_floor(8.0, 22.0, 46.0, 56.0, 0.0, col_warehouse_floor, true);

    // --- South Facade Realism & Architectural Articulation (y = 22.0) ---
    // Concrete foundation plinth along the entire base (z: 0.0..0.8m)
    b.add_box(8.0, 21.5, 0.0, 46.0, 22.0, 0.8, col_concrete_dark);
    b.add_box(8.0, 21.45, 0.75, 46.0, 22.0, 0.85, col_concrete); // Drip cap

    // Solid wall left of Bay 1 (x: 8.0..14.0) with corrugated panel relief
    b.add_box(8.0, 21.6, 0.8, 14.0, 22.0, 9.0, col_warehouse_wall);
    // Bay 1 header (x: 14.0..22.0, z: 3.8..9.0m)
    b.add_box(14.0, 21.6, 3.8, 22.0, 22.0, 9.0, col_warehouse_wall);
    // Wall between Bay 1 and Bay 2 (x: 22.0..30.0)
    b.add_box(22.0, 21.6, 0.8, 30.0, 22.0, 9.0, col_warehouse_wall);
    // Bay 2 header (x: 30.0..38.0)
    b.add_box(30.0, 21.6, 4.0, 38.0, 22.0, 9.0, col_warehouse_wall);
    // Personnel door header (portal at x: 38.5..41.5)
    b.add_box(38.0, 21.6, 2.6, 42.0, 22.0, 9.0, col_warehouse_wall);
    b.add_box(42.0, 21.6, 0.8, 46.0, 22.0, 9.0, col_warehouse_wall);

    // Exposed Structural Steel I-Beam Pilasters (Deep 3D shadows every bay)
    for &px in &[8.0, 14.0, 22.0, 30.0, 38.0, 46.0] {
        b.add_box(px - 0.25, 21.38, 0.8, px + 0.25, 21.65, 9.0, col_warehouse_trim);
        b.add_box(px - 0.35, 21.32, 0.0, px + 0.35, 21.68, 0.85, col_concrete_dark); // Base boot
    }

    // Heavy Roof Parapet Cornice & Sheet Metal Coping Cap
    b.add_box(7.6, 21.3, 8.9, 46.4, 22.1, 9.2, col_warehouse_trim);

    // Row of Industrial Steel-Framed Clerestory Transom Windows (z: 7.2..8.2m)
    for &(wx0, wx1) in &[(9.0, 13.0), (23.0, 29.0), (39.0, 45.0)] {
        b.add_box_ex(wx0, 21.52, 7.2, wx1, 21.58, 8.2, col_glass, false);
        // Window mullion grid
        for i in 1..4 {
            let mx = wx0 + (wx1 - wx0) * (i as f32) / 4.0;
            b.add_box_ex(mx - 0.06, 21.50, 7.2, mx + 0.06, 21.60, 8.2, col_warehouse_trim, false);
        }
        b.add_box_ex(wx0, 21.50, 7.68, wx1, 21.60, 7.74, col_warehouse_trim, false);
    }

    // --- Loading Bay 1 Fine Details ---
    // Overhead Roll-up Shutter Drum Profile (z: 3.6..4.1m, across x: 13.8..22.2)
    b.add_box_ex(13.9, 21.35, 3.65, 22.1, 21.75, 4.05, col_warehouse_trim, false);
    b.add_box_ex(13.9, 21.30, 3.75, 22.1, 21.80, 3.95, col_warehouse_trim, false);
    b.add_box_ex(13.9, 21.40, 3.60, 22.1, 21.70, 4.10, col_warehouse_trim, false);
    // Hanging slatted curtain hood
    b.add_box_ex(14.0, 21.54, 3.4, 22.0, 21.58, 3.8, [0.45, 0.47, 0.50], false);
    // Yellow & black alternating chevron hazard frame on Bay 1 jambs
    b.add_box_ex(13.8, 21.50, 0.0, 14.0, 22.0, 3.8, col_hazard_yellow, false);
    b.add_box_ex(22.0, 21.50, 0.0, 22.2, 22.0, 3.8, col_hazard_yellow, false);
    for h in 0..6 {
        let hz = (h as f32) * 0.6;
        b.add_box_ex(13.78, 21.48, hz, 14.02, 21.52, hz + 0.3, col_hazard_black, false);
        b.add_box_ex(21.98, 21.48, hz, 22.22, 21.52, hz + 0.3, col_hazard_black, false);
    }
    // Heavy steel crash safety bollards in front of Bay 1
    b.add_cylinder(13.5, 20.8, 0.0, 1.1, 0.14, 10, col_hazard_yellow, true);
    b.add_cylinder(13.5, 20.8, 0.95, 1.12, 0.15, 10, col_hazard_black, false);
    b.add_cylinder(22.5, 20.8, 0.0, 1.1, 0.14, 10, col_hazard_yellow, true);
    b.add_cylinder(22.5, 20.8, 0.95, 1.12, 0.15, 10, col_hazard_black, false);
    // Over-bay industrial gooseneck floodlight fixture
    b.add_box_ex(17.8, 21.1, 5.3, 18.2, 21.6, 5.4, col_train_steel, false);
    b.add_box_ex(17.5, 20.9, 5.0, 18.5, 21.3, 5.3, col_lamp_glow, false);

    // Weathered Industrial Signboard ("ASSAULT LOGISTICS / BAY 1 & 2")
    b.add_box_ex(15.5, 21.42, 5.6, 24.5, 21.46, 6.8, [0.85, 0.83, 0.78], false);
    b.add_box_ex(15.3, 21.40, 5.5, 24.7, 21.44, 5.6, col_hazard_yellow, false); // Bottom safety band
    b.add_box_ex(15.3, 21.40, 6.8, 24.7, 21.44, 6.9, col_hazard_yellow, false); // Top safety band
    b.add_box_ex(16.0, 21.47, 6.2, 24.0, 21.48, 6.5, [0.12, 0.14, 0.18], false); // Stencil logo bar

    // Rainwater drainage vertical downspouts
    b.add_cylinder(8.6, 21.45, 0.2, 9.2, 0.08, 8, col_warehouse_trim, false);
    b.add_cylinder(45.4, 21.45, 0.2, 9.2, 0.08, 8, col_warehouse_trim, false);

    // West Wall (x = 8.0)
    b.add_box(7.6, 22.0, 0.0, 8.0, 56.0, 9.0, col_warehouse_wall);
    // North Wall (y = 56.0) with rear exit portal at x: 24.0..27.0
    b.add_box(8.0, 56.0, 0.0, 24.0, 56.4, 9.0, col_warehouse_wall);
    b.add_box(24.0, 56.0, 2.6, 27.0, 56.4, 9.0, col_warehouse_wall);
    b.add_box(27.0, 56.0, 0.0, 46.0, 56.4, 9.0, col_warehouse_wall);
    // East Wall (x = 46.0) with doorway to fire escape at y: 44.0..47.0, z: 4.2..6.8
    b.add_box(46.0, 22.0, 0.0, 46.4, 44.0, 9.0, col_warehouse_wall);
    b.add_box(46.0, 44.0, 6.8, 46.4, 47.0, 9.0, col_warehouse_wall);
    b.add_box(46.0, 47.0, 0.0, 46.4, 56.0, 9.0, col_warehouse_wall);

    // Exterior Steel Fire Escape Staircase on East Wall (x: 46.4..48.8)
    b.add_ramp(46.4, 30.0, 0.0, 48.8, 36.0, 2.1, col_catwalk);
    b.add_box(46.4, 36.0, 2.0, 48.8, 38.5, 2.1, col_catwalk);
    b.add_ramp(46.4, 38.5, 2.1, 48.8, 44.0, 4.2, col_catwalk);
    b.add_box(46.4, 44.0, 4.1, 48.8, 47.0, 4.2, col_catwalk);
    b.add_ramp(46.4, 47.0, 4.2, 48.8, 54.0, 9.0, col_catwalk);

    // Warehouse Roof (z = 9.0m) with Parapets
    b.add_floor(8.0, 22.0, 46.0, 56.0, 9.0, col_warehouse_roof, true);
    b.add_box(7.8, 21.8, 9.0, 46.2, 22.2, 9.8, col_concrete);
    b.add_box(7.8, 55.8, 9.0, 46.2, 56.2, 9.8, col_concrete);
    b.add_box(7.8, 21.8, 9.0, 8.2, 56.2, 9.8, col_concrete);
    b.add_box(45.8, 21.8, 9.0, 46.2, 56.2, 9.8, col_concrete);

    // 8. Rooftop Ventilation System & Chillers
    b.add_box(14.0, 28.0, 9.0, 18.0, 32.0, 10.2, col_vent_sheet);
    b.add_box(28.0, 44.0, 9.0, 32.0, 48.0, 10.2, col_vent_sheet);
    b.add_box(36.0, 28.0, 9.0, 40.0, 34.0, 10.4, [0.35, 0.38, 0.40]);

    // 9. Warehouse Interior Catwalks, Structural Trusses & Columns
    // Structural steel H-beam columns
    for cx in [18.0, 32.0] {
        for cy in [30.0, 42.0] {
            b.add_box(cx - 0.35, cy - 0.35, 0.0, cx + 0.35, cy + 0.35, 9.0, [0.18, 0.19, 0.20]);
        }
    }
    // High-bay roof trusses
    for ty in [28.0, 38.0, 48.0] {
        b.add_box_ex(8.0, ty - 0.15, 8.0, 46.0, ty + 0.15, 8.8, [0.18, 0.19, 0.20], false);
        // Zigzag diagonal truss webbing
        for tx in 0..6 {
            let wx0 = 10.0 + (tx as f32) * 5.5;
            b.add_box_ex(wx0, ty - 0.08, 8.0, wx0 + 2.5, ty + 0.08, 8.8, [0.24, 0.25, 0.27], false);
        }
    }
    // High-bay pendant warehouse dome lamps
    for lx in [18.0, 30.0] {
        for ly in [28.0, 38.0, 48.0] {
            b.add_cylinder(lx, ly, 7.6, 7.8, 0.45, 10, [0.18, 0.19, 0.20], false);
            b.add_box_ex(lx - 0.2, ly - 0.2, 7.45, lx + 0.2, ly + 0.2, 7.6, col_lamp_glow, false);
        }
    }

    // Catwalk at z = 4.2m along West wall
    b.add_box(8.5, 26.0, 4.15, 12.5, 52.0, 4.2, col_catwalk);
    b.add_box(12.4, 26.0, 4.2, 12.5, 52.0, 5.2, col_railing_yellow);
    // Catwalk along North wall connecting to Office
    b.add_box(12.5, 49.0, 4.15, 28.0, 52.5, 4.2, col_catwalk);
    b.add_box(12.5, 49.0, 4.2, 28.0, 49.1, 5.2, col_railing_yellow);
    // Interior steel staircase climbing from floor to catwalk
    b.add_ramp(10.0, 26.0, 0.0, 12.5, 36.0, 4.2, col_catwalk);

    // 10. Upstairs Hostage Office (2nd level, x: 28.0..45.5, y: 40.0..55.5, z: 4.2..8.8m)
    b.add_floor(28.0, 40.0, 45.5, 55.5, 4.2, col_office_floor, true);
    // South Wall overlooking main floor with large observation window
    b.add_box(28.0, 39.8, 4.2, 45.5, 40.0, 5.0, [0.75, 0.72, 0.68]);
    b.add_box_ex(29.0, 39.85, 5.0, 44.5, 39.95, 7.2, col_glass, false);
    // Window mullions
    for i in 1..5 {
        let mx = 29.0 + (15.5) * (i as f32) / 5.0;
        b.add_box_ex(mx - 0.08, 39.82, 5.0, mx + 0.08, 39.98, 7.2, col_warehouse_trim, false);
    }
    b.add_box_ex(29.0, 39.82, 6.05, 44.5, 39.98, 6.15, col_warehouse_trim, false);

    b.add_box(28.0, 39.8, 7.2, 45.5, 40.0, 8.8, [0.75, 0.72, 0.68]);
    // West office wall with doorway to catwalk
    b.add_box(28.0, 40.0, 4.2, 28.4, 48.0, 8.8, [0.75, 0.72, 0.68]);
    b.add_box(28.0, 48.0, 6.6, 28.4, 51.0, 8.8, [0.75, 0.72, 0.68]);
    b.add_box(28.0, 51.0, 4.2, 28.4, 55.5, 8.8, [0.75, 0.72, 0.68]);
    // Office furniture & CCTV security monitors
    for dx in [33.0, 39.0] {
        b.add_box(dx - 1.2, 44.0, 4.2, dx + 1.2, 45.5, 5.0, col_desk);
        b.add_box(dx - 0.4, 44.8, 5.0, dx + 0.4, 45.0, 5.6, col_monitor);
    }
    b.add_box_ex(34.0, 54.8, 5.4, 38.0, 55.0, 6.6, col_cctv_screen, false);

    // 11. Tactical Cover Props (Forklift, Crates, Pallets, Barrels)
    // Detailed Yellow Forklift near Bay 1
    b.add_box(16.0, 34.0, 0.0, 18.5, 36.5, 1.4, col_forklift);
    b.add_box(16.2, 34.2, 1.4, 18.3, 36.3, 2.2, [0.18, 0.19, 0.20]); // Roll cage
    b.add_box(17.0, 36.5, 0.0, 17.5, 36.8, 2.8, [0.22, 0.24, 0.26]); // Lift mast
    b.add_box(16.4, 36.8, 0.1, 18.1, 38.0, 0.2, [0.22, 0.24, 0.26]); // Forks
    // Heavy Pallet racks along west wall
    b.add_box(8.2, 38.0, 0.0, 10.2, 48.0, 3.8, col_container_blue);
    b.add_box_ex(8.15, 38.0, 1.8, 10.25, 48.0, 1.95, col_hazard_yellow, false); // Shelf beams
    b.add_box_ex(8.15, 38.0, 3.6, 10.25, 48.0, 3.75, col_hazard_yellow, false);
    // Wooden crate stacks
    b.add_box(22.0, 34.0, 0.0, 24.5, 36.5, 1.6, col_crate);
    b.add_box(14.0, 42.0, 0.0, 16.5, 44.5, 1.8, col_crate);
    // Steel oil drums with bung rims
    for bx in [13.0, 36.0, 51.0] {
        b.add_cylinder(bx, 24.0, 0.0, 0.9, 0.32, 10, [0.12, 0.25, 0.65], true);
        b.add_cylinder(bx, 24.0, 0.88, 0.92, 0.34, 10, [0.20, 0.22, 0.25], false); // Chime
    }

    // Bomb site / Hostage zone decals
    b.add_floor(34.0, 46.0, 40.0, 52.0, 4.21, [0.88, 0.20, 0.15], false);
    b.add_floor(18.0, 32.0, 24.0, 38.0, 0.01, [0.88, 0.20, 0.15], false);

    // Spawns: CT at South Street facing North (yaw = 90.0), T in Hostage Office facing South (yaw = 270.0)
    let spawns = vec![
        SpawnPoint { x: 16.0, y: 7.0, z: 0.18, yaw: 90.0, team: 0 },
        SpawnPoint { x: 20.0, y: 7.0, z: 0.18, yaw: 90.0, team: 0 },
        SpawnPoint { x: 24.0, y: 7.0, z: 0.18, yaw: 90.0, team: 0 },
        SpawnPoint { x: 28.0, y: 7.0, z: 0.18, yaw: 90.0, team: 0 },
        SpawnPoint { x: 32.0, y: 48.0, z: 4.2, yaw: 270.0, team: 1 },
        SpawnPoint { x: 36.0, y: 48.0, z: 4.2, yaw: 270.0, team: 1 },
        SpawnPoint { x: 40.0, y: 48.0, z: 4.2, yaw: 270.0, team: 1 },
        SpawnPoint { x: 34.0, y: 52.0, z: 4.2, yaw: 270.0, team: 1 },
    ];

    let items = vec![
        ItemRow { id: 1, kind: "health".into(), x: 14.0, y: 6.0, z: 0.18 },
        ItemRow { id: 2, kind: "health".into(), x: 55.0, y: 16.0, z: 0.0 },
        ItemRow { id: 3, kind: "health".into(), x: 38.0, y: 52.0, z: 4.2 },
        ItemRow { id: 4, kind: "armour".into(), x: 20.0, y: 36.0, z: 0.0 },
        ItemRow { id: 5, kind: "armour".into(), x: 32.0, y: 44.0, z: 4.2 },
        ItemRow { id: 6, kind: "ammo_assault".into(), x: 21.0, y: 15.5, z: 0.0 },
        ItemRow { id: 7, kind: "ammo_assault".into(), x: 54.0, y: 30.0, z: 0.0 },
        ItemRow { id: 8, kind: "ammo_sniper".into(), x: 24.0, y: 7.0, z: 7.5 },
        ItemRow { id: 9, kind: "clips".into(), x: 10.5, y: 40.0, z: 4.2 },
        ItemRow { id: 10, kind: "grenade".into(), x: 16.0, y: 30.0, z: 9.0 },
    ];

    let bounds = WorldBounds {
        min: [4.0, 4.0, 0.0],
        max: [60.0, 60.0, 14.0],
        center: [32.0, 32.0, 7.0],
        extent: 56.0,
    };

    let triangles = b.render_positions.len() / 9;

    World3D {
        info,
        bounds,
        render_positions: b.render_positions,
        render_normals: b.render_normals,
        render_colors: b.render_colors,
        render_uvs: b.render_uvs,
        render_materials: b.render_materials,
        triangles,
        col_vertices: b.col_vertices,
        col_indices: b.col_indices,
        spawns,
        items,
        waterlevel: -100.0,
        windows: std::collections::HashMap::new(),
    }
}

/// Create the high-rise corporate headquarters "Office" (`hd_office`) 3D arena.
/// Features:
/// 1. Executive Boardroom: 8m mahogany conference table, chairs, wall AV screen, Site A.
/// 2. Open-plan Cubicle Farm: acoustic felt dividers, workstations with dual monitors.
/// 3. Central Reception & Elevator Lobby: Calacatta marble counter, twin stainless elevator doors.
/// 4. High-Density IT Datacenter: 42U server racks with pulsing LED status strips, Site B.
/// 5. Panoramic exterior perimeter windows and drop-ceiling fluorescent lights.
pub fn create_procedural_office_3d(info: MapInfo) -> World3D {
    let mut b = FacilityBuilder::new();

    let col_carpet_gray = [0.20, 0.22, 0.25];
    let col_marble_lobby = [0.85, 0.85, 0.88];
    let col_server_floor = [0.55, 0.58, 0.62];
    let col_boardroom_carpet = [0.18, 0.20, 0.26];
    let col_wall_drywall = [0.82, 0.83, 0.85];
    let col_wall_accent = [0.15, 0.25, 0.35];
    let col_mahogany = [0.30, 0.14, 0.09];
    let col_acoustic_felt = [0.16, 0.38, 0.48];
    let col_stainless = [0.72, 0.74, 0.76];
    let col_black_metal = [0.10, 0.10, 0.12];
    let col_server_led = [0.0, 0.95, 0.55];
    let col_screen_blue = [0.15, 0.50, 0.90];
    let col_ceiling_tile = [0.90, 0.90, 0.92];
    let col_fluorescent = [0.98, 0.98, 1.0];

    // 1. Perimeter Walls & Windows (64x64 bounds, height 12.0)
    b.add_quad([4.0, 4.0, 0.0], [60.0, 4.0, 0.0], [60.0, 4.0, 12.0], [4.0, 4.0, 12.0], col_wall_drywall, true);
    b.add_quad([60.0, 60.0, 0.0], [4.0, 60.0, 0.0], [4.0, 60.0, 12.0], [60.0, 60.0, 12.0], col_wall_drywall, true);
    b.add_quad([60.0, 4.0, 0.0], [60.0, 60.0, 0.0], [60.0, 60.0, 12.0], [60.0, 4.0, 12.0], col_wall_drywall, true);
    b.add_quad([4.0, 60.0, 0.0], [4.0, 4.0, 0.0], [4.0, 4.0, 12.0], [4.0, 60.0, 12.0], col_wall_drywall, true);

    // 2. Flooring zones
    b.add_floor(4.0, 4.0, 36.0, 30.0, 0.0, col_marble_lobby, true);
    b.add_floor(36.0, 4.0, 60.0, 60.0, 0.0, col_carpet_gray, true);
    b.add_floor(4.0, 30.0, 36.0, 60.0, 0.0, col_boardroom_carpet, true);
    b.add_floor(6.0, 6.0, 26.0, 26.0, 0.15, col_server_floor, true);

    // 3. Structural Concrete Columns
    for cx in [20.0, 36.0, 48.0] {
        for cy in [16.0, 32.0, 48.0] {
            b.add_box(cx - 0.6, cy - 0.6, 0.0, cx + 0.6, cy + 0.6, 12.0, col_wall_accent);
        }
    }

    // 4. Boardroom Interior
    b.add_box(4.0, 30.0, 0.0, 26.0, 30.8, 12.0, col_wall_drywall);
    b.add_box_ex(20.0, 30.0, 2.8, 24.0, 30.8, 12.0, col_wall_drywall, true);
    b.add_box(12.0, 44.8, 0.0, 20.0, 47.2, 0.8, col_mahogany);
    for i in 0..5 {
        let x = 13.0 + (i as f32) * 1.5;
        b.add_box(x, 43.8, 0.0, x + 0.6, 44.4, 0.9, col_black_metal);
        b.add_box(x, 47.6, 0.0, x + 0.6, 48.2, 0.9, col_black_metal);
    }
    b.add_box(4.1, 43.0, 1.8, 4.3, 49.0, 4.2, col_screen_blue);

    // 5. Cubicle Farm
    for row_x in [42.0, 50.0] {
        for pod_y in [14.0, 26.0, 38.0, 50.0] {
            b.add_box(row_x - 3.0, pod_y - 0.1, 0.0, row_x + 3.0, pod_y + 0.1, 1.4, col_acoustic_felt);
            b.add_box(row_x - 3.0, pod_y - 1.5, 0.0, row_x - 2.8, pod_y + 1.5, 1.4, col_acoustic_felt);
            b.add_box(row_x + 2.8, pod_y - 1.5, 0.0, row_x + 3.0, pod_y + 1.5, 1.4, col_acoustic_felt);
            b.add_box(row_x - 2.6, pod_y + 0.2, 0.0, row_x + 2.6, pod_y + 1.2, 0.75, col_wall_drywall);
            b.add_box(row_x - 2.6, pod_y - 1.2, 0.0, row_x + 2.6, pod_y - 0.2, 0.75, col_wall_drywall);
            b.add_box(row_x - 1.0, pod_y + 0.6, 0.75, row_x - 0.2, pod_y + 0.8, 1.3, col_screen_blue);
            b.add_box(row_x + 0.2, pod_y + 0.6, 0.75, row_x + 1.0, pod_y + 0.8, 1.3, col_screen_blue);
        }
    }

    // 6. Central Reception Lobby & Elevators
    b.add_box(26.0, 17.0, 0.0, 32.0, 19.0, 1.1, col_marble_lobby);
    b.add_box(25.8, 16.8, 1.1, 32.2, 19.2, 1.15, col_stainless);
    b.add_box(25.0, 7.0, 0.0, 33.0, 9.0, 12.0, col_wall_accent);
    b.add_box(26.2, 8.9, 0.0, 28.2, 9.1, 2.6, col_stainless);
    b.add_box(29.8, 8.9, 0.0, 31.8, 9.1, 2.6, col_stainless);

    // 7. IT Datacenter
    for r in 0..3 {
        let sx = 9.0 + (r as f32) * 4.0;
        for s in 0..3 {
            let sy = 9.0 + (s as f32) * 4.5;
            b.add_box(sx, sy, 0.15, sx + 1.2, sy + 2.4, 2.4, col_black_metal);
            b.add_box_ex(sx + 1.21, sy + 0.2, 0.4, sx + 1.23, sy + 2.2, 2.2, col_server_led, false);
        }
    }

    // 8. Ceiling & Fluorescent Lighting
    b.add_floor(4.0, 4.0, 60.0, 60.0, 12.0, col_ceiling_tile, false);
    for lx in [16.0, 28.0, 40.0, 52.0] {
        for ly in [14.0, 26.0, 38.0, 50.0] {
            b.add_floor(lx - 1.2, ly - 0.4, lx + 1.2, ly + 0.4, 11.98, col_fluorescent, false);
        }
    }

    let spawns = vec![
        SpawnPoint { x: 28.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 32.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 28.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 32.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 50.0, y: 32.0, z: 0.0, yaw: 270.0, team: 0 },
        SpawnPoint { x: 52.0, y: 28.0, z: 0.0, yaw: 270.0, team: 0 },
        SpawnPoint { x: 52.0, y: 36.0, z: 0.0, yaw: 270.0, team: 0 },
        SpawnPoint { x: 48.0, y: 24.0, z: 0.0, yaw: 270.0, team: 0 },
    ];

    let items = vec![
        ItemRow { id: 1, kind: "health".into(), x: 10.0, y: 48.0, z: 0.0 },
        ItemRow { id: 2, kind: "health".into(), x: 10.0, y: 12.0, z: 0.0 },
        ItemRow { id: 3, kind: "health".into(), x: 54.0, y: 32.0, z: 0.0 },
        ItemRow { id: 4, kind: "armour".into(), x: 16.0, y: 46.0, z: 0.0 },
        ItemRow { id: 5, kind: "armour".into(), x: 16.0, y: 16.0, z: 0.0 },
        ItemRow { id: 6, kind: "ammo_assault".into(), x: 22.0, y: 32.0, z: 0.0 },
        ItemRow { id: 7, kind: "ammo_assault".into(), x: 44.0, y: 18.0, z: 0.0 },
        ItemRow { id: 8, kind: "ammo_sniper".into(), x: 44.0, y: 44.0, z: 0.0 },
        ItemRow { id: 9, kind: "clips".into(), x: 30.0, y: 28.0, z: 0.0 },
        ItemRow { id: 10, kind: "grenade".into(), x: 30.0, y: 36.0, z: 0.0 },
    ];

    let bounds = WorldBounds {
        min: [4.0, 4.0, 0.0],
        max: [60.0, 60.0, 12.0],
        center: [32.0, 32.0, 6.0],
        extent: 56.0,
    };

    let triangles = b.render_positions.len() / 9;

    World3D {
        info,
        bounds,
        render_positions: b.render_positions,
        render_normals: b.render_normals,
        render_colors: b.render_colors,
        render_uvs: b.render_uvs,
        render_materials: b.render_materials,
        triangles,
        col_vertices: b.col_vertices,
        col_indices: b.col_indices,
        spawns,
        items,
        waterlevel: -100.0,
        windows: std::collections::HashMap::new(),
    }
}

/// Create the Desert Citadel II ("Dust II" / `hd_dust2`) 3D arena.
/// Features:
/// 1. Long A: Sunken Pit with sniper ramp, Long corridor, corner, double sandstone archway, ramp up to A.
/// 2. Bomb Site A: Elevated sandstone plateau (+1.2m), double wooden boxes, goose wall, ramp to CT.
/// 3. Catwalk / Short A: Elevated stone walkway (+2.4m) overlooking Mid, Xbox jump crate, stairs to Lower Dark.
/// 4. Middle & Mid Doors: Central street with heavy wooden double doors slightly ajar with sniper slit.
/// 5. Dark Tunnels: Subterranean vaulted stone tunnel network connecting T side, Lower Dark, and Upper B.
/// 6. Bomb Site B: Enclosed Moroccan fortress courtyard, Upper B tunnel lip, B Window, B Double Doors, Back Platform.
/// 7. T & CT Spawns: Terracotta souk courtyard with fabric sun canopies, Persian rugs, and desert palm trees.
pub fn create_procedural_dust2_3d(info: MapInfo) -> World3D {
    let mut b = FacilityBuilder::new();

    let col_sandstone_light = [0.84, 0.74, 0.58];
    let col_sandstone_ochre = [0.76, 0.58, 0.40];
    let col_sandstone_dark = [0.58, 0.46, 0.35];
    let col_limestone_paving = [0.78, 0.72, 0.62];
    let col_wood_cedar = [0.38, 0.28, 0.20];
    let col_wood_crate = [0.52, 0.38, 0.24];
    let col_tile_blue = [0.12, 0.35, 0.58];
    let col_metal_iron = [0.25, 0.20, 0.18];
    let col_scaffolding = [0.45, 0.45, 0.48];
    let col_canopy_crimson = [0.58, 0.12, 0.14];
    let col_palm_bark = [0.30, 0.22, 0.16];

    // 1. Terrain Ground & Outer Perimeter Walls (4.0..66.0, 4.0..66.0)
    b.add_floor(4.0, 4.0, 66.0, 66.0, 0.0, col_limestone_paving, true);

    // High Sandstone Fortress Perimeter Walls (height 10.0)
    b.add_quad([4.0, 4.0, 0.0], [66.0, 4.0, 0.0], [66.0, 4.0, 10.0], [4.0, 4.0, 10.0], col_sandstone_ochre, true);
    b.add_quad([66.0, 66.0, 0.0], [4.0, 66.0, 0.0], [4.0, 66.0, 10.0], [66.0, 66.0, 10.0], col_sandstone_ochre, true);
    b.add_quad([66.0, 4.0, 0.0], [66.0, 66.0, 0.0], [66.0, 66.0, 10.0], [66.0, 4.0, 10.0], col_sandstone_ochre, true);
    b.add_quad([4.0, 66.0, 0.0], [4.0, 4.0, 0.0], [4.0, 4.0, 10.0], [4.0, 66.0, 10.0], col_sandstone_ochre, true);

    // Decorative Moorish blue tile trim along perimeter
    b.add_box_ex(4.0, 4.1, 9.6, 66.0, 4.3, 10.0, col_tile_blue, false);
    b.add_box_ex(4.0, 65.7, 9.6, 66.0, 65.9, 10.0, col_tile_blue, false);

    // 2. Long A & Pit
    b.add_box(43.4, 8.0, 0.0, 44.6, 48.0, 9.0, col_sandstone_light);
    b.add_floor(52.0, 6.0, 65.0, 18.0, -1.4, col_sandstone_dark, true);
    b.add_ramp(52.0, 18.0, -1.4, 56.0, 22.0, 0.0, col_limestone_paving);
    b.add_box(56.0, 11.0, -1.4, 60.0, 13.0, -0.4, col_metal_iron);
    b.add_box(44.0, 15.2, 0.0, 48.0, 16.8, 4.8, col_sandstone_ochre);
    b.add_box(56.0, 15.2, 0.0, 66.0, 16.8, 4.8, col_sandstone_ochre);
    b.add_box(48.0, 15.2, 3.8, 56.0, 16.8, 4.8, col_sandstone_ochre);
    b.add_box(55.0, 35.4, 0.0, 66.0, 36.6, 8.0, col_sandstone_light);
    b.add_cylinder(54.0, 34.0, 0.0, 1.2, 0.45, 8, col_wood_cedar, true);
    b.add_cylinder(54.8, 34.0, 0.0, 1.2, 0.45, 8, col_wood_cedar, true);
    b.add_ramp(48.0, 42.0, 0.0, 56.0, 48.0, 1.2, col_limestone_paving);

    // 3. Bomb Site A (Elevated plateau at Z = 1.2m)
    b.add_floor(42.0, 48.0, 58.0, 62.0, 1.2, col_limestone_paving, true);
    b.add_box(45.0, 51.0, 1.2, 46.3, 53.6, 2.4, col_wood_crate);
    b.add_box(45.0, 51.0, 2.4, 46.3, 52.3, 3.6, col_wood_crate);
    b.add_box(52.0, 61.4, 1.2, 66.0, 62.6, 6.0, col_sandstone_ochre);
    b.add_box_ex(54.0, 61.3, 2.0, 56.0, 61.4, 3.5, col_tile_blue, false);
    b.add_ramp(42.0, 56.0, 1.2, 46.0, 62.0, 0.0, col_limestone_paving);

    // 4. Catwalk & Short A (Elevated walkway at Z = 2.4m)
    b.add_floor(34.0, 42.0, 44.0, 48.0, 2.4, col_sandstone_light, true);
    b.add_ramp(30.0, 40.0, 0.0, 34.0, 44.0, 2.4, col_limestone_paving);
    b.add_box_ex(34.0, 42.0, 2.4, 34.2, 48.0, 3.4, col_metal_iron, true);

    // 5. Middle & Mid Doors
    b.add_box(25.4, 16.0, 0.0, 26.6, 48.0, 8.0, col_sandstone_light);
    b.add_box(26.6, 32.1, 0.0, 30.2, 32.35, 4.0, col_wood_cedar);
    b.add_box(30.65, 31.7, 0.0, 34.2, 31.95, 4.0, col_wood_cedar);
    b.add_box(26.6, 31.5, 4.0, 34.2, 32.5, 5.2, col_sandstone_ochre);
    b.add_box(29.2, 37.2, 0.0, 30.8, 38.8, 1.6, col_wood_crate);

    // 6. Dark Tunnels (Subterranean Vaulted)
    b.add_box(7.4, 22.0, 0.0, 8.6, 46.0, 4.2, col_sandstone_dark);
    b.add_box(21.4, 22.0, 0.0, 22.6, 38.0, 4.2, col_sandstone_dark);
    b.add_floor(8.6, 22.0, 22.6, 42.0, 4.2, col_sandstone_dark, true);
    for ty in [26.0, 32.0, 38.0] {
        b.add_box(8.6, ty - 0.4, 0.0, 10.0, ty + 0.4, 3.6, col_sandstone_ochre);
        b.add_box(20.0, ty - 0.4, 0.0, 21.4, ty + 0.4, 3.6, col_sandstone_ochre);
        b.add_box(8.6, ty - 0.4, 3.2, 21.4, ty + 0.4, 3.8, col_sandstone_ochre);
    }

    // 7. Bomb Site B (Fortress Courtyard)
    b.add_box(5.4, 46.0, 0.0, 6.6, 64.0, 8.0, col_sandstone_ochre);
    b.add_box(5.4, 63.4, 0.0, 26.6, 64.6, 8.0, col_sandstone_ochre);
    b.add_box(25.4, 48.0, 0.0, 26.6, 64.0, 8.0, col_sandstone_ochre);
    b.add_floor(12.0, 50.0, 20.0, 58.0, 0.6, col_limestone_paving, true);
    b.add_box(21.4, 44.0, 0.0, 22.6, 48.0, 2.0, col_sandstone_light);
    b.add_box(21.4, 44.0, 4.2, 22.6, 48.0, 8.0, col_sandstone_light);
    b.add_box(20.0, 45.0, 0.0, 21.4, 47.0, 1.2, col_wood_crate);
    b.add_box_ex(22.8, 44.2, 0.0, 23.0, 47.8, 5.0, col_scaffolding, false);
    b.add_box(25.4, 55.0, 0.0, 25.7, 57.0, 3.6, col_wood_cedar);
    b.add_box(25.4, 57.5, 0.0, 25.7, 59.5, 3.6, col_wood_cedar);
    b.add_floor(7.0, 54.0, 11.0, 62.0, 1.0, col_limestone_paving, true);
    b.add_box(8.0, 56.0, 1.0, 9.4, 57.4, 2.2, col_wood_crate);

    // 8. T Souk courtyards, sun canopies & palm trees
    b.add_box_ex(26.0, 8.0, 4.2, 38.0, 14.0, 4.3, col_canopy_crimson, false);
    b.add_cylinder(22.0, 8.0, 0.0, 6.0, 0.25, 8, col_palm_bark, false);
    b.add_cylinder(36.0, 62.0, 0.0, 6.0, 0.25, 8, col_palm_bark, false);

    let spawns = vec![
        SpawnPoint { x: 30.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 34.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 30.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 34.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 34.0, y: 60.0, z: 0.0, yaw: 270.0, team: 0 },
        SpawnPoint { x: 38.0, y: 60.0, z: 0.0, yaw: 270.0, team: 0 },
        SpawnPoint { x: 34.0, y: 56.0, z: 0.0, yaw: 270.0, team: 0 },
        SpawnPoint { x: 38.0, y: 56.0, z: 0.0, yaw: 270.0, team: 0 },
    ];

    let items = vec![
        ItemRow { id: 1, kind: "health".into(), x: 58.0, y: 12.0, z: 0.0 },
        ItemRow { id: 2, kind: "health".into(), x: 20.0, y: 34.0, z: 0.0 },
        ItemRow { id: 3, kind: "health".into(), x: 36.0, y: 58.0, z: 0.0 },
        ItemRow { id: 4, kind: "armour".into(), x: 54.0, y: 36.0, z: 0.0 },
        ItemRow { id: 5, kind: "armour".into(), x: 10.0, y: 56.0, z: 0.0 },
        ItemRow { id: 6, kind: "ammo_assault".into(), x: 30.0, y: 30.0, z: 0.0 },
        ItemRow { id: 7, kind: "ammo_assault".into(), x: 48.0, y: 52.0, z: 1.2 },
        ItemRow { id: 8, kind: "ammo_sniper".into(), x: 16.0, y: 32.0, z: 0.0 },
        ItemRow { id: 9, kind: "clips".into(), x: 38.0, y: 44.0, z: 2.4 },
        ItemRow { id: 10, kind: "grenade".into(), x: 28.0, y: 12.0, z: 0.0 },
    ];

    let bounds = WorldBounds {
        min: [4.0, 4.0, -2.0],
        max: [66.0, 66.0, 14.0],
        center: [35.0, 35.0, 6.0],
        extent: 62.0,
    };

    let triangles = b.render_positions.len() / 9;

    World3D {
        info,
        bounds,
        render_positions: b.render_positions,
        render_normals: b.render_normals,
        render_colors: b.render_colors,
        render_uvs: b.render_uvs,
        render_materials: b.render_materials,
        triangles,
        col_vertices: b.col_vertices,
        col_indices: b.col_indices,
        spawns,
        items,
        waterlevel: -100.0,
        windows: std::collections::HashMap::new(),
    }
}

/// Create the Tuscan Village ("Inferno" / `hd_inferno`) 3D tournament arena.
/// Features:
/// 1. Banana: Curved cobblestone alley, wooden car barricade, sandbags, and wall boost.
/// 2. Bomb Site B: Open cobblestone square with central sculpted stone fountain, church stone arch, and coffins.
/// 3. Middle & Alt-Mid: Central cobblestone street, boiler room doorway, and T-ramp.
/// 4. Apartments / Boiler: Two-story residential villa with wood floor (z=2.8m) and balcony overlooking Site A.
/// 5. Bomb Site A: Graveyard stone wall, sunken Pit, bicycle cart cover, balcony platform, and CT porch.
/// 6. T & CT Spawns: Terracotta courtyards with Tuscan cypress trees, amphoras, and terracotta tile roofs.
pub fn create_procedural_inferno_3d(info: MapInfo) -> World3D {
    let mut b = FacilityBuilder::new();

    let col_cobble = [0.50, 0.48, 0.45];
    let col_stucco_warm = [0.86, 0.76, 0.62];
    let col_stucco_ochre = [0.80, 0.60, 0.42];
    let col_limestone = [0.72, 0.70, 0.66];
    let col_terracotta = [0.70, 0.32, 0.18];
    let col_chestnut = [0.34, 0.24, 0.16];
    let col_crate = [0.54, 0.40, 0.26];
    let col_iron = [0.16, 0.16, 0.18];
    let col_water = [0.20, 0.45, 0.60];
    let col_sandbag = [0.68, 0.60, 0.46];
    let col_cypress_trunk = [0.28, 0.22, 0.18];
    let col_cypress_leaf = [0.12, 0.28, 0.10];

    // 1. Terrain Ground & Outer Perimeter Walls (4.0..66.0, 4.0..66.0)
    b.add_floor(4.0, 4.0, 66.0, 66.0, 0.0, col_cobble, true);

    // High Perimeter Tuscan Villa Facades (height 10.0)
    b.add_quad([4.0, 4.0, 0.0], [66.0, 4.0, 0.0], [66.0, 4.0, 10.0], [4.0, 4.0, 10.0], col_stucco_warm, true);
    b.add_quad([66.0, 66.0, 0.0], [4.0, 66.0, 0.0], [4.0, 66.0, 10.0], [66.0, 66.0, 10.0], col_stucco_warm, true);
    b.add_quad([66.0, 4.0, 0.0], [66.0, 66.0, 0.0], [66.0, 66.0, 10.0], [66.0, 4.0, 10.0], col_stucco_ochre, true);
    b.add_quad([4.0, 66.0, 0.0], [4.0, 4.0, 0.0], [4.0, 4.0, 10.0], [4.0, 66.0, 10.0], col_stucco_ochre, true);

    // Terracotta roof eaves trim
    b.add_box_ex(4.0, 4.2, 9.8, 66.0, 4.6, 10.2, col_terracotta, false);
    b.add_box_ex(4.0, 65.4, 9.8, 66.0, 65.8, 10.2, col_terracotta, false);

    // 2. Banana Alley & Car
    b.add_box(25.4, 12.0, 0.0, 26.6, 40.0, 9.0, col_stucco_warm);
    b.add_box(11.4, 16.0, 0.0, 12.6, 40.0, 9.0, col_stucco_ochre);
    // Banana car barricade at (18.0, 28.0)
    b.add_box(16.2, 27.1, 0.0, 19.8, 28.9, 1.0, col_iron);
    b.add_box(16.5, 27.2, 1.0, 18.5, 28.8, 1.8, col_chestnut);
    // Sandbags cluster
    b.add_box(13.8, 33.5, 0.0, 16.2, 34.5, 0.8, col_sandbag);
    b.add_box(14.1, 33.6, 0.8, 15.9, 34.4, 1.3, col_sandbag);
    // Boost cart
    b.add_box(23.8, 30.9, 0.0, 25.2, 33.1, 1.2, col_crate);

    // 3. Bomb Site B & Stone Fountain
    b.add_box(5.4, 38.0, 0.0, 6.6, 62.0, 9.0, col_stucco_ochre);
    b.add_box(5.4, 61.4, 0.0, 31.0, 62.6, 9.0, col_stucco_warm);
    // Fountain basin and water
    b.add_cylinder(18.0, 50.0, 0.0, 0.8, 2.6, 16, col_limestone, true);
    b.add_cylinder(18.0, 50.0, 0.5, 0.3, 2.3, 16, col_water, false);
    b.add_cylinder(18.0, 50.0, 0.4, 1.6, 0.5, 12, col_limestone, true);
    b.add_cylinder(18.0, 50.0, 1.8, 0.4, 1.1, 12, col_limestone, true);
    // Church stone arch
    b.add_box(11.5, 55.2, 0.0, 12.3, 56.8, 4.6, col_limestone);
    b.add_box(15.7, 55.2, 0.0, 16.5, 56.8, 4.6, col_limestone);
    b.add_box(11.5, 55.2, 4.6, 16.5, 56.8, 5.5, col_limestone);
    // Coffins crate stack
    b.add_box(21.3, 50.8, 0.0, 22.7, 53.2, 1.2, col_crate);
    b.add_box(21.4, 51.1, 1.2, 22.6, 52.9, 2.0, col_crate);

    // 4. Middle & Alt-Mid
    b.add_box(41.4, 10.0, 0.0, 42.6, 42.0, 9.0, col_stucco_warm);
    // Boiler arch doorway
    b.add_box(34.2, 29.4, 0.0, 35.0, 30.6, 3.6, col_limestone);
    b.add_box(37.0, 29.4, 0.0, 37.8, 30.6, 3.6, col_limestone);
    b.add_box(34.2, 29.4, 3.6, 37.8, 30.6, 4.5, col_limestone);
    // Mid T-ramp
    b.add_ramp(31.0, 14.0, 0.0, 37.0, 22.0, 1.0, col_cobble);
    // Alt-Mid Hay Cart
    b.add_box(31.0, 22.4, 0.0, 33.0, 25.6, 1.2, col_chestnut);

    // 5. Apartments & 2nd Floor
    b.add_box(33.4, 31.0, 0.0, 34.6, 45.0, 5.6, col_stucco_ochre);
    b.add_box(34.0, 45.4, 0.0, 46.0, 46.6, 5.6, col_stucco_warm);
    b.add_floor(34.6, 33.0, 44.6, 45.0, 2.8, col_chestnut, true);
    // Balcony overlooking Site A
    b.add_floor(45.5, 40.0, 48.5, 44.0, 2.8, col_chestnut, true);
    b.add_box_ex(48.4, 40.0, 2.8, 48.5, 44.0, 3.8, col_iron, true);
    // Interior stairs
    b.add_ramp(35.5, 32.0, 0.0, 38.5, 36.0, 2.8, col_chestnut);

    // 6. Bomb Site A, Graveyard & Sunken Pit
    b.add_box(53.6, 52.0, 0.0, 54.4, 60.0, 3.0, col_limestone);
    b.add_box(54.0, 59.6, 0.0, 62.0, 60.4, 3.0, col_limestone);
    // Sunken Pit floor & ramp
    b.add_floor(51.0, 41.0, 57.0, 47.0, -1.2, col_cobble, true);
    b.add_ramp(52.0, 46.5, -1.2, 56.0, 49.5, 0.0, col_cobble);
    // Bicycle cart cover
    b.add_box(47.3, 50.8, 0.0, 48.7, 53.2, 1.2, col_crate);
    // Default plant boxes
    b.add_box(49.4, 49.4, 0.0, 50.6, 50.6, 1.2, col_crate);
    b.add_box(49.4, 50.7, 0.0, 50.6, 51.9, 1.2, col_crate);
    b.add_box(49.4, 49.5, 1.2, 50.5, 50.5, 2.2, col_crate);

    // 7. Tuscan Cypress Trees
    for (cx, cy) in [(26.0, 8.0), (38.0, 8.0), (32.0, 62.0), (44.0, 62.0), (62.0, 48.0)] {
        b.add_cylinder(cx, cy, 0.0, 3.0, 0.2, 8, col_cypress_trunk, false);
        b.add_cylinder(cx, cy, 3.0, 4.5, 0.9, 10, col_cypress_leaf, false);
        b.add_cylinder(cx, cy, 7.5, 3.5, 0.5, 10, col_cypress_leaf, false);
    }

    let spawns = vec![
        SpawnPoint { x: 30.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 34.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 30.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 34.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 30.0, y: 56.0, z: 0.0, yaw: 270.0, team: 0 },
        SpawnPoint { x: 34.0, y: 56.0, z: 0.0, yaw: 270.0, team: 0 },
        SpawnPoint { x: 30.0, y: 52.0, z: 0.0, yaw: 270.0, team: 0 },
        SpawnPoint { x: 34.0, y: 52.0, z: 0.0, yaw: 270.0, team: 0 },
    ];

    let items = vec![
        ItemRow { id: 1, kind: "health".into(), x: 16.0, y: 14.0, z: 0.0 },
        ItemRow { id: 2, kind: "health".into(), x: 48.0, y: 14.0, z: 0.0 },
        ItemRow { id: 3, kind: "health".into(), x: 32.0, y: 32.0, z: 0.0 },
        ItemRow { id: 4, kind: "armour".into(), x: 18.0, y: 48.0, z: 0.0 },
        ItemRow { id: 5, kind: "armour".into(), x: 50.0, y: 48.0, z: 0.0 },
        ItemRow { id: 6, kind: "ammo_assault".into(), x: 14.0, y: 30.0, z: 0.0 },
        ItemRow { id: 7, kind: "ammo_assault".into(), x: 50.0, y: 30.0, z: 0.0 },
        ItemRow { id: 8, kind: "ammo_sniper".into(), x: 32.0, y: 54.0, z: 0.0 },
        ItemRow { id: 9, kind: "clips".into(), x: 32.0, y: 18.0, z: 0.0 },
        ItemRow { id: 10, kind: "grenade".into(), x: 20.0, y: 24.0, z: 0.0 },
    ];

    let bounds = WorldBounds {
        min: [4.0, 4.0, -1.5],
        max: [66.0, 66.0, 14.0],
        center: [35.0, 35.0, 6.0],
        extent: 62.0,
    };

    let triangles = b.render_positions.len() / 9;

    World3D {
        info,
        bounds,
        render_positions: b.render_positions,
        render_normals: b.render_normals,
        render_colors: b.render_colors,
        render_uvs: b.render_uvs,
        render_materials: b.render_materials,
        triangles,
        col_vertices: b.col_vertices,
        col_indices: b.col_indices,
        spawns,
        items,
        waterlevel: -100.0,
        windows: std::collections::HashMap::new(),
    }
}

/// Create the Middle East Sand Arena ("Mirage" / `hd_mirage`) 3D tournament arena.
/// Features:
/// 1. Palace Colonnade & Balcony: Elevated balcony (z=2.8m) with ornate colonnade arches overlooking Site A.
/// 2. Bomb Site A: Tetris boxes (z=1.2m, 2.4m), Triple box cover, Ticket booth structure, and circular plant marker.
/// 3. Middle & Sniper Nest: Window room (z=2.4m) overlooking Mid, Connector ramp connecting Mid to Site A, Catwalk (z=1.8m) to B Apartments, and sunken Underpass (z=-1.0m).
/// 4. Bomb Site B & Apartments: Two-story residential block (z=2.8m) with arched window opening onto Site B, central pillar, and market counter.
/// 5. SWAT / Barricade Van: Armored transport van on B site providing headshot angle cover.
/// 6. Spawns & Aesthetics: Palm trees, terracotta shade canopies, and decorative amphoras.
pub fn create_procedural_mirage_3d(info: MapInfo) -> World3D {
    let mut b = FacilityBuilder::new();

    let col_sand = [0.82, 0.76, 0.62];
    let col_sandstone = [0.76, 0.66, 0.52];
    let col_sandstone_dark = [0.65, 0.54, 0.40];
    let col_limestone = [0.88, 0.85, 0.78];
    let col_terracotta = [0.72, 0.35, 0.20];
    let col_wood = [0.42, 0.30, 0.18];
    let col_crate = [0.55, 0.42, 0.28];
    let col_iron = [0.20, 0.22, 0.24];
    let col_palm_trunk = [0.36, 0.26, 0.18];
    let col_palm_frond = [0.18, 0.42, 0.16];
    let col_canopy = [0.78, 0.32, 0.28];

    // 1. Terrain Ground (4.0..66.0, 4.0..66.0)
    b.add_floor(4.0, 4.0, 66.0, 66.0, 0.0, col_sand, true);

    // High Perimeter Sandstone Walls (height 10.0)
    b.add_quad([4.0, 4.0, 0.0], [66.0, 4.0, 0.0], [66.0, 4.0, 10.0], [4.0, 4.0, 10.0], col_sandstone, true);
    b.add_quad([66.0, 66.0, 0.0], [4.0, 66.0, 0.0], [4.0, 66.0, 10.0], [66.0, 66.0, 10.0], col_sandstone, true);
    b.add_quad([66.0, 4.0, 0.0], [66.0, 66.0, 0.0], [66.0, 66.0, 10.0], [66.0, 4.0, 10.0], col_sandstone_dark, true);
    b.add_quad([4.0, 66.0, 0.0], [4.0, 4.0, 0.0], [4.0, 4.0, 10.0], [4.0, 66.0, 10.0], col_sandstone_dark, true);

    // Decorative blue terracotta trim
    b.add_box_ex(4.0, 4.2, 9.7, 66.0, 4.6, 10.1, [0.20, 0.45, 0.65], false);
    b.add_box_ex(4.0, 65.4, 9.7, 66.0, 65.8, 10.1, [0.20, 0.45, 0.65], false);

    // 2. Palace Colonnade & Balcony (Site A)
    // Palace main outer block
    b.add_box(44.0, 40.0, 0.0, 64.0, 62.0, 8.5, col_sandstone);
    // Palace elevated balcony overlooking Site A
    b.add_floor(44.0, 36.0, 56.0, 40.0, 2.8, col_sandstone_dark, true);
    // Balcony railing
    b.add_box_ex(44.0, 35.9, 2.8, 56.0, 36.1, 3.8, col_wood, true);
    // Columns
    b.add_cylinder(46.0, 36.0, 0.0, 2.8, 0.4, 12, col_limestone, true);
    b.add_cylinder(54.0, 36.0, 0.0, 2.8, 0.4, 12, col_limestone, true);

    // 3. Bomb Site A Structures & Cover
    // Tetris boxes at (45.0, 28.0)
    b.add_box(43.8, 26.8, 0.0, 46.2, 29.2, 1.2, col_crate);
    b.add_box(44.4, 27.4, 1.2, 45.6, 28.6, 2.4, col_crate);
    // Triple boxes at (53.0, 26.0)
    b.add_box(52.0, 24.8, 0.0, 54.0, 26.8, 1.2, col_crate);
    b.add_box(52.0, 26.8, 0.0, 54.0, 28.8, 1.2, col_crate);
    b.add_box(52.2, 25.4, 1.2, 53.8, 27.6, 2.2, col_crate);
    // Ticket booth at (38.0, 24.0)
    b.add_box(36.8, 22.8, 0.0, 39.2, 25.2, 2.6, col_sandstone);
    b.add_ramp(36.8, 25.2, 0.0, 39.2, 28.0, 1.4, col_sandstone);
    // Site A plant disc marker
    b.add_cylinder(48.0, 30.0, 0.0, 0.08, 2.5, 16, col_terracotta, false);

    // 4. Middle, Window Room, Connector & Catwalk
    // Mid dividing walls
    b.add_box(40.0, 14.0, 0.0, 41.2, 42.0, 7.5, col_sandstone);
    // Sniper Nest / Window Room at (34.0, 48.0)
    b.add_floor(30.0, 46.0, 40.0, 54.0, 2.4, col_wood, true);
    b.add_box(30.0, 45.8, 0.0, 40.0, 46.2, 2.4, col_sandstone); // front wall under window
    b.add_box(30.0, 45.8, 3.8, 40.0, 46.2, 7.0, col_sandstone); // wall above window
    b.add_box(30.0, 45.8, 2.4, 33.0, 46.2, 3.8, col_sandstone); // window left jamb
    b.add_box(37.0, 45.8, 2.4, 40.0, 46.2, 3.8, col_sandstone); // window right jamb
    // Connector ramp linking Mid to Site A
    b.add_ramp(38.0, 36.0, 0.0, 44.0, 40.0, 1.4, col_sandstone);
    // Catwalk elevated walkway
    b.add_floor(22.0, 34.0, 30.0, 36.0, 1.8, col_iron, true);
    b.add_box_ex(22.0, 33.9, 1.8, 30.0, 34.1, 2.6, col_iron, true);
    // Underpass sunken floor
    b.add_floor(30.0, 26.0, 34.0, 36.0, -1.0, col_sandstone_dark, true);
    b.add_ramp(30.0, 24.0, 0.0, 34.0, 26.0, -1.0, col_sandstone_dark);

    // 5. Bomb Site B, Apartments, Market & Van
    // B Apartments residential block
    b.add_box(8.0, 26.0, 0.0, 22.0, 42.0, 8.0, col_sandstone_dark);
    b.add_floor(8.0, 26.0, 22.0, 42.0, 2.8, col_wood, true);
    // Arched window sill overlooking Site B
    b.add_box_ex(14.0, 41.8, 2.8, 18.0, 42.2, 3.2, col_limestone, true);
    // Site B raised platform
    b.add_floor(10.0, 44.0, 24.0, 56.0, 0.8, col_sandstone, true);
    b.add_cylinder(17.0, 50.0, 0.8, 4.0, 1.2, 12, col_limestone, true); // Site B central pillar
    // Barricade Van at (14.0, 44.0)
    b.add_box(12.4, 42.8, 0.0, 15.6, 45.2, 0.7, col_iron);
    b.add_box(12.6, 43.0, 0.7, 15.4, 45.0, 1.8, [0.38, 0.42, 0.36]); // Van body
    // Market building south of Site B
    b.add_box(20.0, 44.0, 0.0, 28.0, 56.0, 7.5, col_sandstone);

    // 6. Aesthetics: Palm Trees, Sun Canopy & Urns
    for (cx, cy) in [(28.0, 10.0), (42.0, 10.0), (28.0, 60.0), (42.0, 60.0)] {
        b.add_cylinder(cx, cy, 0.0, 3.5, 0.25, 8, col_palm_trunk, false);
        b.add_cylinder(cx, cy, 3.5, 1.2, 1.6, 8, col_palm_frond, false);
        b.add_cylinder(cx, cy, 4.5, 0.8, 1.2, 8, col_palm_frond, false);
    }
    // Sun canopy at T Spawn
    b.add_quad([30.0, 8.0, 3.2], [36.0, 8.0, 3.2], [36.0, 14.0, 3.6], [30.0, 14.0, 3.6], col_canopy, false);
    // Urns
    for (ux, uy) in [(46.0, 30.0), (54.0, 32.0), (22.0, 42.0), (12.0, 48.0)] {
        b.add_cylinder(ux, uy, 0.0, 0.9, 0.35, 8, col_terracotta, false);
    }

    let spawns = vec![
        SpawnPoint { x: 32.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 36.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 32.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 36.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 32.0, y: 58.0, z: 0.0, yaw: 270.0, team: 0 },
        SpawnPoint { x: 36.0, y: 58.0, z: 0.0, yaw: 270.0, team: 0 },
        SpawnPoint { x: 32.0, y: 54.0, z: 0.0, yaw: 270.0, team: 0 },
        SpawnPoint { x: 36.0, y: 54.0, z: 0.0, yaw: 270.0, team: 0 },
    ];

    let items = vec![
        ItemRow { id: 1, kind: "health".into(), x: 18.0, y: 14.0, z: 0.0 },
        ItemRow { id: 2, kind: "health".into(), x: 50.0, y: 14.0, z: 0.0 },
        ItemRow { id: 3, kind: "health".into(), x: 34.0, y: 34.0, z: 0.0 },
        ItemRow { id: 4, kind: "armour".into(), x: 16.0, y: 48.0, z: 0.8 },
        ItemRow { id: 5, kind: "armour".into(), x: 50.0, y: 48.0, z: 2.8 },
        ItemRow { id: 6, kind: "ammo_assault".into(), x: 14.0, y: 32.0, z: 2.8 },
        ItemRow { id: 7, kind: "ammo_assault".into(), x: 48.0, y: 32.0, z: 0.0 },
        ItemRow { id: 8, kind: "ammo_sniper".into(), x: 34.0, y: 50.0, z: 2.4 },
        ItemRow { id: 9, kind: "clips".into(), x: 34.0, y: 18.0, z: 0.0 },
        ItemRow { id: 10, kind: "grenade".into(), x: 26.0, y: 36.0, z: 1.8 },
    ];

    let bounds = WorldBounds {
        min: [4.0, 4.0, -2.0],
        max: [66.0, 66.0, 14.0],
        center: [35.0, 35.0, 6.0],
        extent: 62.0,
    };

    let triangles = b.render_positions.len() / 9;

    World3D {
        info,
        bounds,
        render_positions: b.render_positions,
        render_normals: b.render_normals,
        render_colors: b.render_colors,
        render_uvs: b.render_uvs,
        render_materials: b.render_materials,
        triangles,
        col_vertices: b.col_vertices,
        col_indices: b.col_indices,
        spawns,
        items,
        waterlevel: -100.0,
        windows: std::collections::HashMap::new(),
    }
}

/// Create the Nuclear Containment Facility ("Nuke" / `hd_nuke`) 3D tournament arena.
/// Features:
/// 1. True Multi-Level Verticality: Upper Reactor Hall (Site A, z=0.0m) directly above Lower Reactor Silo (Site B, z=-4.5m).
/// 2. Bomb Site A: Overhead Rafters & Catwalks (z=3.8m), Yellow Gantry Crane (z=6.8m), Hut structure, and Vent hatch.
/// 3. Bomb Site B: Massive cylindrical nuclear reactor core with glowing ring, coolant containment tanks, and decon chamber.
/// 4. Connectors & Ramps: Vertical ventilation shaft linking A and B, Ramp room connecting ground level down to Site B.
/// 5. Outside Yard: Outer Silo tower, shipping containers, and garage.
pub fn create_procedural_nuke_3d(info: MapInfo) -> World3D {
    let mut b = FacilityBuilder::new();

    let col_concrete_floor = [0.64, 0.65, 0.66];
    let col_concrete_wall = [0.72, 0.74, 0.76];
    let col_hazard_yellow = [0.86, 0.75, 0.12];
    let col_steel_grate = [0.42, 0.44, 0.46];
    let col_reactor_glow = [0.12, 0.85, 0.95];
    let col_reactor_hull = [0.32, 0.35, 0.38];
    let col_coolant = [0.20, 0.42, 0.72];
    let col_container_red = [0.72, 0.20, 0.16];
    let col_container_blue = [0.18, 0.32, 0.62];

    // 1. Terrain Ground (4.0..66.0, 4.0..66.0, z=0.0)
    b.add_floor(4.0, 4.0, 66.0, 66.0, 0.0, col_concrete_floor, true);

    // High Perimeter Concrete Security Walls (height 12.0)
    b.add_quad([4.0, 4.0, 0.0], [66.0, 4.0, 0.0], [66.0, 4.0, 12.0], [4.0, 4.0, 12.0], col_concrete_wall, true);
    b.add_quad([66.0, 66.0, 0.0], [4.0, 66.0, 0.0], [4.0, 66.0, 12.0], [66.0, 66.0, 12.0], col_concrete_wall, true);
    b.add_quad([66.0, 4.0, 0.0], [66.0, 66.0, 0.0], [66.0, 66.0, 12.0], [66.0, 4.0, 12.0], col_concrete_wall, true);
    b.add_quad([4.0, 66.0, 0.0], [4.0, 4.0, 0.0], [4.0, 4.0, 12.0], [4.0, 66.0, 12.0], col_concrete_wall, true);

    // Hazard yellow perimeter trim
    b.add_box_ex(4.0, 4.2, 11.6, 66.0, 4.6, 12.2, col_hazard_yellow, false);
    b.add_box_ex(4.0, 65.4, 11.6, 66.0, 65.8, 12.2, col_hazard_yellow, false);

    // 2. Bomb Site A (Upper Reactor Hall) at Z=0.0m
    // Outer containment building walls
    b.add_box(22.0, 28.0, 0.0, 50.0, 29.2, 8.0, col_concrete_wall);
    b.add_box(22.0, 50.8, 0.0, 50.0, 52.0, 8.0, col_concrete_wall);
    b.add_box(21.4, 28.0, 0.0, 22.6, 52.0, 8.0, col_concrete_wall);
    b.add_box(49.4, 28.0, 0.0, 50.6, 52.0, 8.0, col_concrete_wall);

    // Site A Hut structure
    b.add_box(24.0, 34.0, 0.0, 28.0, 40.0, 2.8, col_concrete_wall);
    b.add_box_ex(24.0, 34.0, 2.8, 28.0, 40.0, 3.0, col_hazard_yellow, true);

    // Overhead Rafters / Catwalks at Z=3.8m
    b.add_floor(47.0, 30.0, 49.0, 50.0, 3.8, col_steel_grate, true);
    b.add_floor(25.0, 48.0, 47.0, 50.0, 3.8, col_steel_grate, true);
    b.add_box_ex(46.9, 30.0, 3.8, 47.1, 50.0, 4.8, col_hazard_yellow, true);

    // Yellow Gantry Crane beam across ceiling (z=6.8m)
    b.add_box_ex(23.0, 39.2, 6.8, 49.0, 40.8, 7.6, col_hazard_yellow, false);

    // Site A Plant disc marker
    b.add_cylinder(36.0, 40.0, 0.0, 0.08, 2.5, 16, col_hazard_yellow, false);

    // 3. Bomb Site B (Lower Reactor Silo) at Z=-4.5m
    // Subterranean excavation pit floor
    b.add_floor(23.0, 29.0, 49.0, 51.0, -4.5, col_concrete_floor, true);

    // Central Reactor Core Cylinder
    b.add_cylinder(36.0, 40.0, -4.5, 4.5, 2.6, 16, col_reactor_hull, true);
    // Glowing core ring
    b.add_cylinder(36.0, 40.0, -2.5, 0.6, 2.7, 16, col_reactor_glow, false);

    // Coolant tanks
    b.add_cylinder(26.0, 44.0, -4.5, 3.0, 1.4, 12, col_coolant, true);
    b.add_cylinder(46.0, 44.0, -4.5, 3.0, 1.4, 12, col_coolant, true);

    // Site B Plant disc marker
    b.add_cylinder(36.0, 40.0, -4.5, 0.08, 3.8, 16, col_hazard_yellow, false);

    // 4. Connectors: Vents & Ramp
    // Vertical vent chute from z=0 down to z=-4.5
    b.add_box(30.4, 35.4, -4.5, 31.6, 36.6, 0.0, col_steel_grate);

    // Sloped Ramp Room (from y=20 down to y=28)
    b.add_ramp(18.0, 20.0, 0.0, 22.0, 28.0, -4.5, col_concrete_floor);

    // 5. Outside Yard: Silo Tower & Shipping Containers
    b.add_cylinder(16.0, 20.0, 0.0, 5.0, 2.4, 16, col_concrete_wall, true);
    b.add_cylinder(16.0, 20.0, 5.0, 0.6, 2.2, 16, col_hazard_yellow, true);

    // Containers
    b.add_box(14.6, 33.0, 0.0, 17.4, 39.0, 2.6, col_container_red);
    b.add_box(14.6, 41.0, 0.0, 17.4, 47.0, 2.6, col_container_blue);

    // Garage
    b.add_box(52.0, 40.0, 0.0, 60.0, 52.0, 5.0, col_concrete_wall);

    let spawns = vec![
        SpawnPoint { x: 32.0, y: 8.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 36.0, y: 8.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 32.0, y: 12.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 36.0, y: 12.0, z: 0.0, yaw: 90.0, team: 1 },
        SpawnPoint { x: 32.0, y: 62.0, z: 0.0, yaw: 270.0, team: 0 },
        SpawnPoint { x: 36.0, y: 62.0, z: 0.0, yaw: 270.0, team: 0 },
        SpawnPoint { x: 32.0, y: 58.0, z: 0.0, yaw: 270.0, team: 0 },
        SpawnPoint { x: 36.0, y: 58.0, z: 0.0, yaw: 270.0, team: 0 },
    ];

    let items = vec![
        ItemRow { id: 1, kind: "health".into(), x: 18.0, y: 18.0, z: 0.0 },
        ItemRow { id: 2, kind: "health".into(), x: 52.0, y: 18.0, z: 0.0 },
        ItemRow { id: 3, kind: "health".into(), x: 36.0, y: 40.0, z: -4.5 },
        ItemRow { id: 4, kind: "armour".into(), x: 36.0, y: 40.0, z: 0.0 },
        ItemRow { id: 5, kind: "armour".into(), x: 26.0, y: 36.0, z: 0.0 },
        ItemRow { id: 6, kind: "ammo_assault".into(), x: 18.0, y: 44.0, z: 0.0 },
        ItemRow { id: 7, kind: "ammo_assault".into(), x: 42.0, y: 54.0, z: 3.8 },
        ItemRow { id: 8, kind: "ammo_sniper".into(), x: 16.0, y: 20.0, z: 5.0 },
        ItemRow { id: 9, kind: "clips".into(), x: 32.0, y: 22.0, z: 0.0 },
        ItemRow { id: 10, kind: "grenade".into(), x: 20.0, y: 28.0, z: -2.2 },
    ];

    let bounds = WorldBounds {
        min: [4.0, 4.0, -5.5],
        max: [66.0, 66.0, 14.0],
        center: [35.0, 35.0, 4.0],
        extent: 62.0,
    };

    let triangles = b.render_positions.len() / 9;

    World3D {
        info,
        bounds,
        render_positions: b.render_positions,
        render_normals: b.render_normals,
        render_colors: b.render_colors,
        render_uvs: b.render_uvs,
        render_materials: b.render_materials,
        triangles,
        col_vertices: b.col_vertices,
        col_indices: b.col_indices,
        spawns,
        items,
        waterlevel: -100.0,
        windows: std::collections::HashMap::new(),
    }
}

const HD_NUKE_GLB_BYTES: &[u8] = include_bytes!("../../../backend/modules/hassault/maps/hd_nuke.glb");
const HD_MIRAGE_GLB_BYTES: &[u8] = include_bytes!("../../../backend/modules/hassault/maps/hd_mirage.glb");
const HD_INFERNO_GLB_BYTES: &[u8] = include_bytes!("../../../backend/modules/hassault/maps/hd_inferno.glb");
const HD_DUST2_GLB_BYTES: &[u8] = include_bytes!("../../../backend/modules/hassault/maps/hd_dust2.glb");
const HD_OFFICE_GLB_BYTES: &[u8] = include_bytes!("../../../backend/modules/hassault/maps/hd_office.glb");
const HD_ASSAULT_GLB_BYTES: &[u8] = include_bytes!("../../../backend/modules/hassault/maps/hd_assault.glb");
const HD_BANK_GLB_BYTES: &[u8] = include_bytes!("../../../backend/modules/hassault/maps/hd_bank.glb");
const HD_JUNKFLEA_GLB_BYTES: &[u8] = include_bytes!("../../../backend/modules/hassault/maps/hd_junkflea.glb");
const HD_FACILITY_GLB_BYTES: &[u8] = include_bytes!("../../../backend/modules/hassault/maps/hd_facility.glb");

fn local_node_matrix(node: &gltf::Node) -> Mat4 {
    match node.transform() {
        gltf::scene::Transform::Matrix { matrix } => Mat4::from_cols_array_2d(&matrix),
        gltf::scene::Transform::Decomposed {
            translation,
            rotation,
            scale,
        } => Mat4::from_scale_rotation_translation(
            Vec3::from_array(scale),
            Quat::from_array(rotation),
            Vec3::from_array(translation),
        ),
    }
}

fn walk_glb_node(
    node: &gltf::Node,
    parent: Mat4,
    blob: Option<&[u8]>,
    render_positions: &mut Vec<f32>,
    render_normals: &mut Vec<f32>,
    render_colors: &mut Vec<f32>,
    render_uvs: &mut Vec<f32>,
    render_materials: &mut Vec<f32>,
    col_vertices: &mut Vec<Point<Real>>,
    col_indices: &mut Vec<[u32; 3]>,
    windows: &mut std::collections::HashMap<String, BreakableWindow>,
) {
    let world = parent * local_node_matrix(node);
    let normal_matrix = world.inverse().transpose();

    if let Some(mesh) = node.mesh() {
        let node_name = node.name().unwrap_or("");
        let is_win = is_breakable_window_node(node_name);
        let is_non_collider = !is_win && (node_name.contains("Glass")
            || node_name.contains("Window")
            || node_name.contains("Lamp")
            || node_name.contains("Glow")
            || node_name.contains("Decal")
            || node_name.contains("Stripe")
            || node_name.contains("Sign")
            || node_name.contains("Text")
            || node_name.contains("Turn")
            || node_name.contains("Siren")
            || node_name.contains("Taillight")
            || node_name.contains("Headlight")
            || node_name.contains("Arm")
            || node_name.contains("Vent")
            || node_name.contains("Rim")
            || node_name.contains("Hub")
            || node_name.contains("Latch")
            || node_name.contains("Divide")
            || node_name.contains("Cap")
            || node_name.contains("Hood")
            || node_name.contains("Mullion")
            || node_name.contains("Camera")
            || node_name.contains("Light")
            || node_name.contains("Truss")
            || node_name.contains("Detail")
            || node_name.contains("Pipe")
            || node_name.contains("Conduit")
            || node_name.contains("Wire")
            || node_name.contains("Debris")
            || node_name.contains("Stain")
            || node_name.contains("Crack")
            || node_name.contains("Drain")
            || node_name.contains("Manhole")
            || node_name.contains("Cable")
            || node_name.contains("Joint")
            || node_name.contains("Rung")
            || node_name.contains("Flange")
            || node_name.contains("Web")
            || node_name.contains("Rail_")
            || node_name.contains("Tie_")
            || node_name.contains("Gusset")
            || node_name.contains("Purlin")
            || node_name.contains("Louver")
            || node_name.contains("Hose")
            || node_name.contains("Vise")
            || node_name.contains("Blind")
            || node_name.contains("Antenna")
            || node_name.contains("Blade")
            || node_name.contains("Propane")
            || node_name.contains("Spool")
            || node_name.contains("Cushion")
            || node_name.contains("Skylight")
            || node_name.contains("Foliage")
            || node_name.contains("Plant")
            || node_name.contains("Chandelier")
            || node_name.contains("Stanchion")
            || node_name.contains("Flute")
            || node_name.contains("Globe")
            || node_name.contains("Clock")
            || node_name.contains("Plaque")
            || node_name.contains("Urn")
            || node_name.contains("Hatch")
            || node_name.contains("Ingot")
            || node_name.contains("NonCol")
            || node_name.contains("Rope")
            || node_name.contains("Lug")
            || node_name.contains("Dial")
            || node_name.contains("Spoke")
            || node_name.contains("Caster")
            || node_name.contains("Cart_Rail")
            || node_name.contains("Trophy")
            || node_name.contains("Frame")
            || node_name.contains("Painting")
            || node_name.contains("Sconce")
            || node_name.contains("Screen")
            || node_name.contains("Keyhole")
            || node_name.contains("Dunnage")
            || node_name.contains("Stringer")
            || node_name.contains("Slat")
            || node_name.contains("Strap")
            || node_name.contains("Buckle")
            || node_name.contains("Rosette")
            || node_name.contains("Medallion")
            || node_name.contains("Inlay")
            || node_name.contains("Blotter")
            || node_name.contains("Keyboard")
            || node_name.contains("Mouse")
            || node_name.contains("Speaker")
            || node_name.contains("Mic")
            || node_name.contains("Nameplate")
            || node_name.contains("Wicket")
            || node_name.contains("Spindle")
            || node_name.contains("Winch")
            || node_name.contains("Lightbar")
            || node_name.contains("Cup")
            || node_name.contains("Mug")
            || node_name.contains("Spigot")
            || node_name.contains("Shade")
            || node_name.contains("Bulb")
            || node_name.contains("Chain")
            || node_name.contains("Handwheel")
            || node_name.contains("Bushing")
            || node_name.contains("Bolt")
            || node_name.contains("Cart_"));

        let is_invisible = node_name.contains("Invisible") || node_name.contains("ColOnly");
        let mut win_col_vertices = Vec::new();
        let mut win_col_indices = Vec::new();

        for prim in mesh.primitives() {
            let mat_name = prim.material().name().unwrap_or("");
            let pbr = prim.material().pbr_metallic_roughness();
            let base_color = pbr.base_color_factor();
            let emissive = prim.material().emissive_factor();
            let color = if emissive != [0.0, 0.0, 0.0] {
                [emissive[0], emissive[1], emissive[2]]
            } else {
                [base_color[0], base_color[1], base_color[2]]
            };

            let is_collider = !is_non_collider
                && !mat_name.contains("Glass")
                && !mat_name.contains("Glow")
                && !mat_name.contains("Lamp")
                && !mat_name.contains("Indicator")
                && !mat_name.contains("Taillight");

            let reader = prim.reader(|b| match b.source() {
                gltf::buffer::Source::Bin => blob,
                gltf::buffer::Source::Uri(_) => None,
            });

            let Some(positions) = reader.read_positions() else { continue; };
            let positions: Vec<[f32; 3]> = positions.collect();
            let normals: Vec<[f32; 3]> = reader
                .read_normals()
                .map(|n| n.collect())
                .unwrap_or_else(|| vec![[0.0, 1.0, 0.0]; positions.len()]);
            // The generated GLBs carry no TEXCOORD_0. Falling back to (0, 0) sampled
            // one texel for every triangle in the map — each surface one flat colour,
            // and that colour the corner of its tile (a concrete seam: dust2's
            // sandstone drew near black). Absent UVs are projected from the world
            // instead, `glb-surfaces.ts` `planarUv` on the browser side.
            let uvs: Option<Vec<[f32; 2]>> = reader.read_tex_coords(0).map(|t| t.into_f32().collect());
            let mat_kind = MaterialKind::from_name(mat_name);
            let mat_id = mat_kind as u8 as f32;
            let indices: Vec<u32> = match reader.read_indices() {
                Some(read) => read.into_u32().collect(),
                None => (0..positions.len() as u32).collect(),
            };

            for chunk in indices.chunks_exact(3) {
                let (i0, i1, i2) = (chunk[0] as usize, chunk[1] as usize, chunk[2] as usize);
                if i0 >= positions.len() || i1 >= positions.len() || i2 >= positions.len() {
                    continue;
                }

                // glTF coordinates: X is East, Y is Elevation (Up), Z is -North
                let p0 = world.transform_point3(Vec3::from_array(positions[i0]));
                let p1 = world.transform_point3(Vec3::from_array(positions[i1]));
                let p2 = world.transform_point3(Vec3::from_array(positions[i2]));

                let n0 = normal_matrix
                    .transform_vector3(Vec3::from_array(normals.get(i0).copied().unwrap_or([0.0, 1.0, 0.0])))
                    .normalize_or_zero();
                let n1 = normal_matrix
                    .transform_vector3(Vec3::from_array(normals.get(i1).copied().unwrap_or([0.0, 1.0, 0.0])))
                    .normalize_or_zero();
                let n2 = normal_matrix
                    .transform_vector3(Vec3::from_array(normals.get(i2).copied().unwrap_or([0.0, 1.0, 0.0])))
                    .normalize_or_zero();

                let [uv0, uv1, uv2] = match &uvs {
                    Some(uvs) => [
                        uvs.get(i0).copied().unwrap_or([0.0, 0.0]),
                        uvs.get(i1).copied().unwrap_or([0.0, 0.0]),
                        uvs.get(i2).copied().unwrap_or([0.0, 0.0]),
                    ],
                    None => {
                        let r = [
                            [p0.x, p0.y, -p0.z],
                            [p1.x, p1.y, -p1.z],
                            [p2.x, p2.y, -p2.z],
                        ];
                        let face = face_normal(r[0], r[1], r[2]);
                        let scale = mat_kind.tile_scale();
                        r.map(|p| glb_planar_uv(p, face, scale))
                    }
                };

                if !is_invisible {
                    // Render space: x = gltf_x, y = gltf_y (elevation), z = -gltf_z (North)
                    // Winding order inverted: (i0, i2, i1) because of z negation
                    let r_p0 = [p0.x, p0.y, -p0.z];
                    let r_p1 = [p1.x, p1.y, -p1.z];
                    let r_p2 = [p2.x, p2.y, -p2.z];

                    let r_n0 = [n0.x, n0.y, -n0.z];
                    let r_n1 = [n1.x, n1.y, -n1.z];
                    let r_n2 = [n2.x, n2.y, -n2.z];

                    render_positions.extend_from_slice(&r_p0);
                    render_positions.extend_from_slice(&r_p2);
                    render_positions.extend_from_slice(&r_p1);

                    render_normals.extend_from_slice(&r_n0);
                    render_normals.extend_from_slice(&r_n2);
                    render_normals.extend_from_slice(&r_n1);

                    render_colors.extend_from_slice(&color);
                    render_colors.extend_from_slice(&color);
                    render_colors.extend_from_slice(&color);

                    render_uvs.extend_from_slice(&uv0);
                    render_uvs.extend_from_slice(&uv2);
                    render_uvs.extend_from_slice(&uv1);

                    render_materials.extend_from_slice(&[mat_id, mat_id, mat_id]);
                }

                if is_win {
                    let g_p0 = [p0.x, -p0.z, p0.y];
                    let g_p1 = [p1.x, -p1.z, p1.y];
                    let g_p2 = [p2.x, -p2.z, p2.y];

                    let base_idx = win_col_vertices.len() as u32;
                    win_col_vertices.push(Point::new(g_p0[0], g_p0[1], g_p0[2]));
                    win_col_vertices.push(Point::new(g_p2[0], g_p2[1], g_p2[2]));
                    win_col_vertices.push(Point::new(g_p1[0], g_p1[1], g_p1[2]));
                    win_col_indices.push([base_idx, base_idx + 1, base_idx + 2]);
                } else if is_collider {
                    // Game/Rapier physics coordinates: x = gltf_x, y = -gltf_z (North), z = gltf_y (Elevation)
                    let g_p0 = [p0.x, -p0.z, p0.y];
                    let g_p1 = [p1.x, -p1.z, p1.y];
                    let g_p2 = [p2.x, -p2.z, p2.y];

                    let base_idx = col_vertices.len() as u32;
                    col_vertices.push(Point::new(g_p0[0], g_p0[1], g_p0[2]));
                    col_vertices.push(Point::new(g_p2[0], g_p2[1], g_p2[2]));
                    col_vertices.push(Point::new(g_p1[0], g_p1[1], g_p1[2]));
                    col_indices.push([base_idx, base_idx + 1, base_idx + 2]);
                }
            }
        }

        if is_win && !win_col_vertices.is_empty() {
            let mut center = [0.0, 0.0, 0.0];
            for v in &win_col_vertices {
                center[0] += v.x;
                center[1] += v.y;
                center[2] += v.z;
            }
            let count = win_col_vertices.len() as f32;
            center[0] /= count;
            center[1] /= count;
            center[2] /= count;

            windows.insert(
                node_name.to_string(),
                BreakableWindow {
                    id: node_name.to_string(),
                    name: node_name.to_string(),
                    center,
                    normal: [0.0, 1.0, 0.0],
                    shattered: false,
                    col_vertices: win_col_vertices,
                    col_indices: win_col_indices,
                },
            );
        }
    }

    for child in node.children() {
        walk_glb_node(
            &child,
            world,
            blob,
            render_positions,
            render_normals,
            render_colors,
            render_uvs,
            render_materials,
            col_vertices,
            col_indices,
            windows,
        );
    }
}

pub fn load_world_3d_from_glb(bytes: &[u8], info: MapInfo) -> Result<World3D, String> {
    let normalised = crate::character::normalise_glb(bytes)?;
    let gltf = gltf::Gltf::from_slice(&normalised).map_err(|e| format!("Map GLB parse error: {e}"))?;
    let blob = gltf.blob.as_deref();
    let document = &gltf.document;

    let mut render_positions = Vec::new();
    let mut render_normals = Vec::new();
    let mut render_colors = Vec::new();
    let mut render_uvs = Vec::new();
    let mut render_materials = Vec::new();
    let mut col_vertices = Vec::new();
    let mut col_indices = Vec::new();
    let mut windows = std::collections::HashMap::new();

    for scene in document.scenes() {
        for node in scene.nodes() {
            walk_glb_node(
                &node,
                Mat4::IDENTITY,
                blob,
                &mut render_positions,
                &mut render_normals,
                &mut render_colors,
                &mut render_uvs,
                &mut render_materials,
                &mut col_vertices,
                &mut col_indices,
                &mut windows,
            );
        }
    }

    let triangles = render_positions.len() / 9;

    let (spawns, items) = if info.name == "hd_facility" {
        (
            vec![
                SpawnPoint { x: 10.0, y: 8.0, z: 0.0, yaw: 0.0, team: 0 },
                SpawnPoint { x: 32.0, y: 4.0, z: 6.0, yaw: 0.0, team: 0 },
                SpawnPoint { x: 54.0, y: 8.0, z: 0.0, yaw: 315.0, team: 0 },
                SpawnPoint { x: 32.0, y: 10.0, z: 0.0, yaw: 0.0, team: 0 },
                SpawnPoint { x: 10.0, y: 56.0, z: 0.0, yaw: 180.0, team: 1 },
                SpawnPoint { x: 32.0, y: 60.0, z: 6.0, yaw: 180.0, team: 1 },
                SpawnPoint { x: 54.0, y: 56.0, z: 0.0, yaw: 225.0, team: 1 },
                SpawnPoint { x: 32.0, y: 54.0, z: 0.0, yaw: 180.0, team: 1 },
            ],
            vec![
                ItemRow { id: 1, kind: "ammo_assault".into(), x: 32.0, y: 32.0, z: 6.0 },
                ItemRow { id: 2, kind: "armour".into(), x: 32.0, y: 32.0, z: -5.0 },
                ItemRow { id: 3, kind: "health".into(), x: 8.0, y: 32.0, z: 0.0 },
                ItemRow { id: 4, kind: "health".into(), x: 56.0, y: 32.0, z: 0.0 },
                ItemRow { id: 5, kind: "ammo_sniper".into(), x: 4.0, y: 4.0, z: 6.0 },
                ItemRow { id: 6, kind: "ammo_sniper".into(), x: 60.0, y: 60.0, z: 6.0 },
            ],
        )
    } else if info.name == "hd_junkflea" {
        (
            vec![
                SpawnPoint { x: 16.0, y: 12.0, z: 0.0, yaw: 0.0, team: 0 },
                SpawnPoint { x: 48.0, y: 12.0, z: 0.0, yaw: 0.0, team: 0 },
                SpawnPoint { x: 32.0, y: 10.0, z: 0.0, yaw: 0.0, team: 0 },
                SpawnPoint { x: 32.0, y: 15.0, z: 0.0, yaw: 0.0, team: 0 },
                SpawnPoint { x: 16.0, y: 52.0, z: 0.0, yaw: 180.0, team: 1 },
                SpawnPoint { x: 48.0, y: 52.0, z: 0.0, yaw: 180.0, team: 1 },
                SpawnPoint { x: 32.0, y: 54.0, z: 0.0, yaw: 180.0, team: 1 },
                SpawnPoint { x: 32.0, y: 49.0, z: 0.0, yaw: 180.0, team: 1 },
            ],
            vec![
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
            ],
        )
    } else if info.name == "hd_bank" {
        (
            vec![
                SpawnPoint { x: 12.0, y: 8.0, z: 0.0, yaw: 0.0, team: 0 },
                SpawnPoint { x: 24.0, y: 8.0, z: 0.0, yaw: 0.0, team: 0 },
                SpawnPoint { x: 36.0, y: 8.0, z: 0.0, yaw: 0.0, team: 0 },
                SpawnPoint { x: 48.0, y: 8.0, z: 0.0, yaw: 0.0, team: 0 },
                SpawnPoint { x: 10.0, y: 55.0, z: 0.0, yaw: 180.0, team: 1 },
                SpawnPoint { x: 20.0, y: 55.0, z: 0.0, yaw: 180.0, team: 1 },
                SpawnPoint { x: 32.0, y: 55.0, z: 0.0, yaw: 180.0, team: 1 },
                SpawnPoint { x: 36.0, y: 51.0, z: 0.0, yaw: 180.0, team: 1 },
            ],
            vec![
                ItemRow { id: 1, kind: "health".into(), x: 10.0, y: 14.0, z: 0.0 },
                ItemRow { id: 2, kind: "health".into(), x: 54.0, y: 14.0, z: 0.0 },
                ItemRow { id: 3, kind: "health".into(), x: 10.0, y: 51.0, z: 0.0 },
                ItemRow { id: 4, kind: "armour".into(), x: 32.0, y: 28.0, z: 0.0 },
                ItemRow { id: 5, kind: "armour".into(), x: 52.0, y: 53.0, z: 0.0 },
                ItemRow { id: 6, kind: "ammo_assault".into(), x: 20.0, y: 11.0, z: 0.0 },
                ItemRow { id: 7, kind: "ammo_assault".into(), x: 44.0, y: 12.0, z: 0.0 },
                ItemRow { id: 8, kind: "ammo_sniper".into(), x: 16.0, y: 40.0, z: 5.0 },
                ItemRow { id: 9, kind: "clips".into(), x: 32.0, y: 22.0, z: 0.0 },
                ItemRow { id: 10, kind: "grenade".into(), x: 52.0, y: 28.0, z: 0.0 },
            ],
        )
    } else if info.name == "hd_dust2" {
        (
            vec![
                SpawnPoint { x: 30.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 34.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 30.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 34.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 34.0, y: 60.0, z: 0.0, yaw: 270.0, team: 0 },
                SpawnPoint { x: 38.0, y: 60.0, z: 0.0, yaw: 270.0, team: 0 },
                SpawnPoint { x: 34.0, y: 56.0, z: 0.0, yaw: 270.0, team: 0 },
                SpawnPoint { x: 38.0, y: 56.0, z: 0.0, yaw: 270.0, team: 0 },
            ],
            vec![
                ItemRow { id: 1, kind: "health".into(), x: 58.0, y: 12.0, z: 0.0 },
                ItemRow { id: 2, kind: "health".into(), x: 20.0, y: 34.0, z: 0.0 },
                ItemRow { id: 3, kind: "health".into(), x: 36.0, y: 58.0, z: 0.0 },
                ItemRow { id: 4, kind: "armour".into(), x: 54.0, y: 36.0, z: 0.0 },
                ItemRow { id: 5, kind: "armour".into(), x: 10.0, y: 56.0, z: 0.0 },
                ItemRow { id: 6, kind: "ammo_assault".into(), x: 30.0, y: 30.0, z: 0.0 },
                ItemRow { id: 7, kind: "ammo_assault".into(), x: 48.0, y: 52.0, z: 1.2 },
                ItemRow { id: 8, kind: "ammo_sniper".into(), x: 16.0, y: 32.0, z: 0.0 },
                ItemRow { id: 9, kind: "clips".into(), x: 38.0, y: 44.0, z: 2.4 },
                ItemRow { id: 10, kind: "grenade".into(), x: 28.0, y: 12.0, z: 0.0 },
            ],
        )
    } else if info.name == "hd_mirage" {
        (
            vec![
                SpawnPoint { x: 32.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 36.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 32.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 36.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 32.0, y: 58.0, z: 0.0, yaw: 270.0, team: 0 },
                SpawnPoint { x: 36.0, y: 58.0, z: 0.0, yaw: 270.0, team: 0 },
                SpawnPoint { x: 32.0, y: 54.0, z: 0.0, yaw: 270.0, team: 0 },
                SpawnPoint { x: 36.0, y: 54.0, z: 0.0, yaw: 270.0, team: 0 },
            ],
            vec![
                ItemRow { id: 1, kind: "health".into(), x: 18.0, y: 14.0, z: 0.0 },
                ItemRow { id: 2, kind: "health".into(), x: 50.0, y: 14.0, z: 0.0 },
                ItemRow { id: 3, kind: "health".into(), x: 34.0, y: 34.0, z: 0.0 },
                ItemRow { id: 4, kind: "armour".into(), x: 16.0, y: 48.0, z: 0.8 },
                ItemRow { id: 5, kind: "armour".into(), x: 50.0, y: 48.0, z: 2.8 },
                ItemRow { id: 6, kind: "ammo_assault".into(), x: 14.0, y: 32.0, z: 2.8 },
                ItemRow { id: 7, kind: "ammo_assault".into(), x: 48.0, y: 32.0, z: 0.0 },
                ItemRow { id: 8, kind: "ammo_sniper".into(), x: 34.0, y: 50.0, z: 2.4 },
                ItemRow { id: 9, kind: "clips".into(), x: 34.0, y: 18.0, z: 0.0 },
                ItemRow { id: 10, kind: "grenade".into(), x: 26.0, y: 36.0, z: 1.8 },
            ],
        )
    } else if info.name == "hd_inferno" {
        (
            vec![
                SpawnPoint { x: 30.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 34.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 30.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 34.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 30.0, y: 56.0, z: 0.0, yaw: 270.0, team: 0 },
                SpawnPoint { x: 34.0, y: 56.0, z: 0.0, yaw: 270.0, team: 0 },
                SpawnPoint { x: 30.0, y: 52.0, z: 0.0, yaw: 270.0, team: 0 },
                SpawnPoint { x: 34.0, y: 52.0, z: 0.0, yaw: 270.0, team: 0 },
            ],
            vec![
                ItemRow { id: 1, kind: "health".into(), x: 16.0, y: 14.0, z: 0.0 },
                ItemRow { id: 2, kind: "health".into(), x: 48.0, y: 14.0, z: 0.0 },
                ItemRow { id: 3, kind: "health".into(), x: 32.0, y: 32.0, z: 0.0 },
                ItemRow { id: 4, kind: "armour".into(), x: 18.0, y: 48.0, z: 0.0 },
                ItemRow { id: 5, kind: "armour".into(), x: 50.0, y: 48.0, z: 0.0 },
                ItemRow { id: 6, kind: "ammo_assault".into(), x: 14.0, y: 30.0, z: 0.0 },
                ItemRow { id: 7, kind: "ammo_assault".into(), x: 50.0, y: 30.0, z: 0.0 },
                ItemRow { id: 8, kind: "ammo_sniper".into(), x: 32.0, y: 54.0, z: 0.0 },
                ItemRow { id: 9, kind: "clips".into(), x: 32.0, y: 18.0, z: 0.0 },
                ItemRow { id: 10, kind: "grenade".into(), x: 20.0, y: 24.0, z: 0.0 },
            ],
        )
    } else if info.name == "hd_office" {
        (
            vec![
                SpawnPoint { x: 28.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 32.0, y: 10.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 28.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 32.0, y: 14.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 50.0, y: 32.0, z: 0.0, yaw: 270.0, team: 0 },
                SpawnPoint { x: 52.0, y: 28.0, z: 0.0, yaw: 270.0, team: 0 },
                SpawnPoint { x: 52.0, y: 36.0, z: 0.0, yaw: 270.0, team: 0 },
                SpawnPoint { x: 48.0, y: 24.0, z: 0.0, yaw: 270.0, team: 0 },
            ],
            vec![
                ItemRow { id: 1, kind: "health".into(), x: 10.0, y: 48.0, z: 0.0 },
                ItemRow { id: 2, kind: "health".into(), x: 10.0, y: 12.0, z: 0.0 },
                ItemRow { id: 3, kind: "health".into(), x: 54.0, y: 32.0, z: 0.0 },
                ItemRow { id: 4, kind: "armour".into(), x: 16.0, y: 46.0, z: 0.0 },
                ItemRow { id: 5, kind: "armour".into(), x: 16.0, y: 16.0, z: 0.0 },
                ItemRow { id: 6, kind: "ammo_assault".into(), x: 22.0, y: 32.0, z: 0.0 },
                ItemRow { id: 7, kind: "ammo_assault".into(), x: 44.0, y: 18.0, z: 0.0 },
                ItemRow { id: 8, kind: "ammo_sniper".into(), x: 44.0, y: 44.0, z: 0.0 },
                ItemRow { id: 9, kind: "clips".into(), x: 30.0, y: 28.0, z: 0.0 },
                ItemRow { id: 10, kind: "grenade".into(), x: 30.0, y: 36.0, z: 0.0 },
            ],
        )
    } else if info.name == "hd_nuke" {
        (
            vec![
                SpawnPoint { x: 34.0, y: 12.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 38.0, y: 12.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 34.0, y: 15.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 38.0, y: 15.0, z: 0.0, yaw: 90.0, team: 1 },
                SpawnPoint { x: 26.0, y: 56.0, z: 0.0, yaw: 270.0, team: 0 },
                SpawnPoint { x: 30.0, y: 56.0, z: 0.0, yaw: 270.0, team: 0 },
                SpawnPoint { x: 26.0, y: 52.0, z: 0.0, yaw: 270.0, team: 0 },
                SpawnPoint { x: 30.0, y: 52.0, z: 0.0, yaw: 270.0, team: 0 },
            ],
            vec![
                ItemRow { id: 1, kind: "health".into(), x: 30.0, y: 35.0, z: 0.0 },
                ItemRow { id: 2, kind: "health".into(), x: 30.0, y: 35.0, z: -4.5 },
                ItemRow { id: 3, kind: "health".into(), x: 12.0, y: 30.0, z: 0.0 },
                ItemRow { id: 4, kind: "armour".into(), x: 32.0, y: 37.0, z: 0.0 },
                ItemRow { id: 5, kind: "armour".into(), x: 32.0, y: 37.0, z: -4.5 },
                ItemRow { id: 6, kind: "ammo_assault".into(), x: 24.0, y: 20.0, z: 0.0 },
                ItemRow { id: 7, kind: "ammo_assault".into(), x: 36.0, y: 48.0, z: 0.0 },
                ItemRow { id: 8, kind: "ammo_sniper".into(), x: 20.0, y: 35.0, z: 3.8 },
                ItemRow { id: 9, kind: "clips".into(), x: 48.0, y: 35.0, z: 0.0 },
                ItemRow { id: 10, kind: "grenade".into(), x: 30.0, y: 25.0, z: 0.0 },
            ],
        )
    } else {
        (
            vec![
                SpawnPoint { x: 16.0, y: 7.0, z: 0.18, yaw: 90.0, team: 0 },
                SpawnPoint { x: 20.0, y: 7.0, z: 0.18, yaw: 90.0, team: 0 },
                SpawnPoint { x: 24.0, y: 7.0, z: 0.18, yaw: 90.0, team: 0 },
                SpawnPoint { x: 28.0, y: 7.0, z: 0.18, yaw: 90.0, team: 0 },
                SpawnPoint { x: 32.0, y: 48.0, z: 4.2, yaw: 270.0, team: 1 },
                SpawnPoint { x: 36.0, y: 48.0, z: 4.2, yaw: 270.0, team: 1 },
                SpawnPoint { x: 40.0, y: 48.0, z: 4.2, yaw: 270.0, team: 1 },
                SpawnPoint { x: 34.0, y: 52.0, z: 4.2, yaw: 270.0, team: 1 },
            ],
            vec![
                ItemRow { id: 1, kind: "health".into(), x: 14.0, y: 6.0, z: 0.18 },
                ItemRow { id: 2, kind: "health".into(), x: 55.0, y: 16.0, z: 0.0 },
                ItemRow { id: 3, kind: "health".into(), x: 38.0, y: 52.0, z: 4.2 },
                ItemRow { id: 4, kind: "armour".into(), x: 20.0, y: 36.0, z: 0.0 },
                ItemRow { id: 5, kind: "armour".into(), x: 32.0, y: 44.0, z: 4.2 },
                ItemRow { id: 6, kind: "ammo_assault".into(), x: 21.0, y: 15.5, z: 0.0 },
                ItemRow { id: 7, kind: "ammo_assault".into(), x: 54.0, y: 30.0, z: 0.0 },
                ItemRow { id: 8, kind: "ammo_sniper".into(), x: 24.0, y: 7.0, z: 7.5 },
                ItemRow { id: 9, kind: "clips".into(), x: 10.5, y: 40.0, z: 4.2 },
                ItemRow { id: 10, kind: "grenade".into(), x: 16.0, y: 30.0, z: 9.0 },
            ],
        )
    };

    let bounds = if info.name == "hd_facility" {
        WorldBounds {
            min: [0.0, 0.0, -5.0],
            max: [64.0, 64.0, 14.0],
            center: [32.0, 32.0, 4.5],
            extent: 42.0,
        }
    } else if info.name == "hd_junkflea" {
        WorldBounds {
            min: [4.0, 4.0, -2.5],
            max: [60.0, 60.0, 14.0],
            center: [32.0, 32.0, 5.75],
            extent: 38.0,
        }
    } else if info.name == "hd_nuke" {
        WorldBounds {
            min: [4.0, 4.0, -5.5],
            max: [66.0, 66.0, 14.0],
            center: [35.0, 35.0, 4.0],
            extent: 62.0,
        }
    } else if info.name == "hd_dust2" || info.name == "hd_inferno" || info.name == "hd_mirage" {
        WorldBounds {
            min: [4.0, 4.0, -2.0],
            max: [66.0, 66.0, 14.0],
            center: [35.0, 35.0, 6.0],
            extent: 62.0,
        }
    } else {
        WorldBounds {
            min: [4.0, 4.0, 0.0],
            max: [60.0, 60.0, 14.0],
            center: [32.0, 32.0, 7.0],
            extent: 56.0,
        }
    };

    Ok(World3D {
        info: info.clone(),
        bounds,
        render_positions,
        render_normals,
        render_colors,
        render_uvs,
        render_materials,
        triangles,
        col_vertices,
        col_indices,
        spawns,
        items,
        waterlevel: if info.name == "hd_facility" { -3.5 } else if info.name == "hd_junkflea" { -5.0 } else { -100.0 },
        windows,
    })
}

pub fn load_nuke_glb(info: MapInfo) -> Result<World3D, String> {
    for path in [
        "../../backend/modules/hassault/maps/hd_nuke.glb",
        "backend/modules/hassault/maps/hd_nuke.glb",
        "assets/maps/hd_nuke.glb",
        "apps/web/public/hd_nuke.glb",
    ] {
        if let Ok(bytes) = std::fs::read(path) {
            return load_world_3d_from_glb(&bytes, info);
        }
    }
    load_world_3d_from_glb(HD_NUKE_GLB_BYTES, info)
}

pub fn load_mirage_glb(info: MapInfo) -> Result<World3D, String> {
    for path in [
        "../../backend/modules/hassault/maps/hd_mirage.glb",
        "backend/modules/hassault/maps/hd_mirage.glb",
        "assets/maps/hd_mirage.glb",
        "apps/web/public/hd_mirage.glb",
    ] {
        if let Ok(bytes) = std::fs::read(path) {
            return load_world_3d_from_glb(&bytes, info);
        }
    }
    load_world_3d_from_glb(HD_MIRAGE_GLB_BYTES, info)
}

pub fn load_inferno_glb(info: MapInfo) -> Result<World3D, String> {
    for path in [
        "../../backend/modules/hassault/maps/hd_inferno.glb",
        "backend/modules/hassault/maps/hd_inferno.glb",
        "assets/maps/hd_inferno.glb",
        "apps/web/public/hd_inferno.glb",
    ] {
        if let Ok(bytes) = std::fs::read(path) {
            return load_world_3d_from_glb(&bytes, info);
        }
    }
    load_world_3d_from_glb(HD_INFERNO_GLB_BYTES, info)
}

pub fn load_dust2_glb(info: MapInfo) -> Result<World3D, String> {
    for path in [
        "../../backend/modules/hassault/maps/hd_dust2.glb",
        "backend/modules/hassault/maps/hd_dust2.glb",
        "assets/maps/hd_dust2.glb",
        "apps/web/public/hd_dust2.glb",
    ] {
        if let Ok(bytes) = std::fs::read(path) {
            return load_world_3d_from_glb(&bytes, info);
        }
    }
    load_world_3d_from_glb(HD_DUST2_GLB_BYTES, info)
}

pub fn load_facility_glb(info: MapInfo) -> Result<World3D, String> {
    for path in [
        "../../backend/modules/hassault/maps/hd_facility.glb",
        "backend/modules/hassault/maps/hd_facility.glb",
        "assets/maps/hd_facility.glb",
        "apps/web/public/hd_facility.glb",
    ] {
        if let Ok(bytes) = std::fs::read(path) {
            return load_world_3d_from_glb(&bytes, info);
        }
    }
    load_world_3d_from_glb(HD_FACILITY_GLB_BYTES, info)
}

pub fn load_junkflea_glb(info: MapInfo) -> Result<World3D, String> {
    for path in [
        "../../backend/modules/hassault/maps/hd_junkflea.glb",
        "backend/modules/hassault/maps/hd_junkflea.glb",
        "assets/maps/hd_junkflea.glb",
        "apps/web/public/hd_junkflea.glb",
    ] {
        if let Ok(bytes) = std::fs::read(path) {
            return load_world_3d_from_glb(&bytes, info);
        }
    }
    load_world_3d_from_glb(HD_JUNKFLEA_GLB_BYTES, info)
}

pub fn load_bank_glb(info: MapInfo) -> Result<World3D, String> {
    for path in [
        "../../backend/modules/hassault/maps/hd_bank.glb",
        "backend/modules/hassault/maps/hd_bank.glb",
        "assets/maps/hd_bank.glb",
        "apps/web/public/hd_bank.glb",
    ] {
        if let Ok(bytes) = std::fs::read(path) {
            return load_world_3d_from_glb(&bytes, info);
        }
    }
    load_world_3d_from_glb(HD_BANK_GLB_BYTES, info)
}

pub fn load_assault_glb(info: MapInfo) -> Result<World3D, String> {
    for path in [
        "../../backend/modules/hassault/maps/hd_assault.glb",
        "backend/modules/hassault/maps/hd_assault.glb",
        "assets/maps/hd_assault.glb",
        "apps/web/public/hd_assault.glb",
    ] {
        if let Ok(bytes) = std::fs::read(path) {
            return load_world_3d_from_glb(&bytes, info);
        }
    }
    load_world_3d_from_glb(HD_ASSAULT_GLB_BYTES, info)
}

pub fn load_office_glb(info: MapInfo) -> Result<World3D, String> {
    for path in [
        "../../backend/modules/hassault/maps/hd_office.glb",
        "backend/modules/hassault/maps/hd_office.glb",
        "assets/maps/hd_office.glb",
        "apps/web/public/hd_office.glb",
    ] {
        if let Ok(bytes) = std::fs::read(path) {
            return load_world_3d_from_glb(&bytes, info);
        }
    }
    load_world_3d_from_glb(HD_OFFICE_GLB_BYTES, info)
}

/// Universal 3D Arena Factory: selects appropriate procedural or modeled 3D map generator.
pub fn create_world_3d(info: MapInfo) -> World3D {
    match info.name.as_str() {
        "hd_nuke" => {
            match load_nuke_glb(info.clone()) {
                Ok(w) => w,
                Err(err) => {
                    eprintln!("hassault: failed to load GLB for hd_nuke ({err}), falling back to procedural");
                    create_procedural_nuke_3d(info)
                }
            }
        }
        "hd_mirage" => {
            match load_mirage_glb(info.clone()) {
                Ok(w) => w,
                Err(err) => {
                    eprintln!("hassault: failed to load GLB for hd_mirage ({err}), falling back to procedural");
                    create_procedural_mirage_3d(info)
                }
            }
        }
        "hd_inferno" => {
            match load_inferno_glb(info.clone()) {
                Ok(w) => w,
                Err(err) => {
                    eprintln!("hassault: failed to load GLB for hd_inferno ({err}), falling back to procedural");
                    create_procedural_inferno_3d(info)
                }
            }
        }
        "hd_dust2" => {
            match load_dust2_glb(info.clone()) {
                Ok(w) => w,
                Err(err) => {
                    eprintln!("hassault: failed to load GLB for hd_dust2 ({err}), falling back to procedural");
                    create_procedural_dust2_3d(info)
                }
            }
        }
        "hd_facility" => {
            match load_facility_glb(info.clone()) {
                Ok(w) => w,
                Err(err) => {
                    eprintln!("hassault: failed to load GLB for hd_facility ({err}), falling back to procedural");
                    create_procedural_facility_3d(info)
                }
            }
        }
        "hd_junkflea" => {
            match load_junkflea_glb(info.clone()) {
                Ok(w) => w,
                Err(err) => {
                    eprintln!("hassault: failed to load GLB for hd_junkflea ({err}), falling back to procedural");
                    create_procedural_junk_flea_3d(info)
                }
            }
        }
        "hd_bank" => {
            match load_bank_glb(info.clone()) {
                Ok(w) => w,
                Err(err) => {
                    eprintln!("hassault: failed to load GLB for hd_bank ({err}), falling back to procedural");
                    create_procedural_bank_3d(info)
                }
            }
        }
        "hd_assault" => {
            match load_assault_glb(info.clone()) {
                Ok(w) => w,
                Err(err) => {
                    eprintln!("hassault: failed to load GLB for hd_assault ({err}), falling back to procedural");
                    create_procedural_assault_3d(info)
                }
            }
        }
        "hd_office" => {
            match load_office_glb(info.clone()) {
                Ok(w) => w,
                Err(err) => {
                    eprintln!("hassault: failed to load GLB for hd_office ({err}), falling back to procedural");
                    create_procedural_office_3d(info)
                }
            }
        }
        _ => create_procedural_facility_3d(info),
    }
}

