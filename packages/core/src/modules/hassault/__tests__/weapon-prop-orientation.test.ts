/**
 * Every shipped weapon prop points its barrel down -Z.
 *
 * This is the one property of these files that nothing else can check. It is
 * not visible in code review (a GLB is a binary blob), the existing
 * `weapon-models` tests build their prototypes synthetically so they never
 * touch the real assets, and `fitWeaponModel` cannot tell — it *defines* the
 * muzzle as the model's `min.z` and would happily put the flash on a buttstock.
 *
 * It shipped wrong. Three of the four props were exported barrel-first down
 * **+Z**, so the shotgun and the sniper spent their whole lives in the view
 * model pointing back at the player, with their muzzle flash 0.2 cubes behind
 * the stock. `--forward` states which way the barrel points *in the source*,
 * and the sign is the half a bounding box cannot tell you, so getting it
 * backwards produces a completely valid file that is completely wrong.
 *
 * The discriminator is thickness. A rifle's front is a barrel and its back is a
 * stock or a grip, so the frontmost slice of the model is dramatically thinner
 * than the rearmost one. Nothing else about these files distinguishes the two
 * ends: the bounding box is identical either way round, which is exactly why
 * `--forward` has to be stated by hand.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import { gripsFor } from '../arms';
import { WEAPON_MODEL_URLS } from '../models/weapons';

const PUBLIC_DIR = fileURLToPath(new URL('../../../../../../apps/web/public/', import.meta.url));

interface Gltf {
  scene?: number;
  scenes: { nodes: number[] }[];
  nodes: {
    mesh?: number;
    children?: number[];
    matrix?: number[];
    translation?: number[];
    rotation?: number[];
    scale?: number[];
  }[];
  meshes: { primitives: { attributes: Record<string, number> }[] }[];
  accessors: { bufferView: number; byteOffset?: number; count: number }[];
  bufferViews: { byteOffset?: number; byteStride?: number }[];
}

/** The JSON chunk and the binary chunk of a GLB, without a glTF library. */
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
    // Chunks are padded to a four-byte boundary.
    offset += 8 + length + ((4 - (length % 4)) % 4);
  }
  if (!gltf || !bin) throw new Error(`${path}: missing a GLB chunk`);
  return { gltf, bin };
}

type Mat4 = number[];

const IDENTITY: Mat4 = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

/** Column-major, as glTF stores them. */
function multiply(a: Mat4, b: Mat4): Mat4 {
  const out = new Array<number>(16).fill(0);
  for (let col = 0; col < 4; col++) {
    for (let row = 0; row < 4; row++) {
      let sum = 0;
      for (let k = 0; k < 4; k++) sum += a[k * 4 + row] * b[col * 4 + k];
      out[col * 4 + row] = sum;
    }
  }
  return out;
}

function localMatrix(node: Gltf['nodes'][number]): Mat4 {
  if (node.matrix) return node.matrix.slice();
  const [x, y, z, w] = node.rotation ?? [0, 0, 0, 1];
  const [sx, sy, sz] = node.scale ?? [1, 1, 1];
  const [tx, ty, tz] = node.translation ?? [0, 0, 0];
  const x2 = x + x;
  const y2 = y + y;
  const z2 = z + z;
  const xx = x * x2;
  const xy = x * y2;
  const xz = x * z2;
  const yy = y * y2;
  const yz = y * z2;
  const zz = z * z2;
  const wx = w * x2;
  const wy = w * y2;
  const wz = w * z2;
  return [
    (1 - (yy + zz)) * sx,
    (xy + wz) * sx,
    (xz - wy) * sx,
    0,
    (xy - wz) * sy,
    (1 - (xx + zz)) * sy,
    (yz + wx) * sy,
    0,
    (xz + wy) * sz,
    (yz - wx) * sz,
    (1 - (xx + yy)) * sz,
    0,
    tx,
    ty,
    tz,
    1,
  ];
}

/** Every vertex of the scene, in the space the view model receives it in. */
function worldPositions(path: string): [number, number, number][] {
  const { gltf, bin } = readGlb(path);
  const points: [number, number, number][] = [];

  const walk = (index: number, parent: Mat4): void => {
    const node = gltf.nodes[index];
    const world = multiply(parent, localMatrix(node));
    if (node.mesh != null) {
      for (const primitive of gltf.meshes[node.mesh].primitives) {
        const accessor = gltf.accessors[primitive.attributes.POSITION];
        const view = gltf.bufferViews[accessor.bufferView];
        const stride = view.byteStride ?? 12;
        const base = (view.byteOffset ?? 0) + (accessor.byteOffset ?? 0);
        for (let i = 0; i < accessor.count; i++) {
          const at = base + i * stride;
          const x = bin.readFloatLE(at);
          const y = bin.readFloatLE(at + 4);
          const z = bin.readFloatLE(at + 8);
          points.push([
            world[0] * x + world[4] * y + world[8] * z + world[12],
            world[1] * x + world[5] * y + world[9] * z + world[13],
            world[2] * x + world[6] * y + world[10] * z + world[14],
          ]);
        }
      }
    }
    for (const child of node.children ?? []) walk(child, world);
  };

  for (const index of gltf.scenes[gltf.scene ?? 0].nodes) walk(index, IDENTITY);
  return points;
}

