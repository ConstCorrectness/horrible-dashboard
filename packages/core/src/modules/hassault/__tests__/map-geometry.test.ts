/**
 * The shipped maps face outward and do not z-fight.
 *
 * Both are properties of the GLB files themselves, which nothing else checks:
 * a GLB is a binary blob in review, and every renderer test builds its own
 * geometry. Both shipped wrong.
 *
 * - **Inside-out meshes.** Both clients cull back faces and derive normals from
 *   winding, so a mesh wound inward is drawn as its own interior: hollow from
 *   outside, solid only from within. The bank's 460 gold ingots and Junk Flea's
 *   bridge ramps were built that way.
 * - **Coplanar overlaps.** Two different-material faces in one plane, facing the
 *   same way, cannot be ordered by the depth buffer and flicker as the camera
 *   moves: Dust II's souk canopies drawn twice, Nuke's hazard stripes inside the
 *   door reveals, monitor screens flush with their bezels.
 *
 * `maplib.clean_meshes` and `maplib.separate_coplanar` fix both at export; this
 * pins that they ran. Checked in the space `buildWorld3DFromGLTF` builds (Z
 * flipped, winding swapped), which is the one that is drawn.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';
import { describe, expect, it } from 'vitest';

const PUBLIC_DIR = fileURLToPath(new URL('../../../../../../apps/web/public/', import.meta.url));

const MAPS = [
  'hd_assault',
  'hd_bank',
  'hd_dust2',
  'hd_facility',
  'hd_inferno',
  'hd_junkflea',
  'hd_mirage',
  'hd_nuke',
  'hd_office',
];

/** Fighting area (cubes²) a map may keep: crossing inlay strips, sub-tile slivers. */
const OVERLAP_BUDGET = 2;

interface Node {
  name?: string;
  mesh?: number;
  children?: number[];
  matrix?: number[];
  translation?: [number, number, number];
  rotation?: [number, number, number, number];
  scale?: [number, number, number];
}

interface Gltf {
  scene?: number;
  scenes: { nodes: number[] }[];
  nodes: Node[];
  meshes: {
    primitives: { attributes: Record<string, number>; indices?: number; material?: number }[];
  }[];
  materials?: { name?: string }[];
  accessors: {
    bufferView: number;
    byteOffset?: number;
    count: number;
    componentType: number;
    type: string;
  }[];
  bufferViews: { byteOffset?: number; byteStride?: number }[];
}

interface Tri {
  node: string;
  material: string;
  a: THREE.Vector3;
  b: THREE.Vector3;
  c: THREE.Vector3;
  n: THREE.Vector3;
  d: number;
}

function readGlb(path: string): { gltf: Gltf; bin: Buffer } {
  const buf = readFileSync(path);
  let offset = 12;
  let gltf: Gltf | null = null;
  let bin: Buffer | null = null;
  while (offset < buf.length) {
    const length = buf.readUInt32LE(offset);
    const kind = buf.readUInt32LE(offset + 4);
    const body = buf.subarray(offset + 8, offset + 8 + length);
    if (kind === 0x4e4f534a) gltf = JSON.parse(body.toString('utf8')) as Gltf;
    else bin = body;
    offset += 8 + length + ((4 - (length % 4)) % 4);
  }
  if (!gltf || !bin) throw new Error(`${path}: missing a GLB chunk`);
  return { gltf, bin };
}

function accessor(gltf: Gltf, bin: Buffer, index: number): number[] {
  const a = gltf.accessors[index];
  const view = gltf.bufferViews[a.bufferView];
  const width = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 }[a.type] ?? 1;
  const bytes =
    a.componentType === 5126 || a.componentType === 5125 ? 4 : a.componentType === 5123 ? 2 : 1;
  const stride = view.byteStride ?? width * bytes;
  const start = (view.byteOffset ?? 0) + (a.byteOffset ?? 0);
  const out: number[] = [];
  for (let k = 0; k < a.count; k++) {
    for (let c = 0; c < width; c++) {
      const at = start + k * stride + c * bytes;
      out.push(
        a.componentType === 5126
          ? bin.readFloatLE(at)
          : a.componentType === 5125
            ? bin.readUInt32LE(at)
            : a.componentType === 5123
              ? bin.readUInt16LE(at)
              : bin.readUInt8(at),
      );
    }
  }
  return out;
}

