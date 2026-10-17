/**
 * Bullet marks: where the shots went, still there when you look back.
 *
 * The world used to keep no memory of being shot at. A tracer for 75ms, a blob
 * at the endpoint for 300ms, and then nothing — so a firefight left a room
 * looking exactly as it had before it, and a player had no way to see their own
 * spray on a wall. Since the spray is now a *learnable pattern*
 * (`weapons.py`'s `spray`), the marks are not decoration: they are how you read
 * the pattern you are supposed to be learning.
 *
 * ## The normal comes off the wire
 *
 * A decal has to lie flat on the surface it is on, which means knowing which
 * face was hit. That is **not** derived here: `weapons.raycast_world_face`
 * already knows it at the instant its walk returns, and puts one small integer
 * per pellet into the `shot` fx. Working it out here instead would be a copy of
 * the world ray whose only job is to agree with the server about the exact point
 * the server chose — and a disagreement of half a cube puts the mark inside the
 * wall, where it is invisible and reports nothing. See `FACE_NORMALS`.
 *
 * ## A ring buffer, not a list
 *
 * `DECAL_MAX` meshes are allocated once and reused in order. Nothing is created
 * or destroyed per shot, and the cap holds *by construction* rather than by a
 * `while (length > MAX) shift()` that has to run after every push. An automatic
 * weapon puts thirty marks on a wall in two seconds; that is a rate at which
 * per-shot allocation is a real cost.
 *
 * Takes `three` as a parameter rather than importing it, like `effects.ts` and
 * `viewmodel.ts`, so the lazy-load stays in one place.
 */
import type * as THREE from 'three';

import { FACE_NONE, FACE_NZ, FACE_PZ } from './trace';

/** How many marks the world remembers. */
export const DECAL_MAX = 256;
/** How long one mark lasts before it starts fading, in seconds. */
export const DECAL_LIFE = 60;
/** How long the fade at the end of that life takes. */
export const DECAL_FADE = 6;
/**
 * How far off the surface a mark sits, in cube units.
 *
 * A decal coplanar with the wall it is on z-fights, which reads as flicker
 * rather than as a bug. Lifted along the face normal by well under a millimetre
 * of world scale — `polygonOffset` does most of the work and this covers the
 * grazing angles where it does not.
 */
export const DECAL_LIFT = 0.012;
/** The mark's width, in cube units. A cube is roughly 36cm, so this is ~7cm. */
export const DECAL_SIZE = 0.2;

export type DecalKind = 'bullet' | 'knife_slash' | 'knife_stab';

/**
 * The six faces as unit vectors, in **cube** coordinates.
 *
 * Indexed by the face on the wire, and mirroring `weapons.FACE_NORMALS` on the
 * server and `FACE_NORMALS` in `decals.rs`. Pinned by `browser_parity.rs`.
 */
export const FACE_NORMALS: readonly [number, number, number][] = [
  [1, 0, 0],
  [-1, 0, 0],
  [0, 1, 0],
  [0, -1, 0],
  [0, 0, 1],
  [0, 0, -1],
];

export type SurfaceMaterial = 'concrete' | 'metal' | 'wood' | 'glass' | 'default';

export const MATERIAL_TINTS: Record<SurfaceMaterial, number> = {
  concrete: 0xb8b8b8,
  metal: 0x586068,
  wood: 0x6a4828,
  glass: 0x88c8e8,
  default: 0xffffff,
};

/**
 * Identify the surface material from cell texture coordinates and hit face.
 */
export function surfaceMaterial(
  world: { ssize: number; wtex: Uint8Array; ftex: Uint8Array; ctex: Uint8Array; utex?: Uint8Array } | null | undefined,
  at: [number, number, number],
  face: number,
): SurfaceMaterial {
  if (!world || face === FACE_NONE || face < 0) return 'default';
  const cx = Math.floor(at[0]);
  const cy = Math.floor(at[1]);
  if (cx < 0 || cy < 0 || cx >= world.ssize || cy >= world.ssize) return 'concrete';
  const idx = cy * world.ssize + cx;
  let tex = 0;
  if (face === FACE_PZ) {
    tex = world.ftex[idx] ?? 0;
  } else if (face === FACE_NZ) {
    tex = world.ctex[idx] ?? 0;
  } else {
    tex = world.wtex[idx] ?? 0;
  }

  // Metal: 3 (Default Floor plate), 21 (Steel Deck), 58 (Grated Floor), 89 (Rusted Plate), 131 (Bulkhead), 150 (Catwalk), 233 (Vent)
  if (tex === 3 || tex === 21 || tex === 58 || tex === 89 || tex === 131 || tex === 150 || tex === 233) {
    return 'metal';
  }
  // Wood / Composite Panels: 4 (Default Ceiling panel), 33 (Ceiling Panel), 76 (Painted Block), 104 (Tile), 214 (Hazard Stripe)
  if (tex === 4 || tex === 33 || tex === 76 || tex === 104 || tex === 214) {
    return 'wood';
  }
  // Glass: 199 (Glass Panel)
  if (tex === 199) {
    return 'glass';
  }
  // Concrete / Stone: 2, 6, 12, 47, 178
  return 'concrete';
}

