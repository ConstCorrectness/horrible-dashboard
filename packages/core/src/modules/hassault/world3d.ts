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
 * Procedural 3D CS:GO "cs_assault" Industrial Warehouse Recreation (hd_assault):
 * 1. CT Spawn & Approach: Highway overpass (z = 8..9), street, SWAT van, rail yard & boxcar, containers.
 * 2. Warehouse Structure: Corrugated steel hangar, front & rear rolling garage doors, rear alley ladder.
 * 3. The Vents: Enclosed rooftop crawlable ductwork with dual drops into catwalk and hostage office.
 * 4. Main Warehouse Interior: High trusses, elevated catwalks (z = 4.2) with ground clearance, semi-truck & trailer, forklift.
 * 5. Back Office / Hostage Room: 2-story office with panoramic glass observation windows overlooking the floor, CCTV monitors, desks.
 */
/**
 * Procedural 3D CS:GO "cs_assault" Industrial Warehouse Recreation (hd_assault) at 1:1 scale on 128x128 grid:
 * 1. CT Spawn & Approach: Highway overpass (z = 5.5m), street with yellow lines, SWAT van, train tracks & boxcar, containers.
 * 2. Warehouse Structure: Corrugated steel hangar (56x56m), front & rear rolling garage doors.
 * 3. The Vents: Enclosed rooftop crawlable ductwork with dual drops into catwalk and hostage office.
 * 4. Main Warehouse Interior: High trusses, elevated catwalks (z = 4.0m), semi-truck & trailer, forklift.
 * 5. Back Office / Hostage Room: 2-story office with panoramic glass observation windows overlooking the floor, CCTV monitors, desks.
 */
