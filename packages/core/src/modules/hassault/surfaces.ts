/**
 * The grain on every surface in the world.
 *
 * The map mesh is untextured by necessity — AssaultCube's textures are its
 * copyright and are never bundled (docs/modules/hassault.mdx) — and the
 * consequence was that every wall in the game was one flat colour across its
 * whole face. Lighting alone cannot fix that: a flat surface lit evenly is still
 * flat, and at a distance the world read as coloured cardboard.
 *
 * So the detail is **generated here, at runtime, from nothing**: one small
 * seamless tile of value noise and cube seams, used as a multiplier over the
 * vertex colours the mesh already carries. It is nobody's artwork, it adds one
 * texture to the whole scene, and it gives the eye something to resolve scale
 * and distance against — which is the entire job.
 *
 * Two things it deliberately is *not*:
 *
 * - **Not a colour.** The tile is greyscale around 1.0, so it modulates the
 *   surface tint that `geometry.ts` assigns per texture id rather than replacing
 *   it. A detail map that carried its own hue would make every surface in every
 *   map the same colour, which is the flatness this exists to remove.
 * - **Not sRGB.** It is a multiplier, so it is sampled linearly: authored 1.0
 *   has to mean "leave this pixel alone", and an sRGB decode would darken every
 *   surface in the game by a third and look like a lighting bug.
 *
 * Takes the three namespace as an argument rather than importing it, so this file
 * never pulls three into the bundle — the same contract as avatars.ts,
 * effects.ts and backdrop.ts.
 */
import type * as THREE from 'three';

/**
 * Tile resolution. Small on purpose: it is sampled once per cube, so detail
 * beyond this is smaller than a pixel at any distance you would notice it, and
 * the tile is regenerated on every map load.
 */
const SIZE = 128;

/**
 * How far the grain swings either side of neutral.
 *
 * Low enough that it reads as a material rather than as dirt: the surface tints
 * are already muted, and noise loud enough to see on its own turns every wall
 * into television static.
 */
const GRAIN = 0.11;

/** How much darker the seam at the edge of each cube is. */
const SEAM = 0.16;

/**
 * A small deterministic hash, so the same map always gets the same grain.
 *
 * Determinism matters more than it looks: the tile is regenerated when a map
 * loads, and a random one would mean the same wall in the same map looking
 * different every time you pressed Play, with nothing to explain why.
 */
function hash(x: number, y: number): number {
  const n = Math.sin(x * 127.1 + y * 311.7) * 43758.5453123;
  return n - Math.floor(n);
}

/** Value noise: a hashed lattice, smoothly interpolated. */
function valueNoise(x: number, y: number, period: number): number {
  const xi = Math.floor(x);
  const yi = Math.floor(y);
  const xf = x - xi;
  const yf = y - yi;
  // Smoothstep the fraction, or the lattice shows as a grid of diamonds.
  const u = xf * xf * (3 - 2 * xf);
  const v = yf * yf * (3 - 2 * yf);
  // Wrapped, so the tile is seamless — an unwrapped lattice puts a hard line
  // down every cube boundary in the world.
  const w = (n: number) => ((n % period) + period) % period;
  const a = hash(w(xi), w(yi));
  const b = hash(w(xi + 1), w(yi));
  const c = hash(w(xi), w(yi + 1));
  const d = hash(w(xi + 1), w(yi + 1));
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}

/**
 * Draw one seamless tile of surface detail.
 *
 * Exported for the test, which is the only way to check a generated texture at
 * all: there is no reference image to compare against, so what is pinned is the
 * two properties that make it usable — it stays near neutral, and it wraps.
 */
export function drawDetailTile(size = SIZE): Uint8Array {
  const out = new Uint8Array(size * size * 4);
  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      // Three octaves. The coarse one gives a surface large-scale mottling so it
      // does not read as uniform sandpaper; the fine one is the grain itself.
      const u = px / size;
      const v = py / size;
      let n = 0;
      n += (valueNoise(u * 4, v * 4, 4) - 0.5) * 0.55;
      n += (valueNoise(u * 12, v * 12, 12) - 0.5) * 0.3;
      n += (valueNoise(u * 32, v * 32, 32) - 0.5) * 0.15;

      // The seam: a soft dark line at the tile edge. The UVs are in cube units,
      // so this draws the cube lattice the map is actually built on — which is
      // architecture in a Cube-engine level, not a tiling artefact.
      const edge = Math.min(Math.min(u, 1 - u), Math.min(v, 1 - v));
      const seam = SEAM * (1 - Math.min(1, edge / 0.035));

      const value = Math.max(0, Math.min(1.35, 1 + n * GRAIN * 2 - seam));
      // Stored over 255 with 1.0 mapped to 189, so the tile can brighten as well
      // as darken without clipping — a multiplier that can only subtract is a
      // dirt map, and it drags the whole world dark.
      const byte = Math.round(Math.max(0, Math.min(255, value * 189)));
      const i = (py * size + px) * 4;
      out[i] = byte;
      out[i + 1] = byte;
      out[i + 2] = byte;
      out[i + 3] = 255;
    }
  }
  return out;
}

/**
 * What `drawDetailTile` writes for a pixel with no grain and no seam.
 *
 * The material's own `color` is set to its reciprocal, which is what makes a
 * neutral pixel leave a surface exactly as `geometry.ts` coloured it while still
 * letting the tile brighten. Compensating in the material rather than in a
 * shader patch matters: `onBeforeCompile` is a single slot and `reveal.ts`
 * already owns it, so a second patch would silently replace the first and the
 * map would stop building itself.
 */
export const DETAIL_NEUTRAL = 189 / 255;

/**
 * Build the detail texture.
 *
 * A `DataTexture` rather than a canvas: the pixels are computed anyway, and a
 * canvas would mean this module could not run headless — which the test needs
 * and a future server-side render would too.
 */
export function createDetailTexture(three: typeof THREE, anisotropy = 1): THREE.DataTexture {
  const data = drawDetailTile();
  const texture = new three.DataTexture(data, SIZE, SIZE, three.RGBAFormat);
  texture.wrapS = three.RepeatWrapping;
  texture.wrapT = three.RepeatWrapping;
  texture.magFilter = three.LinearFilter;
  // Mipmapped and anisotropic, which is not optional here: the UVs are in cube
  // units, so a floor seen down a corridor is sampling hundreds of tiles per
  // pixel row, and without both it boils into moiré the moment you walk.
  texture.minFilter = three.LinearMipmapLinearFilter;
  texture.generateMipmaps = true;
  texture.anisotropy = anisotropy;
  // Linear, not sRGB: see the note at the top of the file.
  texture.colorSpace = three.LinearSRGBColorSpace;
  texture.needsUpdate = true;
  return texture;
}