/** Tile edge in pixels. A bullet hole seen from across a room is a smudge. */
const SIZE = 32;

/** Deterministic hash in [0, 1), so a mark's cracks are the same every run. */
function hash(x: number, y: number): number {
  const n = Math.sin(x * 127.1 + y * 311.7) * 43758.5453123;
  return n - Math.floor(n);
}

/**
 * One impact tile: a dark crater, a lighter rim of displaced material, and a few
 * radial cracks.
 *
 * Alpha is the shape and the RGB is the colour, so one tile serves every
 * surface — a per-material tint is then one field on the material rather than a
 * second texture.
 */
export function drawImpactTile(size = SIZE): Uint8Array {
  const out = new Uint8Array(size * size * 4);
  const half = size / 2;
  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      const dx = (px + 0.5 - half) / half;
      const dy = (py + 0.5 - half) / half;
      const r = Math.hypot(dx, dy);
      const angle = Math.atan2(dy, dx);

      // The crater: solid to about a third of the radius, then falling away.
      let alpha = r < 0.34 ? 1 : Math.max(0, 1 - (r - 0.34) / 0.5);
      // Cracks: a handful of spokes reaching further out than the crater does,
      // roughened so the mark is not a target symbol.
      const spoke = Math.abs(Math.cos(angle * 2.5 + hash(px >> 3, py >> 3) * 2));
      alpha = Math.max(alpha, spoke > 0.93 && r < 0.92 ? 0.45 * (1 - r) : 0);
      // Hard zero at the rim, for the same reason the flash tile has one: a
      // decal with a visible edge reads as a sticker.
      if (r >= 1) alpha = 0;

      // The crater is nearly black and the rim just outside it is pale, which is
      // what makes a hole read as a hole rather than as a dark circle.
      const rim = r > 0.3 && r < 0.46 ? 1 : 0;
      const value = Math.round(rim ? 168 : 26);
      const i = (py * size + px) * 4;
      out[i] = value;
      out[i + 1] = value;
      out[i + 2] = value;
      out[i + 3] = Math.round(Math.max(0, Math.min(1, alpha)) * 255);
    }
  }
  return out;
}

/**
 * One knife slash tile: an elongated tapered gouge, simulating a blade cutting
 * into plaster, wood, or metal with micro-fractures along its flanks.
 */
export function drawSlashTile(size = SIZE): Uint8Array {
  const out = new Uint8Array(size * size * 4);
  const half = size / 2;
  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      const dx = (px + 0.5 - half) / half;
      const dy = (py + 0.5 - half) / half;
      const len = Math.abs(dy);
      if (len >= 0.88) continue;

      // Taper width from center (0.28) to ends (0.02)
      const taper = Math.sqrt(Math.max(0, 1 - (len / 0.88) ** 2));
      const widthLimit = 0.28 * taper;
      const dist = Math.abs(dx);
      if (dist >= widthLimit) continue;

      // Jitter edge to look like jagged sheared material
      const edgeJitter = (hash(px >> 2, py >> 2) - 0.5) * 0.08 * taper;
      const normalizedDist = (dist + edgeJitter) / widthLimit;
      if (normalizedDist > 1) continue;

      // Groove is dark near center, lighter displaced rim toward outer edge
      const inGroove = normalizedDist < 0.42;
      const rim = !inGroove && normalizedDist < 0.88;
      const value = Math.round(inGroove ? 22 : rim ? 175 : 85);
      const alpha = Math.max(0, Math.min(1, 1 - normalizedDist ** 2));

      const i = (py * size + px) * 4;
      out[i] = value;
      out[i + 1] = value;
      out[i + 2] = value;
      out[i + 3] = Math.round(alpha * 255);
    }
  }
  return out;
}