/** Every drawn triangle, per node, in the space the browser builds. */
function trianglesOf(name: string): Map<string, Tri[]> {
  const { gltf, bin } = readGlb(`${PUBLIC_DIR}${name}.glb`);
  const zFlip = new THREE.Matrix4().makeScale(1, 1, -1);
  const out = new Map<string, Tri[]>();
  const walk = (index: number, parent: THREE.Matrix4) => {
    const node = gltf.nodes[index];
    const local = node.matrix
      ? new THREE.Matrix4().fromArray(node.matrix)
      : new THREE.Matrix4().compose(
          new THREE.Vector3(...(node.translation ?? [0, 0, 0])),
          new THREE.Quaternion(...(node.rotation ?? [0, 0, 0, 1])),
          new THREE.Vector3(...(node.scale ?? [1, 1, 1])),
        );
    const world = parent.clone().multiply(local);
    const label = node.name ?? `#${index}`;
    if (node.mesh !== undefined && !/ColOnly|Invisible/.test(label)) {
      const m = world.clone().premultiply(zFlip);
      const tris: Tri[] = [];
      for (const prim of gltf.meshes[node.mesh].primitives) {
        const pos = accessor(gltf, bin, prim.attributes.POSITION);
        const idx = prim.indices !== undefined ? accessor(gltf, bin, prim.indices) : null;
        const material = gltf.materials?.[prim.material ?? -1]?.name ?? '';
        const v = (k: number) =>
          new THREE.Vector3(pos[k * 3], pos[k * 3 + 1], pos[k * 3 + 2]).applyMatrix4(m);
        const count = idx ? idx.length : pos.length / 3;
        for (let t = 0; t < count; t += 3) {
          // The loader swaps the second and third index to undo the Z flip.
          const a = v(idx ? idx[t] : t);
          const b = v(idx ? idx[t + 2] : t + 2);
          const c = v(idx ? idx[t + 1] : t + 1);
          const n = new THREE.Vector3()
            .subVectors(b, a)
            .cross(new THREE.Vector3().subVectors(c, a));
          if (n.lengthSq() < 1e-12) continue;
          n.normalize();
          tris.push({ node: label, material, a, b, c, n, d: n.dot(a) });
        }
      }
      out.set(label, tris);
    }
    for (const child of node.children ?? []) walk(child, world);
  };
  for (const root of gltf.scenes[gltf.scene ?? 0].nodes) walk(root, new THREE.Matrix4());
  return out;
}

/** Closed (every welded edge used twice) and enclosing negative volume. */
function isInsideOut(tris: Tri[]): boolean {
  const key = (p: THREE.Vector3) => `${p.x.toFixed(3)},${p.y.toFixed(3)},${p.z.toFixed(3)}`;
  const edges = new Map<string, number>();
  let volume = 0;
  for (const t of tris) {
    const ks = [key(t.a), key(t.b), key(t.c)];
    for (let i = 0; i < 3; i++) {
      const e = [ks[i], ks[(i + 1) % 3]].sort().join('|');
      edges.set(e, (edges.get(e) ?? 0) + 1);
    }
    volume += t.a.dot(new THREE.Vector3().crossVectors(t.b, t.c)) / 6;
  }
  const closed = [...edges.values()].every((c) => c === 2);
  return closed && volume < -1e-6;
}

/** Area of same-plane, same-facing, different-material overlap between nodes. */
function fightingArea(all: Tri[]): number {
  const buckets = new Map<string, Tri[]>();
  for (const t of all) {
    const k = `${t.n.x.toFixed(2)},${t.n.y.toFixed(2)},${t.n.z.toFixed(2)},${t.d.toFixed(2)}`;
    const list = buckets.get(k);
    if (list) list.push(t);
    else buckets.set(k, [t]);
  }
  let area = 0;
  for (const list of buckets.values()) {
    for (let i = 0; i < list.length; i++) {
      for (let j = 0; j < list.length; j++) {
        const a = list[i];
        const b = list[j];
        if (i === j || a.node === b.node || a.material === b.material) continue;
        // A face pointing down onto something it rests on is never seen.
        if (a.n.y < -0.9) continue;
        const centre = new THREE.Vector3().add(a.a).add(a.b).add(a.c).divideScalar(3);
        if (new THREE.Triangle(b.a, b.b, b.c).containsPoint(centre)) {
          area += new THREE.Triangle(a.a, a.b, a.c).getArea();
          break;
        }
      }
    }
  }
  return area;
}

describe.each(MAPS)('%s geometry', (name) => {
  const nodes = trianglesOf(name);

  it('has no mesh wound inside out', () => {
    const inverted = [...nodes].filter(([, tris]) => isInsideOut(tris)).map(([n]) => n);
    expect(inverted).toEqual([]);
  });

  it('has no surfaces fighting for the same plane', () => {
    expect(fightingArea([...nodes.values()].flat())).toBeLessThan(OVERLAP_BUDGET);
  });
});
