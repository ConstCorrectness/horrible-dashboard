/**
 * True 3D World Representation & Loader for HorribleAssault.
 *
 * Transcends Cube 1's 2.5D column heightfield limitations by supporting arbitrary
 * 3D polygonal geometry (glTF / GLB) with verticality: multi-story buildings,
 * mezzanine catwalks, stairs, underground conduits, and dynamic cover.
 *
 * Coordinate Convention:
 * Game coordinates: (x, y) horizontal grid/plane, z is elevation/height (z-up).
 * Three.js coordinates: three.x = x, three.y = z (height), three.z = y.
 */
import type * as THREE from 'three';
import type { MapInfo } from './api';
import type { Ladder } from './world';
import type { ItemRow } from './net';

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
  dispose(): void;
}

/**
 * Procedural 3D Facility ("The Deadzone Facility"):
 * An inaugural true-3D arena featuring:
 * 1. Lower Coolant Trench (z = -5) with water and ramps.
 * 2. Ground Floor Server Core (z = 0) with pillars, barriers, and hallways.
 * 3. Upper Mezzanine & Catwalks (z = 6) with elevated perimeter firing positions.
 * 4. Staircases connecting all three vertical tiers.
 */
export function createProceduralFacility3D(
  THREE: typeof import('three'),
  info: MapInfo,
): World3D {
  const root = new THREE.Group();
  root.name = 'World3D_Facility';

  const positions: number[] = [];
  const normals: number[] = [];
  const colors: number[] = [];
  const uvs: number[] = [];
  const colVertices: number[] = [];
  const colIndices: number[] = [];

  let indexOffset = 0;

  function addQuad(
    p0: [number, number, number],
    p1: [number, number, number],
    p2: [number, number, number],
    p3: [number, number, number],
    color: [number, number, number] = [0.35, 0.38, 0.42],
    isCollider = true,
  ) {
    // p: [game.x, game.y, game.z] -> three: [p[0], p[2], p[1]]
    const t0 = [p0[0], p0[2], p0[1]];
    const t1 = [p1[0], p1[2], p1[1]];
    const t2 = [p2[0], p2[2], p2[1]];
    const t3 = [p3[0], p3[2], p3[1]];

    // Normal in Three.js space
    const vA = new THREE.Vector3(t1[0] - t0[0], t1[1] - t0[1], t1[2] - t0[2]);
    const vB = new THREE.Vector3(t2[0] - t0[0], t2[1] - t0[1], t2[2] - t0[2]);
    const norm = new THREE.Vector3().crossVectors(vA, vB).normalize();

    // Triangle 1: t0, t1, t2
    positions.push(...t0, ...t1, ...t2);
    normals.push(norm.x, norm.y, norm.z, norm.x, norm.y, norm.z, norm.x, norm.y, norm.z);
    colors.push(...color, ...color, ...color);
    uvs.push(0, 0, 1, 0, 1, 1);

    // Triangle 2: t0, t2, t3
    positions.push(...t0, ...t2, ...t3);
    normals.push(norm.x, norm.y, norm.z, norm.x, norm.y, norm.z, norm.x, norm.y, norm.z);
    colors.push(...color, ...color, ...color);
    uvs.push(0, 0, 1, 1, 0, 1);

    if (isCollider) {
      // In game coordinates for physics simulation (x, y, z)
      colVertices.push(...p0, ...p1, ...p2, ...p3);
      colIndices.push(
        indexOffset, indexOffset + 1, indexOffset + 2,
        indexOffset, indexOffset + 2, indexOffset + 3,
      );
      indexOffset += 4;
    }
  }

  function addBox(
    minX: number, minY: number, minZ: number,
    maxX: number, maxY: number, maxZ: number,
    color: [number, number, number] = [0.4, 0.44, 0.48],
  ) {
    // Floor
    addQuad([minX, minY, minZ], [maxX, minY, minZ], [maxX, maxY, minZ], [minX, maxY, minZ], color);
    // Ceiling / Top
    addQuad([minX, maxY, maxZ], [maxX, maxY, maxZ], [maxX, minY, maxZ], [minX, minY, maxZ], color);
    // North (max Y)
    addQuad([maxX, maxY, minZ], [maxX, maxY, maxZ], [minX, maxY, maxZ], [minX, maxY, minZ], color);
    // South (min Y)
    addQuad([minX, minY, minZ], [minX, minY, maxZ], [maxX, minY, maxZ], [maxX, minY, minZ], color);
    // East (max X)
    addQuad([maxX, minY, minZ], [maxX, minY, maxZ], [maxX, maxY, maxZ], [maxX, maxY, minZ], color);
    // West (min X)
    addQuad([minX, maxY, minZ], [minX, maxY, maxZ], [minX, minY, maxZ], [minX, minY, minZ], color);
  }

  function addRamp(
    minX: number, minY: number, z0: number,
    maxX: number, maxY: number, z1: number,
    color: [number, number, number] = [0.45, 0.42, 0.38],
  ) {
    // Slope quad
    addQuad([minX, minY, z0], [maxX, minY, z0], [maxX, maxY, z1], [minX, maxY, z1], color);
    // Side walls
    addQuad([minX, maxY, z1], [minX, maxY, Math.min(z0, z1)], [minX, minY, Math.min(z0, z1)], [minX, minY, z0], color);
    addQuad([maxX, minY, z0], [maxX, minY, Math.min(z0, z1)], [maxX, maxY, Math.min(z0, z1)], [maxX, maxY, z1], color);
  }

  // Arena Geometry: 64x64 world
  // 1. Outer Perimeter Walls (height = 14)
  addQuad([0, 0, 0], [64, 0, 0], [64, 0, 14], [0, 0, 14], [0.22, 0.24, 0.28]); // South
  addQuad([64, 64, 0], [0, 64, 0], [0, 64, 14], [64, 64, 14], [0.22, 0.24, 0.28]); // North
  addQuad([64, 0, 0], [64, 64, 0], [64, 64, 14], [64, 0, 14], [0.25, 0.27, 0.31]); // East
  addQuad([0, 64, 0], [0, 0, 0], [0, 0, 14], [0, 64, 14], [0.25, 0.27, 0.31]); // West

  // 2. Ground Floor (z = 0), with central pit opening [20..44, 20..44]
  addQuad([0, 0, 0], [20, 0, 0], [20, 64, 0], [0, 64, 0], [0.32, 0.34, 0.36]); // West strip
  addQuad([44, 0, 0], [64, 0, 0], [64, 64, 0], [44, 64, 0], [0.32, 0.34, 0.36]); // East strip
  addQuad([20, 0, 0], [44, 0, 0], [44, 20, 0], [20, 20, 0], [0.30, 0.32, 0.35]); // South middle
  addQuad([20, 44, 0], [44, 44, 0], [44, 64, 0], [20, 64, 0], [0.30, 0.32, 0.35]); // North middle

  // 3. Lower Coolant Pit (z = -5)
  addQuad([20, 20, -5], [44, 20, -5], [44, 44, -5], [20, 44, -5], [0.18, 0.22, 0.26]); // Pit floor
  // Pit walls
  addQuad([20, 20, 0], [44, 20, 0], [44, 20, -5], [20, 20, -5], [0.28, 0.30, 0.34]);
  addQuad([44, 44, 0], [20, 44, 0], [20, 44, -5], [44, 44, -5], [0.28, 0.30, 0.34]);
  addQuad([44, 20, 0], [44, 44, 0], [44, 44, -5], [44, 20, -5], [0.28, 0.30, 0.34]);
  addQuad([20, 44, 0], [20, 20, 0], [20, 20, -5], [20, 44, -5], [0.28, 0.30, 0.34]);

  // Ramps into the Pit (South & North)
  addRamp(28, 14, 0, 36, 20, -5, [0.42, 0.38, 0.32]);
  addRamp(28, 44, -5, 36, 50, 0, [0.42, 0.38, 0.32]);

  // 4. Upper Mezzanine / Catwalk (z = 6, width = 6 around perimeter)
  addQuad([0, 0, 6], [64, 0, 6], [64, 6, 6], [0, 6, 6], [0.48, 0.46, 0.44]); // South catwalk
  addQuad([0, 58, 6], [64, 58, 6], [64, 64, 6], [0, 64, 6], [0.48, 0.46, 0.44]); // North catwalk
  addQuad([0, 6, 6], [6, 6, 6], [6, 58, 6], [0, 58, 6], [0.46, 0.44, 0.42]); // West catwalk
  addQuad([58, 6, 6], [64, 6, 6], [64, 58, 6], [58, 58, 6], [0.46, 0.44, 0.42]); // East catwalk

  // Cross-Bridges over the arena at z = 6 (true multi-tier verticality!)
  addQuad([26, 6, 6], [38, 6, 6], [38, 58, 6], [26, 58, 6], [0.52, 0.50, 0.46]);

  // Ramps leading from Ground (z = 0) to Catwalk (z = 6)
  addRamp(2, 10, 0, 6, 26, 6, [0.45, 0.40, 0.35]); // West ramp
  addRamp(58, 38, 6, 62, 54, 0, [0.45, 0.40, 0.35]); // East ramp

  // 5. Tactical Cover Blocks on Ground Floor
  addBox(10, 12, 0, 14, 16, 3, [0.60, 0.48, 0.28]); // Tactical crate 1
  addBox(50, 48, 0, 54, 52, 3, [0.60, 0.48, 0.28]); // Tactical crate 2
  addBox(12, 48, 0, 16, 52, 3.5, [0.38, 0.42, 0.46]); // Server stack 1
  addBox(48, 12, 0, 52, 16, 3.5, [0.38, 0.42, 0.46]); // Server stack 2

  // Pillars supporting the cross-bridge
  addBox(24, 22, 0, 26, 24, 6, [0.25, 0.28, 0.32]);
  addBox(38, 22, 0, 40, 24, 6, [0.25, 0.28, 0.32]);
  addBox(24, 40, 0, 26, 42, 6, [0.25, 0.28, 0.32]);
  addBox(38, 40, 0, 40, 42, 6, [0.25, 0.28, 0.32]);

  // Build Three.js Mesh
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(positions), 3));
  geo.setAttribute('normal', new THREE.BufferAttribute(new Float32Array(normals), 3));
  geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(colors), 3));
  geo.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(uvs), 2));
  geo.computeBoundingSphere();
  geo.computeBoundingBox();

  const mat = new THREE.MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.72,
    metalness: 0.18,
  });

  const mesh = new THREE.Mesh(geo, mat);
  mesh.frustumCulled = true;
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  root.add(mesh);

  // Spawns (CLA at South, RVSF at North, Catwalk spawns)
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

  // Pickups across tiers
  const items: ItemRow[] = [
    { id: 1, kind: 'ammo_assault', x: 32, y: 32, z: 6 }, // Center catwalk bridge
    { id: 2, kind: 'armour', x: 32, y: 32, z: -5 }, // Center coolant pit
    { id: 3, kind: 'health', x: 8, y: 32, z: 0 }, // West hall
    { id: 4, kind: 'health', x: 56, y: 32, z: 0 }, // East hall
    { id: 5, kind: 'ammo_sniper', x: 4, y: 4, z: 6 }, // Southwest sniper nest
    { id: 6, kind: 'ammo_sniper', x: 60, y: 60, z: 6 }, // Northeast sniper nest
  ];

  const bounds: WorldBounds = {
    min: [0, 0, -5],
    max: [64, 64, 14],
    center: [32, 32, 4.5],
    extent: 42,
  };

  const collision: CollisionGeometry = {
    vertices: new Float32Array(colVertices),
    indices: new Uint32Array(colIndices),
    triangles: colIndices.length / 3,
  };

  return {
    info,
    scene: root,
    bounds,
    collision,
    spawns: {
      cla: claSpawns,
      rvsf: rvsfSpawns,
      all: allSpawns,
    },
    items,
    ladders: [],
    waterlevel: -3.5, // Water in the lower coolant basin
    dispose() {
      geo.dispose();
      mat.dispose();
    },
  };
}