/**
 * One knife stab tile: a deep puncturing diamond gouge with fractured displaced edges.
 */
export function drawStabTile(size = SIZE): Uint8Array {
  const out = new Uint8Array(size * size * 4);
  const half = size / 2;
  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      const dx = (px + 0.5 - half) / half;
      const dy = (py + 0.5 - half) / half;
      const r = Math.hypot(dx, dy);
      const diamond = Math.abs(dx) + Math.abs(dy);
      if (diamond >= 0.85 || r >= 0.95) continue;

      // Deep puncture core
      const core = diamond < 0.32;
      // Displaced chipped perimeter
      const rim = !core && diamond < 0.62;
      // Radial micro-fractures extending along cardinal diagonals
      const fracture = (Math.abs(dx) < 0.12 || Math.abs(dy) < 0.12) && diamond < 0.78;

      let alpha = core ? 1.0 : rim ? 0.85 : fracture ? 0.55 : 0.0;
      if (diamond >= 0.75) alpha *= Math.max(0, (0.85 - diamond) / 0.1);

      const value = Math.round(core ? 20 : rim ? 172 : 90);
      const i = (py * size + px) * 4;
      out[i] = value;
      out[i + 1] = value;
      out[i + 2] = value;
      out[i + 3] = Math.round(Math.max(0, Math.min(1, alpha)) * 255);
    }
  }
  return out;
}

/** Build a decal texture for the given kind. A `DataTexture`, so this module runs headless. */
export function createDecalTexture(
  three: typeof THREE,
  kind: DecalKind = 'bullet',
  size = SIZE,
): THREE.DataTexture {
  const data =
    kind === 'knife_slash'
      ? drawSlashTile(size)
      : kind === 'knife_stab'
      ? drawStabTile(size)
      : drawImpactTile(size);
  const texture = new three.DataTexture(data, size, size, three.RGBAFormat);
  texture.magFilter = three.LinearFilter;
  texture.minFilter = three.LinearMipmapLinearFilter;
  texture.generateMipmaps = true;
  texture.colorSpace = three.SRGBColorSpace;
  texture.needsUpdate = true;
  return texture;
}

/** Build the impact texture. A `DataTexture`, so this module runs headless. */
export function createImpactTexture(three: typeof THREE, size = SIZE): THREE.DataTexture {
  return createDecalTexture(three, 'bullet', size);
}

/**
 * How opaque a mark is, `age` seconds after it was made.
 *
 * Flat for most of its life and then fading, rather than decaying from the
 * start: a mark that begins fading immediately is a mark that is never quite
 * legible, and legibility over a whole magazine is the entire point.
 */
export function decalOpacity(age: number): number {
  if (age <= DECAL_LIFE - DECAL_FADE) return 1;
  if (age >= DECAL_LIFE) return 0;
  return (DECAL_LIFE - age) / DECAL_FADE;
}

interface Mark {
  mesh: THREE.Mesh;
  material: THREE.MeshBasicMaterial;
  age: number;
  live: boolean;
}

export class DecalPool {
  private marks: Mark[] = [];
  private next = 0;
  private geometry: THREE.PlaneGeometry;
  private textures: Record<DecalKind, THREE.DataTexture>;
  /**
   * The six orientations, built once.
   *
   * **Fixed quaternions, never `lookAt`.** `lookAt`'s axis convention differs
   * between cameras and everything else, which is the kind of thing that is
   * wrong by exactly 180° — and a decal wrong by 180° is a decal facing into its
   * own wall, i.e. invisible. `effects.ts` documents the same trap for tracers.
   */
  private orientations: THREE.Quaternion[];

