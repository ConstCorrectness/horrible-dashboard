/**
 * Drawing the map's ladders.
 *
 * Static map furniture, so unlike `items.ts` there is nothing to reconcile with a
 * snapshot: a ladder's span is derived once by `laddersFrom` and never changes.
 * It is drawn for exactly one reason — a climbable volume you cannot see is a
 * secret, and the whole value of a ladder is that players route around it.
 *
 * Two rails and a stack of rungs, from boxes. Nobody's artwork, the same rule the
 * maps and the item shapes follow, and a silhouette is all a ladder needs to be
 * recognised from across a room.
 *
 * **Drawn at the volume's real width**, not at the width of a real ladder. The
 * catch radius is `LADDER_REACH` (two cubes, comfortably wider than the body), and
 * a slender ladder drawn in the middle of it would teach players to aim for a
 * rung when what actually catches them is a cylinder — so the rails sit at the
 * edge of what will grab you. Drawing the *rule* rather than the object is the
 * same choice `nades.ts` makes for a smoke cloud.
 *
 * Takes the three namespace as a parameter, like every other renderer here.
 */
import type * as THREE from 'three';

import { LADDER_REACH } from './player';
import type { World } from './world';

/** Weathered metal. Deliberately not a map texture — there are none yet. */
const COLOR = 0x8a8f98;

/** Cubes between rungs. Roughly a step, so the stack reads as a scale reference. */
const RUNG_SPACING = 1.6;

const RAIL_THICKNESS = 0.16;
const RUNG_THICKNESS = 0.12;

/** How much narrower than the catch radius the rails sit, so they read as inside it. */
const RAIL_INSET = 0.35;

export interface LadderMeshes {
  dispose(): void;
}

/**
 * Build every ladder in the world, or `null` when there are none.
 *
 * One merged geometry for the lot: a map's ladders are a handful of boxes each and
 * never move, so this is one draw call rather than one per rung.
 */
export function createLadders(
  three: typeof THREE,
  scene: THREE.Scene,
  world: World,
): LadderMeshes | null {
  if (world.ladders.length === 0) return null;

  const positions: number[] = [];
  const normals: number[] = [];
  const half = LADDER_REACH - RAIL_INSET;

  for (const ladder of world.ladders) {
    const height = Math.max(0, ladder.top - ladder.base);
    if (height <= 0) continue;
    // Rails run the full span, offset in x. Which axis they run along is
    // arbitrary — a `ladder` entity carries no facing, so there is nothing to
    // read — and a square footprint is the honest depiction of a volume that
    // catches you from every side.
    for (const dx of [-half, half]) {
      addBox(three, positions, normals, {
        cx: ladder.x + dx,
        cy: ladder.y,
        cz: ladder.base + height / 2,
        sx: RAIL_THICKNESS,
        sy: RAIL_THICKNESS,
        sz: height,
      });
    }
    const rungs = Math.max(1, Math.floor(height / RUNG_SPACING));
    for (let i = 0; i <= rungs; i += 1) {
      addBox(three, positions, normals, {
        cx: ladder.x,
        cy: ladder.y,
        cz: ladder.base + (height * i) / rungs,
        sx: half * 2,
        sy: RUNG_THICKNESS,
        sz: RUNG_THICKNESS,
      });
    }
  }

  const geometry = new three.BufferGeometry();
  geometry.setAttribute('position', new three.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new three.Float32BufferAttribute(normals, 3));
  const material = new three.MeshLambertMaterial({ color: COLOR });
  const mesh = new three.Mesh(geometry, material);
  scene.add(mesh);

  return {
    dispose() {
      scene.remove(mesh);
      geometry.dispose();
      material.dispose();
    },
  };
}

interface BoxSpec {
  cx: number;
  cy: number;
  cz: number;
  sx: number;
  sy: number;
  sz: number;
}

/**
 * Append one axis-aligned box to a position/normal buffer.
 *
 * Hand-rolled rather than merging `BoxGeometry` instances: `three/examples`'
 * merge utility is an addon path this bundle does not otherwise import, and a box
 * is thirty-six vertices of arithmetic.
 */
function addBox(
  three: typeof THREE,
  positions: number[],
  normals: number[],
  { cx, cy, cz, sx, sy, sz }: BoxSpec,
): void {
  const box = new three.BoxGeometry(sx, sy, sz);
  box.translate(cx, cy, cz);
  const pos = box.getAttribute('position');
  const nrm = box.getAttribute('normal');
  const index = box.index;
  if (index) {
    for (let i = 0; i < index.count; i += 1) {
      const v = index.getX(i);
      positions.push(pos.getX(v), pos.getY(v), pos.getZ(v));
      normals.push(nrm.getX(v), nrm.getY(v), nrm.getZ(v));
    }
  } else {
    for (let i = 0; i < pos.count; i += 1) {
      positions.push(pos.getX(i), pos.getY(i), pos.getZ(i));
      normals.push(nrm.getX(i), nrm.getY(i), nrm.getZ(i));
    }
  }
  box.dispose();
}
