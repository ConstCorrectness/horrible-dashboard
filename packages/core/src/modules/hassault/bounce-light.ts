/**
 * Baked bounce light: the second bounce a map's sun, sky and lamps put on its
 * surfaces, baked offline into COLOR_1 by `tools/blender/bake_bounce.py`. A
 * sunlit floor warms the wall beside it; a corridor off a bright plaza is not
 * as dark as one off a cellar.
 *
 * Content, like the baked AO in COLOR_0: the same bake on every machine, at
 * every tier, and never on a character (they are not part of the map's GLB).
 * The native client decodes the same bytes the same way (`world3d.rs`
 * `read_bounce`), and `browser_parity.rs` pins both.
 *
 * **The encoding** is declared in the GLB as `asset.extras.hassaultBounce`:
 * normalised bytes on a square-root curve, `bounce = scale · c²`, in the units
 * of the rig's own irradiance — added to three's `irradiance` before
 * `BRDF_Lambert`, exactly where a light map goes (`surface-breakup.ts`).
 *
 * Decoded once at load into a float attribute (`BOUNCE_ATTRIBUTE`), so the
 * shader only adds it, and the batch merge in `world3d.ts` welds on it.
 *
 * Takes the three namespace as an argument, so this file never pulls three into
 * the bundle.
 */
import type * as THREE from 'three';

/** How a GLB stores its bounce. */
export interface BounceEncoding {
  readonly scale: number;
}

/** The decoded attribute a map's geometry carries: irradiance, rgb. */
export const BOUNCE_ATTRIBUTE = 'hdBounce';

/** GLTFLoader's name for a primitive's COLOR_1 (`COLOR_0` alone is `color`). */
const GLTF_COLOR_1 = 'color_1';

/**
 * The encoding a GLB's `asset` declares, or `null`: no bounce baked, or an
 * encoding this client does not know, whose bytes are then not light.
 */
export function readBounce(asset: unknown): BounceEncoding | null {
  const spec = (asset as { extras?: { hassaultBounce?: unknown } } | undefined)?.extras
    ?.hassaultBounce as { attribute?: unknown; scale?: unknown; curve?: unknown } | undefined;
  if (!spec) return null;
  const { attribute, scale, curve } = spec;
  if (attribute !== 'COLOR_1' || curve !== 'square' || typeof scale !== 'number' || !(scale > 0)) {
    console.warn('[world3d] unknown bounce encoding, not drawn:', spec);
    return null;
  }
  return { scale };
}

/** One channel: `scale · c²`. */
export function decodeBounce(encoding: BounceEncoding, c: number): number {
  return encoding.scale * c * c;
}

/**
 * Replace a primitive's COLOR_1 with its decoded bounce (`BOUNCE_ATTRIBUTE`),
 * or drop it when the map declares no encoding. Read through `getX`: the
 * bytes are normalised, and `.array` would hand them over unscaled.
 */
export function installBounceAttribute(
  three: typeof THREE,
  geometry: THREE.BufferGeometry,
  encoding: BounceEncoding | null,
): void {
  const raw = geometry.getAttribute(GLTF_COLOR_1) as THREE.BufferAttribute | undefined;
  if (!raw) return;
  geometry.deleteAttribute(GLTF_COLOR_1);
  if (!encoding) return;
  const out = new Float32Array(raw.count * 3);
  for (let i = 0; i < raw.count; i++) {
    out[i * 3] = decodeBounce(encoding, raw.getX(i));
    out[i * 3 + 1] = decodeBounce(encoding, raw.getY(i));
    out[i * 3 + 2] = decodeBounce(encoding, raw.getZ(i));
  }
  geometry.setAttribute(BOUNCE_ATTRIBUTE, new three.BufferAttribute(out, 3));
}
