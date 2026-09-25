/**
 * Which nodes of a modelled (`hd_*.glb`) map the character collides with.
 *
 * The rules live in `glb-colliders.json`, which `apps/native-fps/src/world3d.rs`
 * reads too, so the two clients cannot disagree about what is solid. They used to
 * be two hand-kept keyword lists — one case-insensitive, one not — and a keyword
 * alone decided: `Perimeter` contains `rim`, so every map's outer wall was
 * walk-through in the browser, along with Mirage's Window Room floor, Dust II's
 * bomb-site crates and the office's cubicle dividers.
 *
 * A keyword now only marks a node as *decoration*, and decoration is skipped only
 * when it is too small to matter: nothing a body could stand on or be stopped by.
 * The keywords exist to keep hundreds of pipes, cables, stripes and fittings out
 * of the collision mesh, where they would snag the capsule — not to decide that a
 * 30-unit wall is a trim piece. `NonCol` in a name is the author saying so
 * outright, and always wins. Pure: no three import, so it runs headless.
 */
import table from './glb-colliders.json';

interface ColliderTable {
  explicitNonCollider: string[];
  decorNameKeywords: string[];
  decorMaterialKeywords: string[];
  blockHeight: number;
  blockWidth: number;
  standWidth: number;
  cases: { name: string; materials: string[]; size: [number, number, number]; collides: boolean }[];
}

export const COLLIDER_TABLE = table as unknown as ColliderTable;

/** Named or textured as decoration. Says nothing yet about whether it collides. */
export function isDecorNode(name: string, materials: readonly string[] = []): boolean {
  const t = COLLIDER_TABLE;
  if (t.decorNameKeywords.some((k) => name.includes(k))) return true;
  return materials.some((m) => {
    const lower = m.toLowerCase();
    return t.decorMaterialKeywords.some((k) => lower.includes(k));
  });
}

/**
 * Whether a node is part of the collision mesh.
 *
 * `size` is its world-space bounding box in game axes: `[x, y, height]`.
 */
export function glbNodeCollides(
  name: string,
  materials: readonly string[],
  size: readonly [number, number, number],
): boolean {
  const t = COLLIDER_TABLE;
  if (t.explicitNonCollider.some((k) => name.includes(k))) return false;
  if (!isDecorNode(name, materials)) return true;
  const [sx, sy, sz] = size;
  const standable = Math.min(sx, sy) >= t.standWidth;
  const blocking = sz >= t.blockHeight && Math.max(sx, sy) >= t.blockWidth;
  return standable || blocking;
}

/**
 * Cubes per metre. The Blender generators author in metres and `maplib.py`
 * exports at `MAP_SCALE = 3.0`; anything tuned in metres (the Rapier controller)
 * multiplies by this. `world3d.ts`'s `MAP_SCALE` is the same number.
 */
export const UNITS_PER_METRE = 3;