/**
 * Procedural 3D Junk Flea:
 * Inspired by Combat Arms' legendary high-intensity CQB junkyard arena.
 * 1. Dual Subterranean Trench Crawlspaces (z = -2.5).
 * 2. Dense Ground Shipping Container Maze (z = 0.0, height = 3.2).
 * 3. Elevated Steel Spanning Bridge & Perches (z = 6.4).
 * 4. Connecting Ramps for slide-jump mobility and high APM flanks.
 */
export function createProceduralJunkFlea3D(
  THREE: typeof import('three'),
  info: MapInfo,
): World3D {
  const root = new THREE.Group();
  root.name = 'World3D_JunkFlea';

  const positions: number[] = [];
  const normals: number[] = [];
  const colors: number[] = [];
  const uvs: number[] = [];
  const colVertices: number[] = [];
  const colIndices: number[] = [];

  let indexOffset = 0;

  function addQuad(
    p0: [number, number, number],
    p1: [number, number, number],
    p2: [number, number, number],
    p3: [number, number, number],
    color: [number, number, number] = [0.42, 0.40, 0.36],
    isCollider = true,
  ) {
    const t0 = [p0[0], p0[2], p0[1]];
    const t1 = [p1[0], p1[2], p1[1]];
    const t2 = [p2[0], p2[2], p2[1]];
    const t3 = [p3[0], p3[2], p3[1]];

    const vA = new THREE.Vector3(t1[0] - t0[0], t1[1] - t0[1], t1[2] - t0[2]);
    const vB = new THREE.Vector3(t2[0] - t0[0], t2[1] - t0[1], t2[2] - t0[2]);
    const norm = new THREE.Vector3().crossVectors(vA, vB).normalize();

    positions.push(...t0, ...t1, ...t2);
    normals.push(norm.x, norm.y, norm.z, norm.x, norm.y, norm.z, norm.x, norm.y, norm.z);
    colors.push(...color, ...color, ...color);
    uvs.push(0, 0, 1, 0, 1, 1);

    positions.push(...t0, ...t2, ...t3);
    normals.push(norm.x, norm.y, norm.z, norm.x, norm.y, norm.z, norm.x, norm.y, norm.z);
    colors.push(...color, ...color, ...color);
    uvs.push(0, 0, 1, 1, 0, 1);

    if (isCollider) {
      const bIdx = indexOffset;
      colVertices.push(...p0, ...p1, ...p2, ...p3);
      colIndices.push(bIdx, bIdx + 1, bIdx + 2, bIdx, bIdx + 2, bIdx + 3);
      indexOffset += 4;
    }
  }

  function addBox(
    minX: number, minY: number, minZ: number,
    maxX: number, maxY: number, maxZ: number,
    color: [number, number, number] = [0.45, 0.38, 0.30],
  ) {
    addQuad([minX, minY, minZ], [maxX, minY, minZ], [maxX, maxY, minZ], [minX, maxY, minZ], color);
    addQuad([minX, maxY, maxZ], [maxX, maxY, maxZ], [maxX, minY, maxZ], [minX, minY, maxZ], color);
    addQuad([maxX, maxY, minZ], [maxX, maxY, maxZ], [minX, maxY, maxZ], [minX, maxY, minZ], color);
    addQuad([minX, minY, minZ], [minX, minY, maxZ], [maxX, minY, maxZ], [maxX, minY, minZ], color);
    addQuad([maxX, minY, minZ], [maxX, minY, maxZ], [maxX, maxY, maxZ], [maxX, maxY, minZ], color);
    addQuad([minX, maxY, minZ], [minX, maxY, maxZ], [minX, minY, maxZ], [minX, minY, minZ], color);
  }

  function addRamp(
    minX: number, minY: number, z0: number,
    maxX: number, maxY: number, z1: number,
    color: [number, number, number] = [0.48, 0.44, 0.38],
  ) {
    addQuad([minX, minY, z0], [maxX, minY, z0], [maxX, maxY, z1], [minX, maxY, z1], color);
    addQuad([minX, maxY, z1], [minX, maxY, Math.min(z0, z1)], [minX, minY, Math.min(z0, z1)], [minX, minY, z0], color);
    addQuad([maxX, minY, z0], [maxX, minY, Math.min(z0, z1)], [maxX, maxY, Math.min(z0, z1)], [maxX, maxY, z1], color);
  }

  // 1. Perimeter Concrete/Steel Walls (height = 14)
  addQuad([4, 4, 0], [60, 4, 0], [60, 4, 14], [4, 4, 14], [0.24, 0.22, 0.20]); // South
  addQuad([60, 60, 0], [4, 60, 0], [4, 60, 14], [60, 60, 14], [0.24, 0.22, 0.20]); // North
  addQuad([60, 4, 0], [60, 60, 0], [60, 60, 14], [60, 4, 14], [0.26, 0.24, 0.22]); // East
  addQuad([4, 60, 0], [4, 4, 0], [4, 4, 14], [4, 60, 14], [0.26, 0.24, 0.22]); // West

  // 2. Ground Floor (z = 0) with Trench cutouts at x in [16..22] and [42..48], y in [20..44]
  addQuad([4, 4, 0], [16, 4, 0], [16, 60, 0], [4, 60, 0], [0.35, 0.33, 0.30]); // West strip
  addQuad([48, 4, 0], [60, 4, 0], [60, 60, 0], [48, 60, 0], [0.35, 0.33, 0.30]); // East strip
  addQuad([22, 4, 0], [42, 4, 0], [42, 60, 0], [22, 60, 0], [0.33, 0.31, 0.28]); // Center strip

  // North & South ground connectors across trenches
  addQuad([16, 4, 0], [22, 4, 0], [22, 14, 0], [16, 14, 0], [0.33, 0.31, 0.28]);
  addQuad([16, 50, 0], [22, 50, 0], [22, 60, 0], [16, 60, 0], [0.33, 0.31, 0.28]);
  addQuad([42, 4, 0], [48, 4, 0], [48, 14, 0], [42, 14, 0], [0.33, 0.31, 0.28]);
  addQuad([42, 50, 0], [48, 50, 0], [48, 60, 0], [42, 60, 0], [0.33, 0.31, 0.28]);

  // 3. Subterranean Trenches (z = -2.0)
  // West Trench
  addQuad([16, 20, -2], [22, 20, -2], [22, 44, -2], [16, 44, -2], [0.20, 0.18, 0.16]);
  addQuad([16, 20, 0], [16, 44, 0], [16, 44, -2], [16, 20, -2], [0.28, 0.26, 0.24]);
  addQuad([22, 44, 0], [22, 20, 0], [22, 20, -2], [22, 44, -2], [0.28, 0.26, 0.24]);
  // Ramps into West Trench
  addRamp(16, 14, 0, 22, 20, -2, [0.40, 0.36, 0.32]);
  addRamp(16, 44, -2, 22, 50, 0, [0.40, 0.36, 0.32]);

  // East Trench
  addQuad([42, 20, -2], [48, 20, -2], [48, 44, -2], [42, 44, -2], [0.20, 0.18, 0.16]);
  addQuad([42, 20, 0], [42, 44, 0], [42, 44, -2], [42, 20, -2], [0.28, 0.26, 0.24]);
  addQuad([48, 44, 0], [48, 20, 0], [48, 20, -2], [48, 44, -2], [0.28, 0.26, 0.24]);
  // Ramps into East Trench
  addRamp(42, 14, 0, 48, 20, -2, [0.40, 0.36, 0.32]);
  addRamp(42, 44, -2, 48, 50, 0, [0.40, 0.36, 0.32]);

  // 4. Large Shipping Containers (z in [0, 3.2])
  addBox(26, 20, 0, 38, 26, 3.2, [0.22, 0.38, 0.52]); // Center South Blue Container
  addBox(26, 38, 0, 38, 44, 3.2, [0.55, 0.25, 0.22]); // Center North Red Container
  addBox(8, 24, 0, 14, 40, 3.2, [0.38, 0.40, 0.30]); // West Khaki Container
  addBox(50, 24, 0, 56, 40, 3.2, [0.58, 0.36, 0.20]); // East Weathered Orange Container

  // 5. Tactical Crates & Cover
  addBox(20, 12, 0, 24, 16, 1.8, [0.52, 0.42, 0.26]); // South yard crate
  addBox(40, 48, 0, 44, 52, 1.8, [0.52, 0.42, 0.26]); // North yard crate
  addBox(30, 30, 0, 34, 34, 1.4, [0.34, 0.38, 0.42]); // Center scrap crate

  // 6. High Steel Catwalk Bridge (z = 6.4)
  addQuad([30, 14, 6.4], [34, 14, 6.4], [34, 50, 6.4], [30, 50, 6.4], [0.46, 0.44, 0.40]);
  // Catwalk Access Ramps
  addRamp(30, 8, 0, 34, 14, 6.4, [0.44, 0.40, 0.36]); // South ramp up
  addRamp(30, 50, 6.4, 34, 56, 0, [0.44, 0.40, 0.36]); // North ramp down

  // Bridge Support Pillars
  addBox(29, 20, 0, 31, 22, 6.4, [0.26, 0.28, 0.30]);
  addBox(33, 20, 0, 35, 22, 6.4, [0.26, 0.28, 0.30]);
  addBox(29, 42, 0, 31, 44, 6.4, [0.26, 0.28, 0.30]);
  addBox(33, 42, 0, 35, 44, 6.4, [0.26, 0.28, 0.30]);

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(positions), 3));
  geo.setAttribute('normal', new THREE.BufferAttribute(new Float32Array(normals), 3));
  geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(colors), 3));
  geo.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(uvs), 2));
  geo.computeBoundingSphere();
  geo.computeBoundingBox();

  const mat = new THREE.MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.78,
    metalness: 0.22,
  });

  const mesh = new THREE.Mesh(geo, mat);
  mesh.frustumCulled = true;
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  root.add(mesh);

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
    { id: 1, kind: 'armour', x: 32, y: 32, z: 0 },
    { id: 2, kind: 'health', x: 19, y: 32, z: -2 },
    { id: 3, kind: 'health', x: 45, y: 32, z: -2 },
    { id: 4, kind: 'ammo_assault', x: 22, y: 12, z: 0 },
    { id: 5, kind: 'ammo_assault', x: 42, y: 12, z: 0 },
    { id: 6, kind: 'ammo_sniper', x: 22, y: 52, z: 0 },
    { id: 7, kind: 'ammo_sniper', x: 42, y: 52, z: 0 },
    { id: 8, kind: 'grenade', x: 12, y: 32, z: 0 },
    { id: 9, kind: 'clips', x: 52, y: 32, z: 0 },
    { id: 10, kind: 'armour', x: 32, y: 20, z: 0 },
  ];

  const bounds: WorldBounds = {
    min: [4, 4, -2.5],
    max: [60, 60, 14],
    center: [32, 32, 5.75],
    extent: 38,
  };

  const collision: CollisionGeometry = {
    vertices: new Float32Array(colVertices),
    indices: new Uint32Array(colIndices),
    triangles: colIndices.length / 3,
  };

  return {
    info,
    scene: root,
    bounds,
    collision,
    spawns: {
      cla: claSpawns,
      rvsf: rvsfSpawns,
      all: allSpawns,
    },
    items,
    ladders: [],
    waterlevel: -5.0,
    dispose() {
      geo.dispose();
      mat.dispose();
    },
  };
}

