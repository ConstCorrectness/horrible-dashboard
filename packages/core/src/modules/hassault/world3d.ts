/**
 * True 3D World Representation & Loader for HorribleAssault.
 *
 * Transcends Cube 1's 2.5D column heightfield limitations by supporting arbitrary
 * 3D polygonal geometry (glTF / GLB) with verticality: multi-story buildings,
 * mezzanine catwalks, stairs, underground conduits, and dynamic cover.
 *
 * Upgraded with Crossfire-grade PBR textured surface rendering, planar world-space
 * UV projection, authentic material responses (polished marble, asphalt, brushed
 * vault steel, hazard warning decals, bulletproof glass), and full Rapier 3D physics.
 *
 * Coordinate Convention:
 * Game coordinates: (x, y) horizontal grid/plane, z is elevation/height (z-up).
 * Three.js coordinates: three.x = x, three.y = z (height), three.z = y.
 */
import type * as THREE from 'three';
import type { MapInfo } from './api';
import type { Ladder } from './world';
import type { ItemRow } from './net';
import { createPBRMaterialLibrary, type MaterialKind, type PBRMaterialLibrary } from './textures3d';

export interface SpawnPoint {
  x: number;
  y: number;
  z: number;
  yaw: number;
  team: number; // 0 = CLA, 1 = RVSF
}

export interface WorldBounds {
  min: [number, number, number];
  max: [number, number, number];
  center: [number, number, number];
  extent: number;
}

export interface CollisionGeometry {
  vertices: Float32Array;
  indices: Uint32Array;
  triangles: number;
}

export interface World3D {
  readonly info: MapInfo;
  readonly scene: THREE.Group;
  readonly bounds: WorldBounds;
  readonly collision: CollisionGeometry;
  readonly spawns: {
    cla: SpawnPoint[];
    rvsf: SpawnPoint[];
    all: SpawnPoint[];
  };
  readonly items: ItemRow[];
  readonly ladders: Ladder[];
  readonly waterlevel: number;
  readonly materials?: THREE.Material[];
  dispose(): void;
}

/**
 * Computes world-space UV coordinates projected along the dominant face normal.
 * Eliminates texture stretching and produces seamless material tiling.
 */
function computePlanarUV(
  p: [number, number, number],
  norm: { x: number; y: number; z: number },
  scale = 4.0,
): [number, number] {
  // Three.js coords: x = game.x, y = game.z (elevation), z = game.y (horizontal)
  if (Math.abs(norm.y) > 0.6) {
    // Horizontal floor or ceiling
    return [p[0] / scale, p[1] / scale];
  }
  if (Math.abs(norm.x) > 0.6) {
    // Vertical wall facing east/west
    return [p[1] / scale, p[2] / scale];
  }
  // Vertical wall facing north/south
  return [p[0] / scale, p[2] / scale];
}

class World3DBuilder {
  private batches = new Map<MaterialKind, { positions: number[]; normals: number[]; uvs: number[] }>();
  public colVertices: number[] = [];
  public colIndices: number[] = [];
  private colIndexOffset = 0;

  constructor(private THREE: typeof import('three')) {}

  private getBatch(kind: MaterialKind) {
    let b = this.batches.get(kind);
    if (!b) {
      b = { positions: [], normals: [], uvs: [] };
      this.batches.set(kind, b);
    }
    return b;
  }

  addTriangle(
    p0: [number, number, number],
    p1: [number, number, number],
    p2: [number, number, number],
    material: MaterialKind = 'concrete',
    isCollider = true,
    customUVs?: [number, number, number, number, number, number],
  ) {
    const t0 = [p0[0], p0[2], p0[1]];
    const t1 = [p1[0], p1[2], p1[1]];
    const t2 = [p2[0], p2[2], p2[1]];

    const vA = new this.THREE.Vector3(t1[0] - t0[0], t1[1] - t0[1], t1[2] - t0[2]);
    const vB = new this.THREE.Vector3(t2[0] - t0[0], t2[1] - t0[1], t2[2] - t0[2]);
    const cross = new this.THREE.Vector3().crossVectors(vA, vB);
    if (cross.lengthSq() < 1e-6) {
      return; // Ignore degenerate triangles
    }
    const norm = cross.normalize();

    let uv0: [number, number], uv1: [number, number], uv2: [number, number];
    if (customUVs) {
      uv0 = [customUVs[0], customUVs[1]];
      uv1 = [customUVs[2], customUVs[3]];
      uv2 = [customUVs[4], customUVs[5]];
    } else {
      const tileScale = material === 'hazard' || material === 'crate' ? 2.0 : 4.0;
      uv0 = computePlanarUV(p0, norm, tileScale);
      uv1 = computePlanarUV(p1, norm, tileScale);
      uv2 = computePlanarUV(p2, norm, tileScale);
    }

    const batch = this.getBatch(material);
    batch.positions.push(...t0, ...t1, ...t2);
    batch.normals.push(norm.x, norm.y, norm.z, norm.x, norm.y, norm.z, norm.x, norm.y, norm.z);
    batch.uvs.push(...uv0, ...uv1, ...uv2);

    if (isCollider) {
      const bIdx = this.colIndexOffset;
      this.colVertices.push(...p0, ...p1, ...p2);
      this.colIndices.push(bIdx, bIdx + 1, bIdx + 2);
      this.colIndexOffset += 3;
    }
  }