  constructor(
    three: typeof THREE,
    private readonly scene: THREE.Scene,
  ) {
    this.geometry = new three.PlaneGeometry(1, 1);
    this.textures = {
      bullet: createDecalTexture(three, 'bullet'),
      knife_slash: createDecalTexture(three, 'knife_slash'),
      knife_stab: createDecalTexture(three, 'knife_stab'),
    };

    // A `PlaneGeometry` faces +z in its own space, so each orientation is the
    // rotation taking +z onto that face's normal — in **three**'s axes, where
    // cube z is up and cube y is three's z.
    const from = new three.Vector3(0, 0, 1);
    this.orientations = FACE_NORMALS.map((n) => {
      const to = new three.Vector3(n[0], n[2], n[1]);
      return new three.Quaternion().setFromUnitVectors(from, to);
    });

    this.marks = Array.from({ length: DECAL_MAX }, () => {
      const material = new three.MeshBasicMaterial({
        map: this.textures.bullet,
        transparent: true,
        opacity: 1,
        // Depth *tested* so a mark is hidden by anything in front of it, but
        // never *written*: marks overlap constantly and a written depth would
        // make the newer one z-fight the older.
        depthWrite: false,
        depthTest: true,
        polygonOffset: true,
        polygonOffsetFactor: -4,
        polygonOffsetUnits: -4,
      });
      const mesh = new three.Mesh(this.geometry, material);
      mesh.visible = false;
      // Below the view model's 2, so a mark can never draw over the gun.
      mesh.renderOrder = 1;
      scene.add(mesh);
      return { mesh, material, age: 0, live: false };
    });
  }

  /**
   * Record one impact, in **cube** coordinates — the axes the netcode speaks.
   *
   * A `face` of `FACE_NONE` is a body hit or a shot that ran out of range, and
   * is silently ignored: there is no surface to mark. Checked here rather than
   * at the call site so every caller has one fewer chance to index
   * `FACE_NORMALS` with `-1`.
   */
  mark(
    at: [number, number, number],
    face: number,
    tintOrMaterial: number | SurfaceMaterial = 'default',
    kind: DecalKind = 'bullet',
    slashAngle = 0,
  ): void {
    if (face === FACE_NONE || face < 0 || face >= FACE_NORMALS.length) return;
    const entry = this.marks[this.next];
    this.next = (this.next + 1) % DECAL_MAX;

    const normal = FACE_NORMALS[face];
    // Cube (x, y, height) -> three (x, height, z), lifted off the surface along
    // its own normal so it does not z-fight the wall it is on.
    entry.mesh.position.set(
      at[0] + normal[0] * DECAL_LIFT,
      at[2] + normal[2] * DECAL_LIFT,
      at[1] + normal[1] * DECAL_LIFT,
    );
    entry.mesh.quaternion.copy(this.orientations[face]);

    if (kind === 'knife_slash') {
      entry.mesh.rotateZ(slashAngle);
      entry.mesh.scale.set(DECAL_SIZE * 0.45, DECAL_SIZE * 1.5, 1);
    } else if (kind === 'knife_stab') {
      entry.mesh.rotateZ(hash(at[0], at[1]) * Math.PI * 2);
      entry.mesh.scale.set(DECAL_SIZE * 0.75, DECAL_SIZE * 0.75, 1);
    } else {
      // Bullet hole: spun about its own normal, so marks are not identical stamps.
      entry.mesh.rotateZ(hash(at[0], at[1]) * Math.PI * 2);
      entry.mesh.scale.set(DECAL_SIZE, DECAL_SIZE, 1);
    }

    entry.material.map = this.textures[kind];
    entry.material.needsUpdate = true;
    const hex =
      typeof tintOrMaterial === 'number'
        ? tintOrMaterial
        : (MATERIAL_TINTS[tintOrMaterial] ?? 0xffffff);
    entry.material.color.setHex(hex);
    entry.material.opacity = 1;
    entry.mesh.visible = true;
    entry.age = 0;
    entry.live = true;
  }

  /** Age every live mark, fading and retiring as it goes. */
  update(dt: number): void {
    for (const entry of this.marks) {
      if (!entry.live) continue;
      entry.age += dt;
      const opacity = decalOpacity(entry.age);
      if (opacity <= 0) {
        entry.live = false;
        entry.mesh.visible = false;
        continue;
      }
      entry.material.opacity = opacity;
    }
  }

  /** How many marks are currently on the world. For tests and the debug HUD. */
  get size(): number {
    return this.marks.reduce((n, m) => n + (m.live ? 1 : 0), 0);
  }

  dispose(): void {
    for (const entry of this.marks) {
      this.scene.remove(entry.mesh);
      entry.material.dispose();
    }
    this.marks = [];
    this.geometry.dispose();
    for (const tex of Object.values(this.textures)) {
      tex.dispose();
    }
  }
}
