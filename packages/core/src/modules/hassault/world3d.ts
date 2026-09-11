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

  addQuad(
    p0: [number, number, number],
    p1: [number, number, number],
    p2: [number, number, number],
    p3: [number, number, number],
    material: MaterialKind = 'concrete',
    isCollider = true,
    customUVs?: [number, number, number, number, number, number, number, number],
  ) {
    const t0 = [p0[0], p0[2], p0[1]];
    const t1 = [p1[0], p1[2], p1[1]];
    const t2 = [p2[0], p2[2], p2[1]];
    const t3 = [p3[0], p3[2], p3[1]];

    const vA = new this.THREE.Vector3(t1[0] - t0[0], t1[1] - t0[1], t1[2] - t0[2]);
    const vB = new this.THREE.Vector3(t2[0] - t0[0], t2[1] - t0[1], t2[2] - t0[2]);
    const norm = new this.THREE.Vector3().crossVectors(vA, vB).normalize();

    let uv0: [number, number], uv1: [number, number], uv2: [number, number], uv3: [number, number];
    if (customUVs) {
      uv0 = [customUVs[0], customUVs[1]];
      uv1 = [customUVs[2], customUVs[3]];
      uv2 = [customUVs[4], customUVs[5]];
      uv3 = [customUVs[6], customUVs[7]];
    } else {
      const tileScale = material === 'hazard' || material === 'crate' ? 2.0 : 4.0;
      uv0 = computePlanarUV(p0, norm, tileScale);
      uv1 = computePlanarUV(p1, norm, tileScale);
      uv2 = computePlanarUV(p2, norm, tileScale);
      uv3 = computePlanarUV(p3, norm, tileScale);
    }

    const batch = this.getBatch(material);

    // Triangle 1: t0, t1, t2
    batch.positions.push(...t0, ...t1, ...t2);
    batch.normals.push(norm.x, norm.y, norm.z, norm.x, norm.y, norm.z, norm.x, norm.y, norm.z);
    batch.uvs.push(...uv0, ...uv1, ...uv2);

    // Triangle 2: t0, t2, t3
    batch.positions.push(...t0, ...t2, ...t3);
    batch.normals.push(norm.x, norm.y, norm.z, norm.x, norm.y, norm.z, norm.x, norm.y, norm.z);
    batch.uvs.push(...uv0, ...uv2, ...uv3);

    if (isCollider) {
      const bIdx = this.colIndexOffset;
      this.colVertices.push(...p0, ...p1, ...p2, ...p3);
      this.colIndices.push(bIdx, bIdx + 1, bIdx + 2, bIdx, bIdx + 2, bIdx + 3);
      this.colIndexOffset += 4;
    }
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

  addRamp(
    minX: number, minY: number, z0: number,
    maxX: number, maxY: number, z1: number,
    material: MaterialKind = 'concrete',
    isCollider = true,
  ) {
    this.addQuad([minX, minY, z0], [maxX, minY, z0], [maxX, maxY, z1], [minX, maxY, z1], material, isCollider);
    this.addQuad([minX, maxY, z1], [minX, maxY, Math.min(z0, z1)], [minX, minY, Math.min(z0, z1)], [minX, minY, z0], material, isCollider);
    this.addQuad([maxX, minY, z0], [maxX, minY, Math.min(z0, z1)], [maxX, maxY, Math.min(z0, z1)], [maxX, maxY, z1], material, isCollider);
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
  builder.addQuad([0, 0, 0], [20, 0, 0], [20, 64, 0], [0, 64, 0], 'concrete');
  builder.addQuad([44, 0, 0], [64, 0, 0], [64, 64, 0], [44, 64, 0], 'concrete');
  builder.addQuad([20, 0, 0], [44, 0, 0], [44, 20, 0], [20, 20, 0], 'concrete');
  builder.addQuad([20, 44, 0], [44, 44, 0], [44, 64, 0], [20, 64, 0], 'concrete');

  // 3. Lower Coolant Pit (z = -5)
  builder.addQuad([20, 20, -5], [44, 20, -5], [44, 44, -5], [20, 44, -5], 'vault_steel');
  builder.addQuad([20, 20, 0], [44, 20, 0], [44, 20, -5], [20, 20, -5], 'vault_steel');
  builder.addQuad([44, 44, 0], [20, 44, 0], [20, 44, -5], [44, 44, -5], 'vault_steel');
  builder.addQuad([44, 20, 0], [44, 44, 0], [44, 44, -5], [44, 20, -5], 'vault_steel');
  builder.addQuad([20, 44, 0], [20, 20, 0], [20, 20, -5], [20, 44, -5], 'vault_steel');

  // Ramps into the Pit
  builder.addRamp(28, 14, 0, 36, 20, -5, 'vault_steel');
  builder.addRamp(28, 44, -5, 36, 50, 0, 'vault_steel');

  // 4. Upper Mezzanine / Catwalk (z = 6, width = 6 around perimeter)
  builder.addQuad([0, 0, 6], [64, 0, 6], [64, 6, 6], [0, 6, 6], 'vault_steel');
  builder.addQuad([0, 58, 6], [64, 58, 6], [64, 64, 6], [0, 64, 6], 'vault_steel');
  builder.addQuad([0, 6, 6], [6, 6, 6], [6, 58, 6], [0, 58, 6], 'vault_steel');
  builder.addQuad([58, 6, 6], [64, 6, 6], [64, 58, 6], [58, 58, 6], 'vault_steel');

  // Cross-Bridge over the arena at z = 6
  builder.addQuad([26, 6, 6], [38, 6, 6], [38, 58, 6], [26, 58, 6], 'vault_steel');

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
  builder.addQuad([4, 4, 0], [16, 4, 0], [16, 60, 0], [4, 60, 0], 'concrete');
  builder.addQuad([48, 4, 0], [60, 4, 0], [60, 60, 0], [48, 60, 0], 'concrete');
  builder.addQuad([22, 4, 0], [42, 4, 0], [42, 60, 0], [22, 60, 0], 'concrete');

  builder.addQuad([16, 4, 0], [22, 4, 0], [22, 14, 0], [16, 14, 0], 'concrete');
  builder.addQuad([16, 50, 0], [22, 50, 0], [22, 60, 0], [16, 60, 0], 'concrete');
  builder.addQuad([42, 4, 0], [48, 4, 0], [48, 14, 0], [42, 14, 0], 'concrete');
  builder.addQuad([42, 50, 0], [48, 50, 0], [48, 60, 0], [42, 60, 0], 'concrete');

  // 3. Subterranean Trenches (z = -2.0)
  builder.addQuad([16, 20, -2], [22, 20, -2], [22, 44, -2], [16, 44, -2], 'concrete');
  builder.addQuad([16, 20, 0], [16, 44, 0], [16, 44, -2], [16, 20, -2], 'concrete');
  builder.addQuad([22, 44, 0], [22, 20, 0], [22, 20, -2], [22, 44, -2], 'concrete');
  builder.addRamp(16, 14, 0, 22, 20, -2, 'vault_steel');
  builder.addRamp(16, 44, -2, 22, 50, 0, 'vault_steel');

  builder.addQuad([42, 20, -2], [48, 20, -2], [48, 44, -2], [42, 44, -2], 'concrete');
  builder.addQuad([42, 20, 0], [42, 44, 0], [42, 44, -2], [42, 20, -2], 'concrete');
  builder.addQuad([48, 44, 0], [48, 20, 0], [48, 20, -2], [48, 44, -2], 'concrete');
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
  builder.addQuad([30, 14, 6.4], [34, 14, 6.4], [34, 50, 6.4], [30, 50, 6.4], 'vault_steel');
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

  // 2. Ground Floors (Street Asphalt vs Bank Polished Marble Floor)
  builder.addQuad([4, 4, 0], [60, 4, 0], [60, 18, 0], [4, 18, 0], 'asphalt');
  builder.addBox(4, 14, 0, 60, 18, 0.2, 'concrete'); // Sidewalk Curb
  builder.addQuad([4, 18, 0], [60, 18, 0], [60, 46, 0], [4, 46, 0], 'marble');
  builder.addQuad([4, 46, 0], [40, 46, 0], [40, 60, 0], [4, 60, 0], 'concrete');
  builder.addQuad([40, 46, 0], [60, 46, 0], [60, 60, 0], [40, 60, 0], 'vault_steel');

  // 3. Bank Exterior Facade Wall (y: 18..22) with 3 Entrances
  builder.addBox(4, 18, 0, 8, 22, 14, 'concrete');
  builder.addBox(14, 18, 0, 28, 22, 14, 'concrete');
  builder.addBox(36, 18, 0, 50, 22, 14, 'concrete');
  builder.addBox(56, 18, 0, 60, 22, 14, 'concrete');
  builder.addBox(26, 16, 0, 28, 18, 12, 'marble'); // Grand Classical Columns
  builder.addBox(36, 16, 0, 38, 18, 12, 'marble');
  builder.addBox(25, 15, 4.2, 39, 18.5, 4.8, 'concrete'); // Canopy skill-jump platform

  // 4. Street Vehicles & Tactical Skill Jump Props
  builder.addBox(18, 10, 0, 22, 12.5, 1.5, 'vehicle'); // SWAT Van Hood
  builder.addBox(18, 12.5, 0, 22, 16, 2.8, 'vehicle'); // SWAT Van Cab / Roof
  builder.addBox(42, 10, 0, 46, 14, 1.6, 'vehicle');  // Police Cruiser
  builder.addBox(30, 8, 0, 34, 10, 1.2, 'concrete');   // Jersey Barrier

  // 5. Grand Banking Hall (Site B) - Marble Pillars & Mahogany Teller Counter
  builder.addBox(22, 24, 0, 24, 26, 14, 'marble');
  builder.addBox(40, 24, 0, 42, 26, 14, 'marble');
  builder.addBox(22, 40, 0, 24, 42, 14, 'marble');
  builder.addBox(40, 40, 0, 42, 42, 14, 'marble');
  // Teller Counter Island (Rich Mahogany Wood with Bulletproof Glass)
  builder.addBox(28, 32, 0, 36, 35, 1.4, 'wood');
  builder.addBox(28.5, 32.2, 1.4, 31.5, 32.6, 3.4, 'glass');
  builder.addBox(32.5, 32.2, 1.4, 35.5, 32.6, 3.4, 'glass');

  // 6. Executive Mezzanine & Balconies (z = 5.0)
  builder.addQuad([14, 38, 5], [20, 38, 5], [20, 45, 5], [14, 45, 5], 'marble');
  builder.addBox(19.8, 38, 5, 20.2, 45, 6.1, 'wood'); // Balcony railing
  builder.addRamp(14, 32, 0, 18, 38, 5, 'concrete');   // West stairs
  builder.addBox(14, 18, 4.8, 18, 22, 5.0, 'vault_steel'); // Fire escape connection
  builder.addQuad([44, 38, 5], [50, 38, 5], [50, 45, 5], [44, 45, 5], 'marble');
  builder.addBox(43.8, 38, 5, 44.2, 45, 6.1, 'wood'); // Balcony railing
  builder.addRamp(46, 32, 0, 50, 38, 5, 'concrete');   // East stairs

  // 7. Dividing Wall between Lobby and Rear Bank (y: 46..49)
  builder.addBox(4, 46, 0, 10, 49, 14, 'concrete');
  builder.addBox(16, 46, 0, 28, 49, 14, 'concrete');
  builder.addBox(36, 46, 0, 48, 49, 14, 'concrete');
  builder.addBox(54, 46, 0, 60, 49, 14, 'concrete');

  // 8. The Vault (Site A)
  builder.addBox(39, 49, 0, 41, 58, 10, 'concrete');
  builder.addBox(45, 49, 0, 46.5, 51, 8, 'hazard'); // Vault archway with warning hazard stripes!
  builder.addBox(46.5, 48.5, 0, 50.5, 49.5, 7.5, 'vault_steel'); // Heavy blast door slab
  builder.addBox(56.5, 51, 0, 58, 58, 8, 'vault_steel'); // Deposit lockers
  builder.addBox(41, 56.5, 0, 56.5, 58, 8, 'vault_steel');
  builder.addBox(44, 53, 0, 47, 56, 1.8, 'gold'); // Pallets of gleaming gold bullion!
  builder.addBox(50, 44, 0, 53, 46, 1.8, 'vault_steel'); // HVAC Unit
  builder.addBox(49, 44, 3.4, 53, 48, 4.2, 'vault_steel'); // Overhead duct for jump shooting

  // 9. Staff Offices (Defender territory)
  builder.addBox(14, 51, 0, 18, 54, 1.5, 'wood');
  builder.addBox(26, 51, 0, 30, 54, 1.5, 'wood');

  // 10. Tactical Bomb Site Spray Decals ("A" and "B")
  // Site A: On the Vault floor in front of gold pallets
  builder.addQuad(
    [43, 50, 0.02],
    [48, 50, 0.02],
    [48, 55, 0.02],
    [43, 55, 0.02],
    'site_a',
    false,
    [0, 0, 1, 0, 1, 1, 0, 1],
  );

  // Site B: Centered on the Grand Banking Hall marble floor
  builder.addQuad(
    [29, 26, 0.02],
    [34, 26, 0.02],
    [34, 31, 0.02],
    [29, 31, 0.02],
    'site_b',
    false,
    [0, 0, 1, 0, 1, 1, 0, 1],
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