  addQuad(
    p0: [number, number, number],
    p1: [number, number, number],
    p2: [number, number, number],
    p3: [number, number, number],
    material: MaterialKind = 'concrete',
    isCollider = true,
    customUVs?: [number, number, number, number, number, number, number, number],
  ) {
    if (customUVs) {
      this.addTriangle(p0, p1, p2, material, isCollider, [
        customUVs[0], customUVs[1],
        customUVs[2], customUVs[3],
        customUVs[4], customUVs[5],
      ]);
      this.addTriangle(p0, p2, p3, material, isCollider, [
        customUVs[0], customUVs[1],
        customUVs[4], customUVs[5],
        customUVs[6], customUVs[7],
      ]);
    } else {
      this.addTriangle(p0, p1, p2, material, isCollider);
      this.addTriangle(p0, p2, p3, material, isCollider);
    }
  }

  addFloor(
    minX: number, minY: number,
    maxX: number, maxY: number,
    z: number,
    material: MaterialKind = 'concrete',
    isCollider = true,
    customUVs?: [number, number, number, number, number, number, number, number],
  ) {
    // Top face pointing strictly UP (+y in Three.js coordinates):
    // p0 = (minX, maxY), p1 = (maxX, maxY), p2 = (maxX, minY), p3 = (minX, minY)
    this.addQuad(
      [minX, maxY, z],
      [maxX, maxY, z],
      [maxX, minY, z],
      [minX, minY, z],
      material,
      isCollider,
      customUVs,
    );
  }

  addBox(
    minX: number, minY: number, minZ: number,
    maxX: number, maxY: number, maxZ: number,
    material: MaterialKind = 'concrete',
    isCollider = true,
  ) {
    this.addQuad([minX, minY, minZ], [maxX, minY, minZ], [maxX, maxY, minZ], [minX, maxY, minZ], material, isCollider);
    this.addQuad([minX, maxY, maxZ], [maxX, maxY, maxZ], [maxX, minY, maxZ], [minX, minY, maxZ], material, isCollider);
    this.addQuad([maxX, maxY, minZ], [maxX, maxY, maxZ], [minX, maxY, maxZ], [minX, maxY, minZ], material, isCollider);
    this.addQuad([minX, minY, minZ], [minX, minY, maxZ], [maxX, minY, maxZ], [maxX, minY, minZ], material, isCollider);
    this.addQuad([maxX, minY, minZ], [maxX, minY, maxZ], [maxX, maxY, maxZ], [maxX, maxY, minZ], material, isCollider);
    this.addQuad([minX, maxY, minZ], [minX, maxY, maxZ], [minX, minY, maxZ], [minX, minY, minZ], material, isCollider);
  }

  addCylinder(
    cx: number,
    cy: number,
    minZ: number,
    maxZ: number,
    radius: number,
    segments: number,
    material: MaterialKind = 'concrete',
    isCollider = true,
  ) {
    const seg = Math.max(6, segments);
    const step = (Math.PI * 2) / seg;
    for (let i = 0; i < seg; i++) {
      const a0 = i * step;
      const a1 = (i + 1) * step;
      const x0 = cx + radius * Math.cos(a0);
      const y0 = cy + radius * Math.sin(a0);
      const x1 = cx + radius * Math.cos(a1);
      const y1 = cy + radius * Math.sin(a1);

      // Wall quad (faces outwards)
      this.addQuad(
        [x0, y0, minZ],
        [x1, y1, minZ],
        [x1, y1, maxZ],
        [x0, y0, maxZ],
        material,
        isCollider,
      );

      // Top disc (facing +z up)
      this.addTriangle(
        [cx, cy, maxZ],
        [x1, y1, maxZ],
        [x0, y0, maxZ],
        material,
        isCollider,
      );

      // Bottom disc (facing -z down)
      this.addTriangle(
        [cx, cy, minZ],
        [x0, y0, minZ],
        [x1, y1, minZ],
        material,
        isCollider,
      );
    }
  }

  addRamp(
    minX: number, minY: number, z0: number,
    maxX: number, maxY: number, z1: number,
    material: MaterialKind = 'concrete',
    isCollider = true,
  ) {
    const minZ = Math.min(z0, z1);

    // 1. Slope Quad (wound so normal points UP into the sky, norm.y > 0)
    // p0: (minX, minY, z0), p1: (minX, maxY, z1), p2: (maxX, maxY, z1), p3: (maxX, minY, z0)
    this.addQuad(
      [minX, minY, z0],
      [minX, maxY, z1],
      [maxX, maxY, z1],
      [maxX, minY, z0],
      material,
      isCollider,
    );

    // 2. West side wall (x = minX, outward normal norm.x < 0)
    this.addTriangle(
      [minX, minY, z0],
      [minX, minY, minZ],
      [minX, maxY, z1],
      material,
      isCollider,
    );
    this.addTriangle(
      [minX, minY, minZ],
      [minX, maxY, minZ],
      [minX, maxY, z1],
      material,
      isCollider,
    );

    // 3. East side wall (x = maxX, outward normal norm.x > 0)
    this.addTriangle(
      [maxX, minY, minZ],
      [maxX, minY, z0],
      [maxX, maxY, z1],
      material,
      isCollider,
    );
    this.addTriangle(
      [maxX, maxY, minZ],
      [maxX, minY, minZ],
      [maxX, maxY, z1],
      material,
      isCollider,
    );

    // 4. Back vertical wall if elevated above minZ
    if (z1 > z0 && z1 > minZ) {
      // Elevated at maxY, wall faces North (+y in game, +z in Three)
      this.addQuad(
        [minX, maxY, minZ],
        [maxX, maxY, minZ],
        [maxX, maxY, z1],
        [minX, maxY, z1],
        material,
        isCollider,
      );
    } else if (z0 > z1 && z0 > minZ) {
      // Elevated at minY, wall faces South (-y in game, -z in Three)
      this.addQuad(
        [maxX, minY, minZ],
        [minX, minY, minZ],
        [minX, minY, z0],
        [maxX, minY, z0],
        material,
        isCollider,
      );
    }
  }