/** The diagonal of the cross-section over a slice of the weapon's length. */
function girth(
  points: [number, number, number][],
  from: number,
  to: number,
): { diagonal: number; seen: number } {
  let x0 = Infinity;
  let x1 = -Infinity;
  let y0 = Infinity;
  let y1 = -Infinity;
  let seen = 0;
  for (const [x, y, z] of points) {
    if (z < from || z > to) continue;
    seen++;
    if (x < x0) x0 = x;
    if (x > x1) x1 = x;
    if (y < y0) y0 = y;
    if (y > y1) y1 = y;
  }
  if (seen === 0) return { diagonal: 0, seen };
  return { diagonal: Math.hypot(x1 - x0, y1 - y0), seen };
}

describe('the shipped weapon props', () => {
  const entries = Object.entries(WEAPON_MODEL_URLS);
  const fileFor = (url: string): string => `${PUBLIC_DIR}${url.replace(/^\//, '')}`;

  it('ships a file for every registered weapon', () => {
    // An entry pointing at a missing file is the one failure mode
    // `loadWeaponModel` cannot report as anything but a single console warning
    // in a game that still renders — so it would reach a player long before it
    // reached anyone who could fix it.
    expect(entries.length).toBeGreaterThan(0);
    for (const [, url] of entries) {
      expect(() => readFileSync(fileFor(url))).not.toThrow();
    }
  });

  it.each(entries)('points the %s barrel down -Z', (_weapon, url) => {
    const points = worldPositions(fileFor(url));
    expect(points.length).toBeGreaterThan(100);

    let near = Infinity;
    let far = -Infinity;
    for (const [, , z] of points) {
      if (z < near) near = z;
      if (z > far) far = z;
    }
    const length = far - near;
    // A twentieth of the length at each end, and the narrowness is the point.
    // Measured over a tenth instead, the shotgun scores 1.32 rather than 2.14 —
    // its front tenth reaches back over the underslung light and the magazine
    // tube, which are as bulky as the stock and have nothing to do with which
    // way it points. The extreme tip is barrel and only barrel.
    const window = length * 0.05;
    const front = girth(points, near, near + window);
    const back = girth(points, far - window, far);

    // Guarded rather than assumed: an end slice thin enough to be empty would
    // make the ratio a division by zero and this test would pass or fail on
    // nothing at all.
    expect(front.seen).toBeGreaterThan(20);
    expect(back.seen).toBeGreaterThan(20);
    expect(front.diagonal).toBeGreaterThan(0);

    // A ratio rather than an absolute, since these range from a 0.6-cube pistol
    // to a 2.9-cube shotgun. One end is a barrel and the other is a shoulder
    // stock or a grip, so there is a lot of room between "correct" and
    // "backwards": measured, the four score 2.14 (shotgun), 2.45 (pistol), 4.26
    // (sniper) and 4.35 (assault), and a flipped prop scores the reciprocal.
    expect(back.diagonal / front.diagonal).toBeGreaterThan(1.8);
  });

  it.each(entries)('has both %s grips on the weapon itself', (weapon, url) => {
    // **An anchor floating in space is the silent failure.** The two-bone solve
    // will happily reach an empty point beside the gun, so the arms are drawn,
    // the elbows bend, nothing errors — and a hand hovers a few centimetres off
    // the handguard forever.
    //
    // The prop is fitted onto the box model's own bounding volume, so an anchor
    // written against the boxes lands on the prop too. Checked with a margin,
    // because a grip is *on* a surface rather than inside the hull: a trigger
    // hand sits just under the receiver and a support hand just under a barrel.
    const points = worldPositions(fileFor(url));
    const lo = [Infinity, Infinity, Infinity];
    const hi = [-Infinity, -Infinity, -Infinity];
    for (const p of points) {
      for (let i = 0; i < 3; i++) {
        if (p[i] < lo[i]) lo[i] = p[i];
        if (p[i] > hi[i]) hi[i] = p[i];
      }
    }
    // The box the prop occupies, centred like `fitWeaponModel` leaves it.
    const half = [0, 1, 2].map((i) => (hi[i] - lo[i]) / 2);
    const grips = gripsFor(weapon);
    const margin = 0.45;
    for (const [name, anchor] of [
      ['primary', grips.primary],
      ['support', grips.support],
    ] as const) {
      if (anchor === null) continue;
      for (let i = 0; i < 3; i++) {
        expect(
          Math.abs(anchor[i]),
          `${weapon} ${name} axis ${i} is ${anchor[i]}, outside a ${half[i].toFixed(2)}-cube weapon`,
        ).toBeLessThanOrEqual(half[i] + margin);
      }
    }
  });
});
