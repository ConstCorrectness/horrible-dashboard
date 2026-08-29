/**
 * Drawing the map's water plane.
 *
 * `waterlevel` has been parsed since the reader was written and drawn by nothing,
 * which was survivable only while it also *did* nothing. Now that it decides how
 * a body moves, an invisible water plane would be the worst kind of bug: a player
 * who suddenly cannot jump, moves at two thirds speed and takes no fall damage,
 * with no way to see why.
 *
 * It is one plane, not a volume. Cube 1 water is a single global height — there
 * is no per-cell water — so a quad at `waterlevel` spanning the map is an exact
 * depiction rather than an approximation, and the parts of it inside rock are
 * hidden by the rock.
 *
 * **Rendered from both sides** (`DoubleSide`), because you spend real time under
 * it: seen from below the surface is the ceiling of the pool, and a
 * back-face-culled plane would make swimming look like standing in tinted air.
 *
 * The colour is the **map's own** `watercolor`, not a constant. A mapper choosing
 * green water meant it, and a global blue would quietly overrule every map.
 *
 * Takes the three namespace as a parameter rather than importing it, so this file
 * never pulls three into the bundle — the same contract as `avatars.ts`,
 * `effects.ts`, `backdrop.ts`, `nades.ts`, `items.ts` and `surfaces.ts`.
 */
import type * as THREE from 'three';

import type { World } from './world';

/** Fallback tint for a map whose `watercolor` is unset (every channel zero). */
const DEFAULT_COLOR = 0x2f6f8f;

/** How see-through the surface is. Opaque water hides the bottom of every pool. */
const OPACITY = 0.42;

/** Ripple: how far the surface swings, and how fast. Small on purpose — this is a
 *  plane a player has to judge a jump against, so it must not visibly move the
 *  line they are aiming at. */
const RIPPLE = 0.06;
const RIPPLE_SPEED = 0.9;

export interface WaterSurface {
  update(elapsed: number): void;
  dispose(): void;
}

/**
 * Build the surface, or `null` when the map has none.
 *
 * Returning `null` rather than an invisible mesh keeps the "no water" case free:
 * most maps have none, and every one of them would otherwise carry a plane the
 * renderer sorts, lights and blends every frame for nothing.
 */
export function createWater(
  three: typeof THREE,
  scene: THREE.Scene,
  world: World,
): WaterSurface | null {
  if (!hasWater(world)) return null;

  const size = world.ssize;
  const geometry = new three.PlaneGeometry(size, size);
  const [r, g, b, a] = world.info.watercolor ?? [0, 0, 0, 0];
  const color = r || g || b ? (r << 16) | (g << 8) | b : DEFAULT_COLOR;
  const material = new three.MeshLambertMaterial({
    color,
    transparent: true,
    // A map may carry its own alpha; zero means "unset", not "invisible".
    opacity: a ? Math.min(1, a / 255) : OPACITY,
    side: three.DoubleSide,
    // Water does not occlude what is behind it, and writing depth makes every
    // submerged surface disappear behind the plane rather than tint through it.
    depthWrite: false,
  });
  const mesh = new three.Mesh(geometry, material);
  mesh.position.set(size / 2, size / 2, world.waterlevel);
  // Drawn after the world so blending has something to blend with.
  mesh.renderOrder = 1;
  scene.add(mesh);

  return {
    update(elapsed: number) {
      mesh.position.z = world.waterlevel + Math.sin(elapsed * RIPPLE_SPEED) * RIPPLE;
    },
    dispose() {
      scene.remove(mesh);
      geometry.dispose();
      material.dispose();
    },
  };
}

/**
 * Whether this map has water worth drawing.
 *
 * A plane below every floor is how a `.cgz` says "no water" — every official map
 * ships one — so the test is not "is `waterlevel` set" but "is any floor under
 * it". Scanning the grid once at load is cheap and is the only reading that
 * matches what `physics.inWater` will actually do.
 */
export function hasWater(world: World): boolean {
  const level = world.waterlevel;
  for (let y = 0; y < world.ssize; y += 1) {
    for (let x = 0; x < world.ssize; x += 1) {
      if (world.isSolid(x, y)) continue;
      if (world.floorAt(x, y) < level) return true;
    }
  }
  return false;
}