  build(root: THREE.Group): { geometries: THREE.BufferGeometry[]; materials: THREE.Material[]; lib: PBRMaterialLibrary } {
    const lib = createPBRMaterialLibrary(this.THREE);
    const geometries: THREE.BufferGeometry[] = [];
    const materials: THREE.Material[] = [];

    for (const [kind, batch] of this.batches.entries()) {
      if (batch.positions.length === 0) continue;
      const geo = new this.THREE.BufferGeometry();
      geo.setAttribute('position', new this.THREE.Float32BufferAttribute(batch.positions, 3));
      geo.setAttribute('normal', new this.THREE.Float32BufferAttribute(batch.normals, 3));
      geo.setAttribute('uv', new this.THREE.Float32BufferAttribute(batch.uvs, 2));
      geo.computeBoundingSphere();
      geo.computeBoundingBox();

      const mat = lib.materials[kind];
      const mesh = new this.THREE.Mesh(geo, mat);
      mesh.name = `World3D_${kind}`;
      mesh.castShadow = kind !== 'glass';
      mesh.receiveShadow = true;
      root.add(mesh);

      geometries.push(geo);
      materials.push(mat);
    }

    return { geometries, materials, lib };
  }
}

/**
 * Procedural 3D Facility ("The Deadzone Facility"):
 * 1. Lower Coolant Trench (z = -5) with water and steel ramps.
 * 2. Ground Floor Server Core (z = 0) with concrete pillars, server stacks, and tactical crates.
 * 3. Upper Mezzanine & Catwalks (z = 6) with elevated perimeter firing positions.
 * 4. Staircases connecting all three vertical tiers.
 */
export function createProceduralFacility3D(
  THREE: typeof import('three'),
  info: MapInfo,
): World3D {
  const root = new THREE.Group();
  root.name = 'World3D_Facility';
  const builder = new World3DBuilder(THREE);

  // Arena Geometry: 64x64 world
  // 1. Outer Perimeter Walls (height = 14)
  builder.addQuad([0, 0, 0], [64, 0, 0], [64, 0, 14], [0, 0, 14], 'concrete');
  builder.addQuad([64, 64, 0], [0, 64, 0], [0, 64, 14], [64, 64, 14], 'concrete');
  builder.addQuad([64, 0, 0], [64, 64, 0], [64, 64, 14], [64, 0, 14], 'concrete');
  builder.addQuad([0, 64, 0], [0, 0, 0], [0, 0, 14], [0, 64, 14], 'concrete');

  // 2. Ground Floor (z = 0), with central pit opening [20..44, 20..44]
  builder.addFloor(0, 0, 20, 64, 0, 'concrete');
  builder.addFloor(44, 0, 64, 64, 0, 'concrete');
  builder.addFloor(20, 0, 44, 20, 0, 'concrete');
  builder.addFloor(20, 44, 44, 64, 0, 'concrete');

  // 3. Lower Coolant Pit (z = -5)
  builder.addFloor(20, 20, 44, 44, -5, 'vault_steel');
  // Pit walls (facing inward into the coolant pit):
  builder.addQuad([20, 20, -5], [44, 20, -5], [44, 20, 0], [20, 20, 0], 'vault_steel');
  builder.addQuad([44, 44, -5], [20, 44, -5], [20, 44, 0], [44, 44, 0], 'vault_steel');
  builder.addQuad([44, 20, -5], [44, 44, -5], [44, 44, 0], [44, 20, 0], 'vault_steel');
  builder.addQuad([20, 44, -5], [20, 20, -5], [20, 20, 0], [20, 44, 0], 'vault_steel');

  // Ramps into the Pit
  builder.addRamp(28, 14, 0, 36, 20, -5, 'vault_steel');
  builder.addRamp(28, 44, -5, 36, 50, 0, 'vault_steel');

  // 4. Upper Mezzanine / Catwalk (z = 6, width = 6 around perimeter)
  builder.addFloor(0, 0, 64, 6, 6, 'vault_steel');
  builder.addFloor(0, 58, 64, 64, 6, 'vault_steel');
  builder.addFloor(0, 6, 6, 58, 6, 'vault_steel');
  builder.addFloor(58, 6, 64, 58, 6, 'vault_steel');

  // Cross-Bridge over the arena at z = 6
  builder.addFloor(26, 6, 38, 58, 6, 'vault_steel');

  // Ramps leading from Ground (z = 0) to Catwalk (z = 6)
  builder.addRamp(2, 10, 0, 6, 26, 6, 'concrete');
  builder.addRamp(58, 38, 6, 62, 54, 0, 'concrete');

  // 5. Tactical Cover Blocks on Ground Floor
  builder.addBox(10, 12, 0, 14, 16, 3, 'crate');
  builder.addBox(50, 48, 0, 54, 52, 3, 'crate');
  builder.addBox(12, 48, 0, 16, 52, 3.5, 'vault_steel');
  builder.addBox(48, 12, 0, 52, 16, 3.5, 'vault_steel');

  // Pillars supporting the cross-bridge
  builder.addBox(24, 22, 0, 26, 24, 6, 'concrete');
  builder.addBox(38, 22, 0, 40, 24, 6, 'concrete');
  builder.addBox(24, 40, 0, 26, 42, 6, 'concrete');
  builder.addBox(38, 40, 0, 40, 42, 6, 'concrete');

  const { geometries, materials, lib } = builder.build(root);

  const claSpawns: SpawnPoint[] = [
    { x: 10, y: 8, z: 0, yaw: 0, team: 0 },
    { x: 32, y: 4, z: 6, yaw: 0, team: 0 },
    { x: 54, y: 8, z: 0, yaw: 315, team: 0 },
    { x: 32, y: 10, z: 0, yaw: 0, team: 0 },
  ];
  const rvsfSpawns: SpawnPoint[] = [
    { x: 10, y: 56, z: 0, yaw: 180, team: 1 },
    { x: 32, y: 60, z: 6, yaw: 180, team: 1 },
    { x: 54, y: 56, z: 0, yaw: 225, team: 1 },
    { x: 32, y: 54, z: 0, yaw: 180, team: 1 },
  ];
  const allSpawns = [...claSpawns, ...rvsfSpawns];

  const items: ItemRow[] = [
    { id: 1, kind: 'ammo_assault', x: 32, y: 32, z: 6 },
    { id: 2, kind: 'armour', x: 32, y: 32, z: -5 },
    { id: 3, kind: 'health', x: 8, y: 32, z: 0 },
    { id: 4, kind: 'health', x: 56, y: 32, z: 0 },
    { id: 5, kind: 'ammo_sniper', x: 4, y: 4, z: 6 },
    { id: 6, kind: 'ammo_sniper', x: 60, y: 60, z: 6 },
  ];

  const bounds: WorldBounds = {
    min: [0, 0, -5],
    max: [64, 64, 14],
    center: [32, 32, 4.5],
    extent: 42,
  };

  const collision: CollisionGeometry = {
    vertices: new Float32Array(builder.colVertices),
    indices: new Uint32Array(builder.colIndices),
    triangles: builder.colIndices.length / 3,
  };

  return {
    info,
    scene: root,
    bounds,
    collision,
    spawns: { cla: claSpawns, rvsf: rvsfSpawns, all: allSpawns },
    items,
    ladders: [],
    waterlevel: -3.5,
    materials,
    dispose() {
      for (const g of geometries) g.dispose();
      lib.dispose();
    },
  };
}