export function createProceduralAssault3D(
  THREE: typeof import("three"),
  info: MapInfo,
): World3D {
  const root = new THREE.Group();
  root.name = "World3D_Assault";
  const builder = new World3DBuilder(THREE);

  // 1. Perimeter Boundary Walls (56x56 bounds: 4.0..60.0, height = 14.0m)
  builder.addQuad([4, 4, 0], [60, 4, 0], [60, 4, 14], [4, 4, 14], 'concrete');
  builder.addQuad([60, 60, 0], [4, 60, 0], [4, 60, 14], [60, 60, 14], 'concrete');
  builder.addQuad([60, 4, 0], [60, 60, 0], [60, 60, 14], [60, 4, 14], 'concrete');
  builder.addQuad([4, 60, 0], [4, 4, 0], [4, 4, 14], [4, 60, 14], 'concrete');

  // 2. Outside Street & Sidewalks (Human-scale 0.18m curb)
  builder.addFloor(4, 8.5, 60, 21, 0, 'asphalt');
  // Double solid yellow center lines along y = 14.75
  builder.addFloor(4, 14.65, 46, 14.75, 0.01, 'hazard', false);
  builder.addFloor(4, 14.85, 46, 14.95, 0.01, 'hazard', false);
  // White pedestrian crosswalk stripes near CT spawn (x: 14.0..18.0)
  for (let i = 0; i < 5; i++) {
    const sx = 14.0 + i * 0.9;
    builder.addFloor(sx, 8.7, sx + 0.55, 20.8, 0.01, 'concrete', false);
  }
  // Cast-iron storm drain catch basins along the curb gutter
  for (const dx of [13, 25, 37]) {
    builder.addBox(dx, 8.6, 0.005, dx + 1.2, 9.2, 0.015, 'vault_steel', false);
    for (let bar = 0; bar < 4; bar++) {
      const bx = dx + 0.15 + bar * 0.25;
      builder.addBox(bx, 8.65, 0.015, bx + 0.1, 9.15, 0.02, 'vault_steel', false);
    }
  }
  // Cast-iron circular manhole cover in street
  builder.addCylinder(26, 16, 0, 0.015, 0.45, 12, 'vault_steel', false);
  builder.addCylinder(26, 16, 0.015, 0.02, 0.35, 10, 'vault_steel', false);

  // South sidewalk (CT spawn curb at z = 0.18m)
  builder.addBox(4, 4, 0, 60, 8.5, 0.18, 'concrete');
  builder.addBox(4, 8.35, 0, 60, 8.5, 0.19, 'concrete');
  // North sidewalk (Warehouse apron curb at z = 0.18m)
  builder.addBox(4, 21, 0, 46, 22, 0.18, 'concrete');
  builder.addBox(4, 21, 0, 46, 21.15, 0.19, 'concrete');
  // Smooth pedestrian curb cut ramps for seamless movement
  builder.addRamp(13, 8.0, 0.18, 19, 8.7, 0.0, 'concrete');
  builder.addRamp(23, 8.0, 0.18, 29, 8.7, 0.0, 'concrete');
  builder.addRamp(13, 20.8, 0.0, 19, 21.5, 0.18, 'concrete');

  // Street Furniture on Sidewalk
  // Commercial 6-yard steel dumpster in forest green
  builder.addBox(10, 5, 0.18, 12.5, 7.2, 1.4, 'container');
  builder.addBox(9.95, 4.95, 1.4, 12.55, 7.25, 1.6, 'vault_steel');
  builder.addBox(9.85, 5.8, 0.5, 10, 6.4, 0.8, 'vault_steel');
  builder.addBox(12.5, 5.8, 0.5, 12.65, 6.4, 0.8, 'vault_steel');
  // Stack of wooden shipping pallets with shrink-wrapped freight
  builder.addBox(29.5, 5.2, 0.18, 31.8, 7.2, 0.5, 'crate');
  builder.addBox(29.6, 5.3, 0.5, 31.7, 7.1, 1.4, 'concrete');
  // Red municipal fire hydrant
  builder.addCylinder(13, 8, 0.18, 0.85, 0.18, 8, 'hazard', true);
  builder.addCylinder(13, 8, 0.85, 0.98, 0.12, 8, 'hazard', false);
  // Streetlight pole with curved mast arm and luminaire
  builder.addCylinder(12, 7.8, 0.18, 6.5, 0.1, 8, 'vault_steel', true);
  builder.addBox(12, 7.8, 6.3, 14.5, 8.0, 6.5, 'vault_steel', false);
  builder.addBox(14, 7.7, 6.1, 14.6, 8.1, 6.3, 'hazard', false);

  // 3. Highway Overpass (High clearance: Deck at z = 7.5m, out of CT line of sight)
  builder.addBox(4, 4, 7.2, 60, 9.5, 7.5, 'concrete');
  builder.addFloor(4, 4.2, 60, 9.3, 7.51, 'asphalt', true);
  // Concrete safety guardrail along overpass edge
  builder.addBox(4, 9.3, 7.5, 60, 9.6, 8.5, 'concrete');
  builder.addBox(4, 4, 7.5, 60, 4.3, 8.5, 'concrete');
  // Support piers tucked against south perimeter wall
  for (const px of [10, 24, 38, 52]) {
    builder.addCylinder(px, 5, 0, 7.2, 0.8, 12, 'concrete', true);
    builder.addBox(px - 1, 4, 6.8, px + 1, 9.5, 7.2, 'concrete');
  }
  // Overhead green highway sign
  builder.addBox(21, 9.6, 8.2, 27, 9.75, 9.4, 'container');
  builder.addBox(21.4, 9.76, 8.7, 26.6, 9.77, 8.85, 'concrete', false);

  // 4. Ultra-Detailed SWAT Tactical Van (parked on street at x: 19.2..24.8, y: 12.8..15.2)
  for (const wx of [23.6, 20.2]) {
    builder.addCylinder(wx, 12.75, 0, 0.76, 0.38, 10, 'vault_steel', true);
    builder.addCylinder(wx, 12.65, 0.15, 0.61, 0.23, 8, 'vault_steel', false);
    builder.addCylinder(wx, 12.63, 0.34, 0.42, 0.08, 6, 'vault_steel', false);
    builder.addCylinder(wx, 15.25, 0, 0.76, 0.38, 10, 'vault_steel', true);
    builder.addCylinder(wx, 15.35, 0.15, 0.61, 0.23, 8, 'vault_steel', false);
    builder.addCylinder(wx, 15.37, 0.34, 0.42, 0.08, 6, 'vault_steel', false);
  }
  // Wheel well fender arches
  builder.addBox(19.8, 12.7, 0.65, 20.6, 12.9, 0.85, 'vault_steel');
  builder.addBox(23.2, 12.7, 0.65, 24.0, 12.9, 0.85, 'vault_steel');
  builder.addBox(19.8, 15.1, 0.65, 20.6, 15.3, 0.85, 'vault_steel');
  builder.addBox(23.2, 15.1, 0.65, 24.0, 15.3, 0.85, 'vault_steel');

  // Black chassis
  builder.addBox(19.4, 13, 0.2, 24.6, 15, 0.5, 'vault_steel');
  // Bullbar bumper
  builder.addBox(24.8, 12.8, 0.2, 25.1, 15.2, 0.55, 'vault_steel');
  builder.addBox(24.85, 13.0, 0.55, 25.15, 13.2, 1.15, 'vault_steel');
  builder.addBox(24.85, 14.8, 0.55, 25.15, 15.0, 1.15, 'vault_steel');
  builder.addBox(24.88, 13.0, 1.05, 25.12, 15.0, 1.15, 'vault_steel');
  // Front hood
  builder.addBox(23.2, 13, 0.5, 24.8, 15, 1.3, 'container');
  builder.addBox(24.4, 13.4, 1.31, 24.7, 14.6, 1.33, 'vault_steel', false);
  // Headlights and indicators
  builder.addBox(24.81, 13.1, 0.75, 24.85, 13.5, 0.95, 'hazard');
  builder.addBox(24.81, 13.52, 0.78, 24.84, 13.7, 0.92, 'hazard');
  builder.addBox(24.81, 14.5, 0.75, 24.85, 14.9, 0.95, 'hazard');
  builder.addBox(24.81, 14.3, 0.78, 24.84, 14.48, 0.92, 'hazard');
  // Windshield
  builder.addBox(22.7, 13.1, 1.3, 23.2, 14.9, 1.9, 'glass', false);
  builder.addBox(22.68, 13.08, 1.28, 23.22, 14.92, 1.32, 'vault_steel', false);
  // Mirrors
  builder.addBox(23.0, 12.5, 1.5, 23.2, 12.9, 1.7, 'vault_steel', false);
  builder.addBox(23.0, 15.1, 1.5, 23.2, 15.5, 1.7, 'vault_steel', false);

  // Armored cab and rear compartment
  builder.addBox(19.2, 12.9, 0.5, 22.7, 15.1, 2.5, 'container');
  // Rear doors details
  builder.addBox(19.18, 13.98, 0.6, 19.21, 14.02, 2.3, 'vault_steel', false);
  builder.addBox(19.15, 13.85, 1.2, 19.20, 13.95, 1.3, 'vault_steel', false);
  builder.addBox(19.18, 13.2, 1.5, 19.21, 13.8, 1.9, 'glass', false);
  builder.addBox(19.18, 14.2, 1.5, 19.21, 14.8, 1.9, 'glass', false);
  builder.addBox(18.8, 12.8, 0.25, 19.2, 15.2, 0.52, 'vault_steel');
  builder.addBox(19.18, 12.95, 0.7, 19.21, 13.15, 1.05, 'hazard', false);
  builder.addBox(19.18, 14.85, 0.7, 19.21, 15.05, 1.05, 'hazard', false);

  // Side windows
  builder.addBox(21.8, 12.88, 1.6, 22.6, 12.92, 2.1, 'glass', false);
  builder.addBox(21.8, 15.08, 1.6, 22.6, 15.12, 2.1, 'glass', false);
  // White stripe panels
  builder.addBox(19.8, 12.88, 1.1, 22.2, 12.91, 1.35, 'concrete', false);
  builder.addBox(19.8, 15.09, 1.1, 22.2, 15.12, 1.35, 'concrete', false);
  // Emergency Lightbar
  builder.addBox(21.8, 13.2, 2.5, 22.4, 13.95, 2.72, 'hazard');
  builder.addBox(21.8, 14.05, 2.5, 22.4, 14.8, 2.72, 'container');
  builder.addBox(21.9, 13.95, 2.5, 22.3, 14.05, 2.7, 'vault_steel');

  // 5. Semi-Truck & Trailer backed into Bay 2 (x: 32.0..36.0, y: 15.0..25.0)
  builder.addBox(32.5, 15, 0.3, 35.5, 18.5, 3.2, 'concrete');
  builder.addCylinder(32.3, 18.3, 1.2, 3.8, 0.08, 8, 'vault_steel', false);
  builder.addCylinder(35.7, 18.3, 1.2, 3.8, 0.08, 8, 'vault_steel', false);
  builder.addBox(32.2, 18.5, 0.8, 35.8, 25, 3.8, 'vault_steel');

  // 6. East Rail Yard, Train Boxcar & Stacked Containers (x: 46.0..60.0, y: 10.0..58.0)
  builder.addFloor(46, 10, 60, 58, 0, 'concrete');
  for (const tx of [49, 55]) {
    builder.addBox(tx - 0.72, 10, 0, tx - 0.62, 58, 0.2, 'vault_steel', false);
    builder.addBox(tx + 0.62, 10, 0, tx + 0.72, 58, 0.2, 'vault_steel', false);
  }
  builder.addBox(53.5, 32, 0.36, 56.5, 46, 3.8, 'container');
  builder.addBox(53.4, 31.9, 3.8, 56.6, 46.1, 4.0, 'vault_steel');
  builder.addBox(56.5, 33, 0.5, 56.7, 33.5, 3.8, 'vault_steel');
  builder.addBox(47, 18, 0, 49.5, 28, 2.6, 'container');
  builder.addBox(47, 30, 0, 49.5, 40, 2.6, 'container');
  builder.addBox(47, 22, 2.6, 49.5, 32, 5.2, 'container');

  // 7. Main Warehouse Shell (x: 8.0..46.0, y: 22.0..56.0, z: 0.0..9.0m)
  builder.addFloor(8, 22, 46, 56, 0, 'concrete');

  // South Facade Realism & Architectural Articulation
  builder.addBox(8, 21.5, 0, 46, 22, 0.8, 'vault_steel');
  builder.addBox(8, 21.45, 0.75, 46, 22, 0.85, 'concrete');

  builder.addBox(8, 21.6, 0.8, 14, 22, 9, 'concrete');
  builder.addBox(14, 21.6, 3.8, 22, 22, 9, 'concrete');
  builder.addBox(22, 21.6, 0.8, 30, 22, 9, 'concrete');
  builder.addBox(30, 21.6, 4.0, 38, 22, 9, 'concrete');
  builder.addBox(38, 21.6, 2.6, 42, 22, 9, 'concrete');
  builder.addBox(42, 21.6, 0.8, 46, 22, 9, 'concrete');

  // Pilasters
  for (const px of [8, 14, 22, 30, 38, 46]) {
    builder.addBox(px - 0.25, 21.38, 0.8, px + 0.25, 21.65, 9, 'vault_steel');
    builder.addBox(px - 0.35, 21.32, 0, px + 0.35, 21.68, 0.85, 'vault_steel');
  }

  // Cornice
  builder.addBox(7.6, 21.3, 8.9, 46.4, 22.1, 9.2, 'vault_steel');

  // Clerestory Transom Windows
  for (const [wx0, wx1] of [[9, 13], [23, 29], [39, 45]]) {
    builder.addBox(wx0, 21.52, 7.2, wx1, 21.58, 8.2, 'glass', false);
    for (let i = 1; i < 4; i++) {
      const mx = wx0 + (wx1 - wx0) * i / 4;
      builder.addBox(mx - 0.06, 21.5, 7.2, mx + 0.06, 21.6, 8.2, 'vault_steel', false);
    }
    builder.addBox(wx0, 21.5, 7.68, wx1, 21.6, 7.74, 'vault_steel', false);
  }

  // Loading Bay 1 Details
  // Overhead Roll-up Shutter Drum Profile (z: 3.6..4.1m, across x: 13.8..22.2)
  builder.addBox(13.9, 21.35, 3.65, 22.1, 21.75, 4.05, 'vault_steel', false);
  builder.addBox(13.9, 21.30, 3.75, 22.1, 21.80, 3.95, 'vault_steel', false);
  builder.addBox(13.9, 21.40, 3.60, 22.1, 21.70, 4.10, 'vault_steel', false);
  builder.addBox(14, 21.54, 3.4, 22, 21.58, 3.8, 'vault_steel', false);
  builder.addBox(13.8, 21.5, 0, 14, 22, 3.8, 'hazard', false);
  builder.addBox(22, 21.5, 0, 22.2, 22, 3.8, 'hazard', false);
  for (let h = 0; h < 6; h++) {
    const hz = h * 0.6;
    builder.addBox(13.78, 21.48, hz, 14.02, 21.52, hz + 0.3, 'vault_steel', false);
    builder.addBox(21.98, 21.48, hz, 22.22, 21.52, hz + 0.3, 'vault_steel', false);
  }
  builder.addCylinder(13.5, 20.8, 0, 1.1, 0.14, 10, 'hazard', true);
  builder.addCylinder(13.5, 20.8, 0.95, 1.12, 0.15, 10, 'vault_steel', false);
  builder.addCylinder(22.5, 20.8, 0, 1.1, 0.14, 10, 'hazard', true);
  builder.addCylinder(22.5, 20.8, 0.95, 1.12, 0.15, 10, 'vault_steel', false);
  builder.addBox(17.8, 21.1, 5.3, 18.2, 21.6, 5.4, 'vault_steel', false);
  builder.addBox(17.5, 20.9, 5.0, 18.5, 21.3, 5.3, 'hazard', false);

  // Signboard
  builder.addBox(15.5, 21.42, 5.6, 24.5, 21.46, 6.8, 'concrete', false);
  builder.addBox(15.3, 21.4, 5.5, 24.7, 21.44, 5.6, 'hazard', false);
  builder.addBox(15.3, 21.4, 6.8, 24.7, 21.44, 6.9, 'hazard', false);
  builder.addBox(16, 21.47, 6.2, 24, 21.48, 6.5, 'vault_steel', false);

  // Downspouts
  builder.addCylinder(8.6, 21.45, 0.2, 9.2, 0.08, 8, 'vault_steel', false);
  builder.addCylinder(45.4, 21.45, 0.2, 9.2, 0.08, 8, 'vault_steel', false);

  // West Wall
  builder.addBox(7.6, 22, 0, 8, 56, 9, 'concrete');
  // North Wall
  builder.addBox(8, 56, 0, 24, 56.4, 9, 'concrete');
  builder.addBox(24, 56, 2.6, 27, 56.4, 9, 'concrete');
  builder.addBox(27, 56, 0, 46, 56.4, 9, 'concrete');
  // East Wall
  builder.addBox(46, 22, 0, 46.4, 44, 9, 'concrete');
  builder.addBox(46, 44, 6.8, 46.4, 47, 9, 'concrete');
  builder.addBox(46, 47, 0, 46.4, 56, 9, 'concrete');

  // Exterior Steel Fire Escape Staircase on East Wall (x: 46.4..48.8)
  builder.addRamp(46.4, 30, 0, 48.8, 36, 2.1, 'vault_steel');
  builder.addBox(46.4, 36, 2.0, 48.8, 38.5, 2.1, 'vault_steel');
  builder.addRamp(46.4, 38.5, 2.1, 48.8, 44, 4.2, 'vault_steel');
  builder.addBox(46.4, 44, 4.1, 48.8, 47, 4.2, 'vault_steel');
  builder.addRamp(46.4, 47, 4.2, 48.8, 54, 9.0, 'vault_steel');

  // Warehouse Roof (z = 9.0m) with Parapets
  builder.addFloor(8, 22, 46, 56, 9, 'concrete');
  builder.addBox(7.8, 21.8, 9, 46.2, 22.2, 9.8, 'concrete');
  builder.addBox(7.8, 55.8, 9, 46.2, 56.2, 9.8, 'concrete');
  builder.addBox(7.8, 21.8, 9, 8.2, 56.2, 9.8, 'concrete');
  builder.addBox(45.8, 21.8, 9, 46.2, 56.2, 9.8, 'concrete');

  // 8. Rooftop Ventilation System & Chillers
  builder.addBox(14, 28, 9, 18, 32, 10.2, 'vault_steel');
  builder.addBox(28, 44, 9, 32, 48, 10.2, 'vault_steel');
  builder.addBox(36, 28, 9, 40, 34, 10.4, 'vault_steel');

  // 9. Warehouse Interior Catwalks, Structural Trusses & Columns
  for (const cx of [18, 32]) {
    for (const cy of [30, 42]) {
      builder.addBox(cx - 0.35, cy - 0.35, 0, cx + 0.35, cy + 0.35, 9, 'vault_steel');
    }
  }
  for (const ty of [28, 38, 48]) {
    builder.addBox(8, ty - 0.15, 8, 46, ty + 0.15, 8.8, 'vault_steel', false);
    for (let tx = 0; tx < 6; tx++) {
      const wx0 = 10.0 + tx * 5.5;
      builder.addBox(wx0, ty - 0.08, 8.0, wx0 + 2.5, ty + 0.08, 8.8, 'vault_steel', false);
    }
  }
  // Lamps
  for (const lx of [18, 30]) {
    for (const ly of [28, 38, 48]) {
      builder.addCylinder(lx, ly, 7.6, 7.8, 0.45, 10, 'vault_steel', false);
      builder.addBox(lx - 0.2, ly - 0.2, 7.45, lx + 0.2, ly + 0.2, 7.6, 'hazard', false);
    }
  }

  // West Catwalk at z = 4.2m
  builder.addBox(8.5, 26, 4.15, 12.5, 52, 4.2, 'vault_steel');
  builder.addBox(12.4, 26, 4.2, 12.5, 52, 5.2, 'hazard');
  // North Catwalk
  builder.addBox(12.5, 49, 4.15, 28, 52.5, 4.2, 'vault_steel');
  builder.addBox(12.5, 49, 4.2, 28, 49.1, 5.2, 'hazard');
  // Interior Staircase
  builder.addRamp(10, 26, 0, 12.5, 36, 4.2, 'vault_steel');

  // 10. Upstairs Hostage Office (2nd level, x: 28.0..45.5, y: 40.0..55.5, z: 4.2..8.8m)
  builder.addFloor(28, 40, 45.5, 55.5, 4.2, 'concrete');
  builder.addBox(28, 39.8, 4.2, 45.5, 40, 5.0, 'concrete');
  builder.addBox(29, 39.85, 5.0, 44.5, 39.95, 7.2, 'glass', false);
  for (let i = 1; i < 5; i++) {
    const mx = 29.0 + 15.5 * i / 5.0;
    builder.addBox(mx - 0.08, 39.82, 5.0, mx + 0.08, 39.98, 7.2, 'vault_steel', false);
  }
  builder.addBox(29.0, 39.82, 6.05, 44.5, 39.98, 6.15, 'vault_steel', false);
  builder.addBox(28, 39.8, 7.2, 45.5, 40, 8.8, 'concrete');
  builder.addBox(28, 40, 4.2, 28.4, 48, 8.8, 'concrete');
  builder.addBox(28, 48, 6.6, 28.4, 51, 8.8, 'concrete');
  builder.addBox(28, 51, 4.2, 28.4, 55.5, 8.8, 'concrete');
  // Office furniture
  for (const dx of [33, 39]) {
    builder.addBox(dx - 1.2, 44, 4.2, dx + 1.2, 45.5, 5.0, 'wood');
    builder.addBox(dx - 0.4, 44.8, 5.0, dx + 0.4, 45, 5.6, 'vault_steel');
  }
  builder.addBox(34, 54.8, 5.4, 38, 55, 6.6, 'glass', false);

  // 11. Tactical Cover Props
  builder.addBox(16, 34, 0, 18.5, 36.5, 1.4, 'hazard');
  builder.addBox(16.2, 34.2, 1.4, 18.3, 36.3, 2.2, 'vault_steel');
  builder.addBox(17.0, 36.5, 0.0, 17.5, 36.8, 2.8, 'vault_steel');
  builder.addBox(16.4, 36.8, 0.1, 18.1, 38.0, 0.2, 'vault_steel');
  builder.addBox(8.2, 38, 0, 10.2, 48, 3.8, 'container');
  builder.addBox(8.15, 38, 1.8, 10.25, 48, 1.95, 'hazard', false);
  builder.addBox(8.15, 38, 3.6, 10.25, 48, 3.75, 'hazard', false);
  builder.addBox(22, 34, 0, 24.5, 36.5, 1.6, 'crate');
  builder.addBox(14, 42, 0, 16.5, 44.5, 1.8, 'crate');
  for (const bx of [13, 36, 51]) {
    builder.addCylinder(bx, 24, 0, 0.9, 0.32, 10, 'container', true);
    builder.addCylinder(bx, 24, 0.88, 0.92, 0.34, 10, 'vault_steel', false);
  }

  // Site decals
  builder.addFloor(34, 46, 40, 52, 4.21, 'site_a', false, [0, 1, 1, 1, 1, 0, 0, 0]);
  builder.addFloor(18, 32, 24, 38, 0.01, 'site_b', false, [0, 1, 1, 1, 1, 0, 0, 0]);

  const { geometries, materials, lib } = builder.build(root);

  // CT spawns at South street facing North (yaw = 0.0), T spawns in Hostage Office facing South (yaw = 180.0)
  const ctSpawns: SpawnPoint[] = [
    { x: 16, y: 7, z: 0.18, yaw: 90, team: 0 },
    { x: 20, y: 7, z: 0.18, yaw: 90, team: 0 },
    { x: 24, y: 7, z: 0.18, yaw: 90, team: 0 },
    { x: 28, y: 7, z: 0.18, yaw: 90, team: 0 },
  ];

  const tSpawns: SpawnPoint[] = [
    { x: 32, y: 48, z: 4.2, yaw: 270, team: 1 },
    { x: 36, y: 48, z: 4.2, yaw: 270, team: 1 },
    { x: 40, y: 48, z: 4.2, yaw: 270, team: 1 },
    { x: 34, y: 52, z: 4.2, yaw: 270, team: 1 },
  ];

  const allSpawns = [...ctSpawns, ...tSpawns];

  const items: ItemRow[] = [
    { id: 1, kind: "health", x: 14, y: 6, z: 0.18 },
    { id: 2, kind: "health", x: 55, y: 16, z: 0 },
    { id: 3, kind: "health", x: 38, y: 52, z: 4.2 },
    { id: 4, kind: "armour", x: 20, y: 36, z: 0 },
    { id: 5, kind: "armour", x: 32, y: 44, z: 4.2 },
    { id: 6, kind: "ammo_assault", x: 21, y: 15.5, z: 0 },
    { id: 7, kind: "ammo_assault", x: 54, y: 30, z: 0 },
    { id: 8, kind: "ammo_sniper", x: 24, y: 7, z: 7.5 },
    { id: 9, kind: "clips", x: 10.5, y: 40, z: 4.2 },
    { id: 10, kind: "grenade", x: 16, y: 30, z: 9.0 },
  ];

  const bounds: WorldBounds = {
    min: [4, 4, 0],
    max: [60, 60, 14],
    center: [32, 32, 7],
    extent: 56,
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
    spawns: { cla: ctSpawns, rvsf: tSpawns, all: allSpawns },
    items,
    ladders: [{ x: 46.4, y: 36, base: 0, top: 9 }],
    waterlevel: -100.0,
    materials,
    dispose() {
      for (const g of geometries) g.dispose();
      lib.dispose();
    },
  };
}

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
  if (info.name === 'hd_assault') {
    return createProceduralAssault3D(THREE, info);
  }
  return createProceduralFacility3D(THREE, info);
}