/**
 * Procedural 3D Bank Arena ("The Bank", hd_bank):
 * A competitive tactical bomb-defusal arena featuring:
 * 1. Street boulevard approach with SWAT van hood/roof skill jump to 2nd-story fire escape.
 * 2. Grand Banking Hall (Site B) with marble pillars, mahogany teller counters with bulletproof glass.
 * 3. Executive Mezzanine & Balconies (z = 5.0) for vertical crossfires.
 * 4. Reinforced Vault (Site A) with thick blast door, safety deposit lockers, and cash/gold pallets.
 * 5. Jump-shooting vantage points from HVAC overhead ducts into the vault.
 */
export function createProceduralBank3D(
  THREE: typeof import('three'),
  info: MapInfo,
): World3D {
  const root = new THREE.Group();
  root.name = 'World3D_Bank';

  const positions: number[] = [];
  const normals: number[] = [];
  const colors: number[] = [];
  const uvs: number[] = [];
  const colVertices: number[] = [];
  const colIndices: number[] = [];

  let indexOffset = 0;

  function addQuad(
    p0: [number, number, number],
    p1: [number, number, number],
    p2: [number, number, number],
    p3: [number, number, number],
    color: [number, number, number] = [0.45, 0.48, 0.52],
    isCollider = true,
  ) {
    const t0 = [p0[0], p0[2], p0[1]];
    const t1 = [p1[0], p1[2], p1[1]];
    const t2 = [p2[0], p2[2], p2[1]];
    const t3 = [p3[0], p3[2], p3[1]];

    const vA = new THREE.Vector3(t1[0] - t0[0], t1[1] - t0[1], t1[2] - t0[2]);
    const vB = new THREE.Vector3(t2[0] - t0[0], t2[1] - t0[1], t2[2] - t0[2]);
    const norm = new THREE.Vector3().crossVectors(vA, vB).normalize();

    positions.push(...t0, ...t1, ...t2);
    normals.push(norm.x, norm.y, norm.z, norm.x, norm.y, norm.z, norm.x, norm.y, norm.z);
    colors.push(...color, ...color, ...color);
    uvs.push(0, 0, 1, 0, 1, 1);

    positions.push(...t0, ...t2, ...t3);
    normals.push(norm.x, norm.y, norm.z, norm.x, norm.y, norm.z, norm.x, norm.y, norm.z);
    colors.push(...color, ...color, ...color);
    uvs.push(0, 0, 1, 1, 0, 1);

    if (isCollider) {
      const bIdx = indexOffset;
      colVertices.push(...p0, ...p1, ...p2, ...p3);
      colIndices.push(bIdx, bIdx + 1, bIdx + 2, bIdx, bIdx + 2, bIdx + 3);
      indexOffset += 4;
    }
  }

  function addBox(
    minX: number, minY: number, minZ: number,
    maxX: number, maxY: number, maxZ: number,
    color: [number, number, number] = [0.45, 0.48, 0.52],
  ) {
    addQuad([minX, minY, minZ], [maxX, minY, minZ], [maxX, maxY, minZ], [minX, maxY, minZ], color);
    addQuad([minX, maxY, maxZ], [maxX, maxY, maxZ], [maxX, minY, maxZ], [minX, minY, maxZ], color);
    addQuad([maxX, maxY, minZ], [maxX, maxY, maxZ], [minX, maxY, maxZ], [minX, maxY, minZ], color);
    addQuad([minX, minY, minZ], [minX, minY, maxZ], [maxX, minY, maxZ], [maxX, minY, minZ], color);
    addQuad([maxX, minY, minZ], [maxX, minY, maxZ], [maxX, maxY, maxZ], [maxX, maxY, minZ], color);
    addQuad([minX, maxY, minZ], [minX, maxY, maxZ], [minX, minY, maxZ], [minX, minY, minZ], color);
  }

  function addRamp(
    minX: number, minY: number, z0: number,
    maxX: number, maxY: number, z1: number,
    color: [number, number, number] = [0.48, 0.44, 0.40],
  ) {
    addQuad([minX, minY, z0], [maxX, minY, z0], [maxX, maxY, z1], [minX, maxY, z1], color);
    addQuad([minX, maxY, z1], [minX, maxY, Math.min(z0, z1)], [minX, minY, Math.min(z0, z1)], [minX, minY, z0], color);
    addQuad([maxX, minY, z0], [maxX, minY, Math.min(z0, z1)], [maxX, maxY, Math.min(z0, z1)], [maxX, maxY, z1], color);
  }

  // 1. Perimeter Walls (64x64 bounds, height = 14)
  addQuad([4, 4, 0], [60, 4, 0], [60, 4, 14], [4, 4, 14], [0.24, 0.25, 0.28]); // South street
  addQuad([60, 60, 0], [4, 60, 0], [4, 60, 14], [60, 60, 14], [0.24, 0.25, 0.28]); // North rear
  addQuad([60, 4, 0], [60, 60, 0], [60, 60, 14], [60, 4, 14], [0.26, 0.27, 0.30]); // East
  addQuad([4, 60, 0], [4, 4, 0], [4, 4, 14], [4, 60, 14], [0.26, 0.27, 0.30]); // West

  // 2. Ground Floors (Street Asphalt vs Bank Marble Floor)
  addQuad([4, 4, 0], [60, 4, 0], [60, 18, 0], [4, 18, 0], [0.20, 0.20, 0.22]);
  addBox(4, 14, 0, 60, 18, 0.2, [0.48, 0.46, 0.44]);
  addQuad([4, 18, 0], [60, 18, 0], [60, 46, 0], [4, 46, 0], [0.72, 0.70, 0.66]);
  addQuad([4, 46, 0], [40, 46, 0], [40, 60, 0], [4, 60, 0], [0.58, 0.56, 0.52]);
  addQuad([40, 46, 0], [60, 46, 0], [60, 60, 0], [40, 60, 0], [0.28, 0.30, 0.34]);

  // 3. Bank Exterior Facade Wall (y: 18..22) with 3 Entrances
  addBox(4, 18, 0, 8, 22, 14, [0.55, 0.52, 0.48]);
  addBox(14, 18, 0, 28, 22, 14, [0.55, 0.52, 0.48]);
  addBox(36, 18, 0, 50, 22, 14, [0.55, 0.52, 0.48]);
  addBox(56, 18, 0, 60, 22, 14, [0.55, 0.52, 0.48]);
  addBox(26, 16, 0, 28, 18, 12, [0.82, 0.80, 0.78]); // Columns
  addBox(36, 16, 0, 38, 18, 12, [0.82, 0.80, 0.78]);
  addBox(25, 15, 4.2, 39, 18.5, 4.8, [0.68, 0.66, 0.62]); // Canopy (Skill jump platform)

  // 4. Street Cover & Tactical Skill Jump Props
  addBox(18, 10, 0, 22, 12.5, 1.5, [0.15, 0.20, 0.28]); // SWAT Van Hood
  addBox(18, 12.5, 0, 22, 16, 2.8, [0.12, 0.16, 0.24]); // SWAT Van Cab / Roof
  addBox(42, 10, 0, 46, 14, 1.6, [0.18, 0.24, 0.35]);  // Police Cruiser
  addBox(30, 8, 0, 34, 10, 1.2, [0.50, 0.48, 0.46]);   // Jersey Barrier

  // 5. Grand Banking Hall (Site B) - Marble Pillars & Teller Counter
  addBox(22, 24, 0, 24, 26, 14, [0.78, 0.76, 0.72]);
  addBox(40, 24, 0, 42, 26, 14, [0.78, 0.76, 0.72]);
  addBox(22, 40, 0, 24, 42, 14, [0.78, 0.76, 0.72]);
  addBox(40, 40, 0, 42, 42, 14, [0.78, 0.76, 0.72]);
  // Teller Counter Island
  addBox(28, 32, 0, 36, 35, 1.4, [0.45, 0.30, 0.18]);
  addBox(28.5, 32.2, 1.4, 31.5, 32.6, 3.4, [0.65, 0.75, 0.85]);
  addBox(32.5, 32.2, 1.4, 35.5, 32.6, 3.4, [0.65, 0.75, 0.85]);

  // 6. Executive Mezzanine & Balconies (z = 5.0)
  addQuad([14, 38, 5], [20, 38, 5], [20, 45, 5], [14, 45, 5], [0.55, 0.52, 0.48]);
  addBox(19.8, 38, 5, 20.2, 45, 6.1, [0.65, 0.58, 0.32]); // Brass railing
  addRamp(14, 32, 0, 18, 38, 5, [0.48, 0.44, 0.40]);       // West stairs
  addBox(14, 18, 4.8, 18, 22, 5.0, [0.35, 0.33, 0.30]);   // Fire escape connection
  addQuad([44, 38, 5], [50, 38, 5], [50, 45, 5], [44, 45, 5], [0.55, 0.52, 0.48]);
  addBox(43.8, 38, 5, 44.2, 45, 6.1, [0.65, 0.58, 0.32]); // Brass railing
  addRamp(46, 32, 0, 50, 38, 5, [0.48, 0.44, 0.40]);       // East stairs

  // 7. Dividing Wall between Lobby and Rear Bank (y: 46..49)
  addBox(4, 46, 0, 10, 49, 14, [0.50, 0.48, 0.45]);
  addBox(16, 46, 0, 28, 49, 14, [0.50, 0.48, 0.45]);
  addBox(36, 46, 0, 48, 49, 14, [0.50, 0.48, 0.45]);
  addBox(54, 46, 0, 60, 49, 14, [0.50, 0.48, 0.45]);

  // 8. The Vault (Site A)
  addBox(39, 49, 0, 41, 58, 10, [0.28, 0.30, 0.34]);
  addBox(45, 49, 0, 46.5, 51, 8, [0.22, 0.24, 0.28]);   // Vault door frame
  addBox(46.5, 48.5, 0, 50.5, 49.5, 7.5, [0.35, 0.38, 0.44]); // Heavy blast door slab
  addBox(56.5, 51, 0, 58, 58, 8, [0.60, 0.58, 0.52]);  // Deposit lockers
  addBox(41, 56.5, 0, 56.5, 58, 8, [0.60, 0.58, 0.52]);
  addBox(44, 53, 0, 47, 56, 1.8, [0.72, 0.62, 0.25]);  // Gold bullion & cash pallets
  addBox(50, 44, 0, 53, 46, 1.8, [0.25, 0.32, 0.28]);  // HVAC Unit
  addBox(49, 44, 3.4, 53, 48, 4.2, [0.45, 0.48, 0.50]); // Overhead duct for jump shooting

  // 9. Staff Offices (Defender territory)
  addBox(14, 51, 0, 18, 54, 1.5, [0.42, 0.36, 0.30]);
  addBox(26, 51, 0, 30, 54, 1.5, [0.42, 0.36, 0.30]);

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));

  const mat = new THREE.MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.65,
    metalness: 0.25,
  });

  const mesh = new THREE.Mesh(geo, mat);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  root.add(mesh);

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
    vertices: new Float32Array(colVertices),
    indices: new Uint32Array(colIndices),
    triangles: colIndices.length / 3,
  };

  return {
    info,
    scene: root,
    bounds,
    collision,
    spawns: {
      cla: claSpawns,
      rvsf: rvsfSpawns,
      all: allSpawns,
    },
    items,
    ladders: [],
    waterlevel: -100.0,
    dispose() {
      geo.dispose();
      mat.dispose();
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