/**
 * Procedural 3D Junk Flea:
 * Inspired by high-intensity CQB junkyard arenas with shipping containers.
 */
export function createProceduralJunkFlea3D(
  THREE: typeof import('three'),
  info: MapInfo,
): World3D {
  const root = new THREE.Group();
  root.name = 'World3D_JunkFlea';
  const builder = new World3DBuilder(THREE);

  // 1. Perimeter Concrete/Steel Walls (height = 14)
  builder.addQuad([4, 4, 0], [60, 4, 0], [60, 4, 14], [4, 4, 14], 'concrete');
  builder.addQuad([60, 60, 0], [4, 60, 0], [4, 60, 14], [60, 60, 14], 'concrete');
  builder.addQuad([60, 4, 0], [60, 60, 0], [60, 60, 14], [60, 4, 14], 'concrete');
  builder.addQuad([4, 60, 0], [4, 4, 0], [4, 4, 14], [4, 60, 14], 'concrete');

  // 2. Ground Floor (z = 0) with Trench cutouts
  builder.addFloor(4, 4, 16, 60, 0, 'concrete');
  builder.addFloor(48, 4, 60, 60, 0, 'concrete');
  builder.addFloor(22, 4, 42, 60, 0, 'concrete');

  builder.addFloor(16, 4, 22, 14, 0, 'concrete');
  builder.addFloor(16, 50, 22, 60, 0, 'concrete');
  builder.addFloor(42, 4, 48, 14, 0, 'concrete');
  builder.addFloor(42, 50, 48, 60, 0, 'concrete');

  // 3. Subterranean Trenches (z = -2.0)
  builder.addFloor(16, 20, 22, 44, -2, 'concrete');
  // Trench 1 walls facing inward into trench
  builder.addQuad([16, 20, -2], [16, 44, -2], [16, 44, 0], [16, 20, 0], 'concrete');
  builder.addQuad([22, 44, -2], [22, 20, -2], [22, 20, 0], [22, 44, 0], 'concrete');
  builder.addRamp(16, 14, 0, 22, 20, -2, 'vault_steel');
  builder.addRamp(16, 44, -2, 22, 50, 0, 'vault_steel');

  builder.addFloor(42, 20, 48, 44, -2, 'concrete');
  // Trench 2 walls facing inward into trench
  builder.addQuad([42, 20, -2], [42, 44, -2], [42, 44, 0], [42, 20, 0], 'concrete');
  builder.addQuad([48, 44, -2], [48, 20, -2], [48, 20, 0], [48, 44, 0], 'concrete');
  builder.addRamp(42, 14, 0, 48, 20, -2, 'vault_steel');
  builder.addRamp(42, 44, -2, 48, 50, 0, 'vault_steel');

  // 4. Large Corrugated Shipping Containers (z in [0, 3.2])
  builder.addBox(26, 20, 0, 38, 26, 3.2, 'container');
  builder.addBox(26, 38, 0, 38, 44, 3.2, 'container');
  builder.addBox(8, 24, 0, 14, 40, 3.2, 'container');
  builder.addBox(50, 24, 0, 56, 40, 3.2, 'container');

  // 5. Tactical Crates & Cover
  builder.addBox(20, 12, 0, 24, 16, 1.8, 'crate');
  builder.addBox(40, 48, 0, 44, 52, 1.8, 'crate');
  builder.addBox(30, 30, 0, 34, 34, 1.4, 'crate');

  // 6. High Steel Catwalk Bridge (z = 6.4)
  builder.addFloor(30, 14, 34, 50, 6.4, 'vault_steel');
  builder.addRamp(30, 8, 0, 34, 14, 6.4, 'vault_steel');
  builder.addRamp(30, 50, 6.4, 34, 56, 0, 'vault_steel');

  // Bridge Support Pillars
  builder.addBox(29, 20, 0, 31, 22, 6.4, 'concrete');
  builder.addBox(33, 20, 0, 35, 22, 6.4, 'concrete');
  builder.addBox(29, 42, 0, 31, 44, 6.4, 'concrete');
  builder.addBox(33, 42, 0, 35, 44, 6.4, 'concrete');

  const { geometries, materials, lib } = builder.build(root);

  const claSpawns: SpawnPoint[] = [
    { x: 16, y: 12, z: 0, yaw: 0, team: 0 },
    { x: 48, y: 12, z: 0, yaw: 0, team: 0 },
    { x: 32, y: 10, z: 0, yaw: 0, team: 0 },
    { x: 32, y: 15, z: 0, yaw: 0, team: 0 },
  ];
  const rvsfSpawns: SpawnPoint[] = [
    { x: 16, y: 52, z: 0, yaw: 180, team: 1 },
    { x: 48, y: 52, z: 0, yaw: 180, team: 1 },
    { x: 32, y: 54, z: 0, yaw: 180, team: 1 },
    { x: 32, y: 49, z: 0, yaw: 180, team: 1 },
  ];
  const allSpawns = [...claSpawns, ...rvsfSpawns];

  const items: ItemRow[] = [
    { id: 1, kind: 'ammo_shotgun', x: 32, y: 32, z: 6.4 },
    { id: 2, kind: 'armour', x: 19, y: 32, z: -2 },
    { id: 3, kind: 'armour', x: 45, y: 32, z: -2 },
    { id: 4, kind: 'health', x: 11, y: 12, z: 0 },
    { id: 5, kind: 'health', x: 53, y: 52, z: 0 },
    { id: 6, kind: 'ammo_assault', x: 32, y: 23, z: 3.2 },
    { id: 7, kind: 'ammo_assault', x: 32, y: 41, z: 3.2 },
  ];

  const bounds: WorldBounds = {
    min: [4, 4, -2],
    max: [60, 60, 14],
    center: [32, 32, 5],
    extent: 40,
  };

  const collision: CollisionGeometry = {
    vertices: new Float32Array(builder.colVertices),
    indices: new Uint32Array(builder.colIndices),
    triangles: builder.colIndices.length / 3,
  };

  return {
    info,
    scene: root,
    bounds,
    collision,
    spawns: { cla: claSpawns, rvsf: rvsfSpawns, all: allSpawns },
    items,
    ladders: [],
    waterlevel: -100.0,
    materials,
    dispose() {
      for (const g of geometries) g.dispose();
      lib.dispose();
    },
  };
}

/**
 * Procedural 3D Bank Arena ("The Bank", hd_bank):
 * A premier Crossfire Search & Destroy tactical bomb-defusal arena featuring:
 * 1. Street approach with asphalt, road markings, SWAT van, police cruiser.
 * 2. Grand Banking Hall (Site B) with polished marble tiles and mahogany counter.
 * 3. Executive Mezzanine & Balconies (z = 5.0) for high-ground crossfires.
 * 4. Reinforced Vault (Site A) with hazard-striped archway, steel blast door, and gold bullion pallets.
 * 5. Authentic Bomb Site A & Site B spray stencils on floors.
 */
export function createProceduralBank3D(
  THREE: typeof import('three'),
  info: MapInfo,
): World3D {
  const root = new THREE.Group();
  root.name = 'World3D_Bank';
  const builder = new World3DBuilder(THREE);

  // 1. Perimeter Concrete Walls (64x64 bounds, height = 14)
  builder.addQuad([4, 4, 0], [60, 4, 0], [60, 4, 14], [4, 4, 14], 'concrete');
  builder.addQuad([60, 60, 0], [4, 60, 0], [4, 60, 14], [60, 60, 14], 'concrete');
  builder.addQuad([60, 4, 0], [60, 60, 0], [60, 60, 14], [60, 4, 14], 'concrete');
  builder.addQuad([4, 60, 0], [4, 4, 0], [4, 4, 14], [4, 60, 14], 'concrete');

  // 2. Ground Floors (Street Asphalt vs Bank Marble Floor)
  builder.addFloor(4, 4, 60, 18, 0, 'asphalt');
  // Double solid yellow center divider stripe along y = 10
  builder.addFloor(4, 9.85, 60, 9.95, 0.01, 'hazard', false);
  builder.addFloor(4, 10.05, 60, 10.15, 0.01, 'hazard', false);
  // Pedestrian crosswalk stripes leading straight into bank entrance
  for (let i = 0; i < 5; i++) {
    const sx = 27.5 + i * 2.0;
    builder.addFloor(sx, 7.0, sx + 1.2, 13.5, 0.01, 'concrete', false);
  }

  // Sidewalk concrete curb (y: 14..18)
  builder.addBox(4, 14, 0, 60, 18, 0.2, 'concrete');
  builder.addBox(4, 13.8, 0, 60, 14, 0.25, 'concrete');

  // Street lamps
  for (const lx of [12, 52]) {
    builder.addCylinder(lx, 6, 0, 5.5, 0.12, 8, 'vault_steel', false);
    builder.addBox(lx - 0.2, 5.8, 5.3, lx + 0.2, 7.5, 5.6, 'vault_steel', false);
    builder.addBox(lx - 0.35, 7.0, 5.0, lx + 0.35, 7.6, 5.4, 'gold', false);
  }
  // Red fire hydrants
  for (const hx of [8, 54]) {
    builder.addCylinder(hx, 13, 0, 0.85, 0.22, 8, 'hazard', true);
  }
  // Sidewalk tree planters with lush green hedges
  for (const px of [14, 48]) {
    builder.addBox(px, 14.5, 0.2, px + 2.6, 17.5, 0.6, 'concrete');
    builder.addBox(px + 0.2, 14.7, 0.6, px + 2.4, 17.3, 1.6, 'wood');
  }

  // Main bank marble floor (y: 18..46)
  builder.addFloor(4, 18, 60, 46, 0, 'marble');
  // Polished borders around lobby
  builder.addFloor(4, 18, 60, 19, 0.01, 'wood', false);
  builder.addFloor(4, 45, 60, 46, 0.01, 'wood', false);
  builder.addFloor(4, 19, 5, 45, 0.01, 'wood', false);
  builder.addFloor(59, 19, 60, 45, 0.01, 'wood', false);

  // Rear offices floor (y: 46..60)
  builder.addFloor(4, 46, 40, 60, 0, 'concrete');
  // Vault steel floor (x: 40..60, y: 46..60)
  builder.addFloor(40, 46, 60, 60, 0, 'vault_steel');

  // 3. Bank Exterior Facade Wall (y: 18..22) with 3 Entrances
  builder.addBox(4, 18, 0, 8, 22, 14, 'concrete');
  builder.addBox(14, 18, 0, 28, 22, 14, 'concrete');
  builder.addBox(36, 18, 0, 50, 22, 14, 'concrete');
  builder.addBox(56, 18, 0, 60, 22, 14, 'concrete');

  // Outdoor 24/7 ATM Wall Unit on west facade
  builder.addBox(9.5, 17.6, 0.4, 12.5, 18.0, 2.4, 'vault_steel', true);
  builder.addBox(9.8, 17.55, 1.2, 12.2, 17.6, 1.8, 'glass', false);
  builder.addBox(9.2, 17.4, 2.4, 12.8, 17.6, 2.8, 'vehicle', false);

  // Neoclassical Columns flanking Grand Entrance
  for (const cx of [26, 36]) {
    builder.addBox(cx, 16, 0, cx + 2, 18, 0.8, 'marble');
    builder.addCylinder(cx + 1, 17, 0.8, 12.5, 0.9, 12, 'marble', true);
    builder.addBox(cx - 0.2, 15.8, 12.5, cx + 2.2, 18.2, 14, 'marble');
  }
  // Entrance Stone Canopy (Key Skill Jump platform at z = 4.2..4.8)
  builder.addBox(25, 15, 4.2, 39, 18.5, 4.8, 'concrete');
  // Classical Triangular Pediment / Gable above entrance canopy
  builder.addTriangle([24, 17.8, 4.8], [40, 17.8, 4.8], [32, 17.8, 8.2], 'concrete', true);
  builder.addTriangle([40, 18.2, 4.8], [24, 18.2, 4.8], [32, 18.2, 8.2], 'concrete', true);
  // Gold embossed bank architrave banner
  builder.addBox(26, 17.6, 5.0, 38, 17.8, 5.8, 'gold', false);

  // Brass-trimmed Grand Double Doors in entrance portal
  builder.addBox(29, 18.1, 0, 35, 18.4, 3.8, 'wood', false);
  builder.addBox(29.5, 18.0, 0.4, 31.8, 18.2, 3.4, 'glass', false);
  builder.addBox(32.2, 18.0, 0.4, 34.5, 18.2, 3.4, 'glass', false);

  // 4. Street Cover & Tactical Skill Jump Props
  // Armored SWAT / Cash Van (Hood = 1.5, Roof = 2.8)
  builder.addBox(18, 10, 0, 22, 12.5, 1.5, 'vehicle'); // Hood
  builder.addBox(18, 12.5, 0, 22, 16, 2.8, 'vehicle'); // Cab & Roof
  // Van wheels
  for (const wy of [11.0, 14.5]) {
    builder.addBox(17.6, wy, 0, 18.0, wy + 1.2, 0.75, 'concrete', true);
    builder.addBox(22.0, wy, 0, 22.4, wy + 1.2, 0.75, 'concrete', true);
  }
  // Van windshield & emergency strobe lightbar
  builder.addBox(18.2, 12.4, 1.5, 21.8, 12.7, 2.4, 'glass', false);
  builder.addBox(18.5, 13.5, 2.8, 20.0, 14.0, 3.05, 'hazard', false);
  builder.addBox(20.0, 13.5, 2.8, 21.5, 14.0, 3.05, 'vehicle', false);
  builder.addBox(17.8, 9.7, 0.3, 22.2, 10.0, 0.9, 'vault_steel', true); // Bullbar

  // Police Patrol Cruiser
  builder.addBox(42, 10, 0, 46, 14, 1.6, 'vehicle');
  builder.addBox(42.5, 11.8, 1.6, 44.0, 12.3, 1.85, 'hazard', false);
  builder.addBox(44.0, 11.8, 1.6, 45.5, 12.3, 1.85, 'vehicle', false);

  // Concrete Jersey Barrier with hazard warning top
  builder.addBox(30, 8, 0, 34, 10, 1.2, 'concrete');
  builder.addBox(30, 8, 1.15, 34, 10, 1.25, 'hazard', false);

  // 5. Grand Banking Hall (Site B) - Neoclassical Pillars, Coffered Ceiling, Teller Island
  for (const [px, py] of [[22, 24], [40, 24], [22, 40], [40, 40]]) {
    builder.addBox(px, py, 0, px + 2, py + 2, 0.8, 'marble');
    builder.addCylinder(px + 1, py + 1, 0.8, 12.5, 0.88, 12, 'marble', true);
    builder.addBox(px - 0.2, py - 0.2, 12.5, px + 2.2, py + 2.2, 14, 'marble');
  }

  // Classical Coffered Ceiling Beams
  for (const bx of [22, 32, 42]) {
    builder.addBox(bx - 0.4, 18, 13, bx + 0.4, 46, 14, 'concrete', false);
  }
  for (const by of [24, 32, 40]) {
    builder.addBox(4, by - 0.4, 13, 60, by + 0.4, 14, 'concrete', false);
  }

  // Grand Central Chandelier
  builder.addCylinder(32, 32, 11.5, 12.0, 2.2, 8, 'gold', false);
  builder.addCylinder(32, 32, 10.8, 11.5, 1.4, 8, 'gold', false);

  // Large Gold Wall-Mounted Bank Clock
  builder.addCylinder(32, 18.2, 7.5, 9.0, 0.85, 12, 'gold', false);
  builder.addCylinder(32, 18.15, 7.6, 8.9, 0.75, 12, 'marble', false);

  // Teller Counter Island (Base = 1.4, Partitions = 3.4 for skill jump)
  builder.addBox(28, 32, 0, 36, 35, 1.4, 'wood');
  builder.addBox(27.8, 31.8, 1.35, 36.2, 35.2, 1.45, 'marble', true);
  builder.addBox(28.5, 32.2, 1.4, 31.5, 32.6, 3.4, 'glass');
  builder.addBox(32.5, 32.2, 1.4, 35.5, 32.6, 3.4, 'glass');
  // Computer monitors
  for (const tx of [29.0, 30.5, 33.0, 34.5]) {
    builder.addBox(tx, 33.2, 1.45, tx + 0.6, 33.6, 1.95, 'vault_steel', false);
    builder.addBox(tx + 0.05, 33.15, 1.5, tx + 0.55, 33.2, 1.9, 'glass', false);
  }

  // Customer Queue Stanchions & Velvet Rope
  for (const qx of [28, 30, 32, 34, 36]) {
    builder.addCylinder(qx, 28.5, 0, 0.9, 0.08, 6, 'gold', false);
    builder.addCylinder(qx, 28.5, 0.85, 0.95, 0.12, 6, 'gold', false);
  }
  builder.addBox(28, 28.45, 0.7, 36, 28.55, 0.8, 'hazard', false);

  // Customer Island Writing Desks
  for (const dx of [23, 39]) {
    builder.addBox(dx, 27.5, 0, dx + 2, 29.5, 1.0, 'wood');
    builder.addBox(dx + 0.2, 28.0, 1.0, dx + 1.8, 29.0, 1.15, 'gold', false);
  }

  // Free-standing Indoor ATM Kiosks along west wall
  for (const ay of [26, 34]) {
    builder.addBox(4.8, ay, 0, 6.2, ay + 1.4, 2.1, 'vault_steel');
    builder.addBox(6.0, ay + 0.2, 1.2, 6.25, ay + 1.2, 1.8, 'glass', false);
  }

  // Waiting Lounge Area (East wall)
  builder.addBox(57, 27, 0, 59.2, 33, 0.85, 'wood');
  builder.addBox(58.5, 27, 0.85, 59.5, 33, 1.4, 'wood', false);
  builder.addBox(54.5, 28.5, 0, 56.2, 31.5, 0.5, 'glass');

  // Potted Trees in Lobby Corners
  for (const [px, py] of [[6, 20], [58, 20], [6, 44]]) {
    builder.addCylinder(px, py, 0, 0.7, 0.55, 8, 'marble', false);
    builder.addCylinder(px, py, 0.7, 2.4, 0.9, 8, 'wood', false);
  }

  // 6. Executive Mezzanine & Balconies (z = 5.0)
  builder.addFloor(14, 38, 20, 45, 5, 'marble');
  builder.addFloor(15.5, 38, 18.5, 45, 5.01, 'wood', false);
  builder.addBox(19.8, 38, 5, 20.2, 45, 6.1, 'gold');
  builder.addBox(19.9, 38, 5.2, 20.1, 45, 6.0, 'glass', false);
  builder.addRamp(14, 32, 0, 18, 38, 5, 'concrete');
  builder.addBox(14, 18, 4.8, 18, 22, 5.0, 'vault_steel');

  builder.addFloor(44, 38, 50, 45, 5, 'marble');
  builder.addFloor(45.5, 38, 48.5, 45, 5.01, 'wood', false);
  builder.addBox(43.8, 38, 5, 44.2, 45, 6.1, 'gold');
  builder.addBox(43.9, 38, 5.2, 44.1, 45, 6.0, 'glass', false);
  builder.addRamp(46, 32, 0, 50, 38, 5, 'concrete');

  // 7. Dividing Wall between Lobby and Rear Bank (y: 46..49)
  builder.addBox(4, 46, 0, 10, 49, 14, 'concrete');
  builder.addBox(16, 46, 0, 28, 49, 14, 'concrete');
  builder.addBox(36, 46, 0, 48, 49, 14, 'concrete');
  builder.addBox(54, 46, 0, 60, 49, 14, 'concrete');

  // Security Surveillance Desk
  builder.addBox(32, 46.5, 0, 35.5, 48.5, 1.1, 'vault_steel');
  builder.addBox(32.2, 47.0, 1.1, 35.3, 47.4, 1.8, 'vault_steel', false);
  builder.addBox(32.3, 47.35, 1.15, 35.2, 47.4, 1.75, 'glass', false);

  // 8. The Vault (Site A - x: 40..60, y: 49..60)
  builder.addBox(39, 49, 0, 41, 58, 10, 'concrete');
  // Vault Entrance Portal with Hazard Warning Stripes
  builder.addBox(45, 49, 0, 46.5, 51, 8, 'hazard');

  // Massive Round Vault Blast Door
  builder.addBox(46.5, 48.5, 0, 50.5, 49.5, 7.5, 'vault_steel');
  builder.addCylinder(48.5, 49.0, 1.0, 6.5, 2.0, 12, 'vault_steel', false);
  builder.addCylinder(48.5, 48.6, 3.2, 4.2, 0.55, 8, 'gold', false);
  builder.addBox(47.6, 48.5, 3.65, 49.4, 48.65, 3.85, 'gold', false);
  builder.addBox(48.4, 48.5, 2.9, 48.6, 48.65, 4.6, 'gold', false);

  // Safety Deposit Lockers
  builder.addBox(56.5, 51, 0, 58, 58, 8, 'vault_steel');
  builder.addBox(41, 56.5, 0, 56.5, 58, 8, 'vault_steel');

  // Cash & Gold Bullion Pallets (Cover inside Vault)
  builder.addBox(44, 53, 0, 47, 56, 1.8, 'gold');
  builder.addBox(44.3, 53.3, 1.8, 46.7, 55.7, 2.3, 'gold', false);

  // Wire-Mesh Money Carts
  builder.addBox(51, 52, 0, 53.5, 54.5, 1.4, 'vault_steel');
  builder.addBox(51.2, 52.2, 0.3, 53.3, 54.3, 1.35, 'wood', false);

  // HVAC Unit & Overhead Air Duct
  builder.addBox(50, 44, 0, 53, 46, 1.8, 'vault_steel');
  builder.addBox(49, 44, 3.4, 53, 48, 4.2, 'vault_steel');

  // 9. Staff Offices (Defender territory)
  builder.addBox(14, 51, 0, 18, 54, 1.2, 'wood');
  builder.addBox(15.2, 52, 1.2, 16.8, 53, 1.6, 'vault_steel', false);
  builder.addBox(13, 52, 0, 14, 53.5, 1.4, 'wood');
  builder.addBox(26, 51, 0, 30, 54, 1.1, 'wood');
  builder.addCylinder(31, 53, 0, 1.0, 0.3, 8, 'concrete', true);
  builder.addCylinder(31, 53, 1.0, 1.7, 0.26, 8, 'glass', false);

  // 10. Tactical Bomb Site Spray Decals ("A" and "B")
  builder.addFloor(
    44, 50.5, 48, 53.5, 0.02,
    'site_a',
    false,
    [0, 1, 1, 1, 1, 0, 0, 0],
  );

  builder.addFloor(
    30, 24.5, 34, 27.5, 0.02,
    'site_b',
    false,
    [0, 1, 1, 1, 1, 0, 0, 0],
  );

  const { geometries, materials, lib } = builder.build(root);

  const claSpawns: SpawnPoint[] = [
    { x: 12, y: 8, z: 0, yaw: 0, team: 0 },
    { x: 24, y: 8, z: 0, yaw: 0, team: 0 },
    { x: 36, y: 8, z: 0, yaw: 0, team: 0 },
    { x: 48, y: 8, z: 0, yaw: 0, team: 0 },
  ];

  const rvsfSpawns: SpawnPoint[] = [
    { x: 10, y: 55, z: 0, yaw: 180, team: 1 },
    { x: 20, y: 55, z: 0, yaw: 180, team: 1 },
    { x: 32, y: 55, z: 0, yaw: 180, team: 1 },
    { x: 36, y: 51, z: 0, yaw: 180, team: 1 },
  ];

  const allSpawns = [...claSpawns, ...rvsfSpawns];

  const items: ItemRow[] = [
    { id: 1, kind: 'health', x: 10, y: 14, z: 0 },
    { id: 2, kind: 'health', x: 54, y: 14, z: 0 },
    { id: 3, kind: 'health', x: 10, y: 51, z: 0 },
    { id: 4, kind: 'armour', x: 32, y: 28, z: 0 },
    { id: 5, kind: 'armour', x: 52, y: 53, z: 0 },
    { id: 6, kind: 'ammo_assault', x: 20, y: 12, z: 0 },
    { id: 7, kind: 'ammo_assault', x: 44, y: 12, z: 0 },
    { id: 8, kind: 'ammo_sniper', x: 16, y: 40, z: 5 },
    { id: 9, kind: 'clips', x: 32, y: 22, z: 0 },
    { id: 10, kind: 'grenade', x: 52, y: 28, z: 0 },
  ];

  const bounds: WorldBounds = {
    min: [4, 4, 0],
    max: [60, 60, 14],
    center: [32, 32, 5],
    extent: 40,
  };

  const collision: CollisionGeometry = {
    vertices: new Float32Array(builder.colVertices),
    indices: new Uint32Array(builder.colIndices),
    triangles: builder.colIndices.length / 3,
  };

  return {
    info,
    scene: root,
    bounds,
    collision,
    spawns: { cla: claSpawns, rvsf: rvsfSpawns, all: allSpawns },
    items,
    ladders: [],
    waterlevel: -100.0,
    materials,
    dispose() {
      for (const g of geometries) g.dispose();
      lib.dispose();
    },
  };
}

/**
 * Universal 3D Arena Factory: selects appropriate procedural 3D map generator.
 */
export function createWorld3D(
  THREE: typeof import('three'),
  info: MapInfo,
): World3D {
  if (info.name === 'hd_junkflea') {
    return createProceduralJunkFlea3D(THREE, info);
  }
  if (info.name === 'hd_bank') {
    return createProceduralBank3D(THREE, info);
  }
  return createProceduralFacility3D(THREE, info);
}
