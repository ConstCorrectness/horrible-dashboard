/**
 * Surface breakup: what stops a photographed floor reading as one tile stamped
 * a thousand times. The native client's `surface_breakup` and anti-tiling in
 * `shader.wgsl`, as a patch on three's own materials; `browser_parity.rs` pins
 * the numbers in both.
 *
 * Three parts, all content — the same UVs (`planarUv`), the same world
 * positions, the same baked AO on every machine — so it is the same picture at
 * every tier, Lambert twins included:
 *
 * - **Anti-tiling**, Quílez's offset technique. A noise over the UV picks a
 *   band, each band shifts the tile by its own offset, and a pixel blends the
 *   two bands it falls between. The colour, normal and roughness maps are all
 *   sampled at the same two offsets with the same weight. Offsets only, never a
 *   rotation, so the normal map stays a valid tangent-space normal. Not on
 *   glass or the site markings: a letter offset is a different letter. The
 *   weight is the band's fraction alone (no colour nudge), so it is exactly 0
 *   or 1 where an offset jumps, and plain implicit-derivative reads are safe.
 * - **A macro tint**: a slow two-octave noise in world space moves a surface's
 *   albedo ±10%, so a wall is not one exact colour from end to end.
 * - **Grime**, where the baked AO is low — wall bases, corners, undersides —
 *   pulled towards a dirt colour and broken up by a third noise. The AO already
 *   rides in the vertex colour (`maplib.bake_vertex_ao`), so it costs no new
 *   data. (The plan also named height above the floor; there is no floor
 *   height to read per pixel on a multi-level map, and the AO bake is already
 *   darkest exactly at the foot of a wall.)
 *
 * The same patch carries the map's **baked bounce light** (`bounce-light.ts`)
 * into three's `irradiance`, where a light map goes, on every GLB material and
 * every tier — glass included, which is otherwise left alone.
 *
 * **One `onBeforeCompile` per material.** Nothing else patches a modelled map's
 * materials (`reveal.ts` patches the cube maps' one material), and
 * `surface-quality.ts` copies the patch onto each Lambert twin.
 *
 * Takes the three namespace as an argument, so this file never pulls three into
 * the bundle.
 */
import type * as THREE from 'three';

import { BOUNCE_ATTRIBUTE } from './bounce-light';
import type { SurfaceKind } from './glb-surfaces';

/** How often the anti-tiling's offset changes, per tile (native `ANTI_TILE_FREQ`). */
export const ANTI_TILE_FREQ = 0.15;
/** The macro tint's frequency in world units, and its reach (native `MACRO_*`). */
export const MACRO_FREQ = 0.045;
export const MACRO_AMOUNT = 0.1;
/** Grime: full at or below the first AO, none at or above the second (native `GRIME_*`). */
export const GRIME_AO_FULL = 0.55;
export const GRIME_AO_NONE = 0.95;
export const GRIME_STRENGTH = 0.55;
export const GRIME_TINT: readonly [number, number, number] = [0.62, 0.55, 0.47];

/** A GLSL float literal: `0.1` stays `0.1`, `1` becomes `1.0`. */
function f(x: number): string {
  const s = String(x);
  return s.includes('.') || s.includes('e') ? s : `${s}.0`;
}

/** Shared by the world and the water: the hash and the value noise. */
export const BREAKUP_NOISE_GLSL = /* glsl */ `
// Dave Hoskins' hash without sine (native \`breakup_hash\`): sin of a large
// argument is not the same number on every GPU.
float hdHash(vec2 p) {
  vec3 p3 = fract(vec3(p.x, p.y, p.x) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
float hdNoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = p - i;
  vec2 w = f * f * (3.0 - 2.0 * f);
  float a = hdHash(i);
  float b = hdHash(i + vec2(1.0, 0.0));
  float c = hdHash(i + vec2(0.0, 1.0));
  float d = hdHash(i + vec2(1.0, 1.0));
  return mix(mix(a, b, w.x), mix(c, d, w.x), w.y);
}
`;

const FRAGMENT_COMMON = /* glsl */ `
varying vec3 vHdWorld;
varying vec3 vHdBounce;
${BREAKUP_NOISE_GLSL}
vec3 hdSurfaceBreakup(vec3 albedo, vec3 world, float ao) {
  vec2 q = (world.xz + world.yy * vec2(0.37, 0.61)) * ${f(MACRO_FREQ)};
  vec3 tinted = albedo;
#ifdef HD_MACRO
  float swell = hdNoise(q) * 0.65 + hdNoise(q * 2.7) * 0.35;
  tinted = albedo * (1.0 + ${f(MACRO_AMOUNT)} * (swell * 2.0 - 1.0));
#endif
  // Most of a map is unoccluded: only a corner pays for the grime's noise.
  if (ao >= ${f(GRIME_AO_NONE)}) return tinted;
  float patchy = 0.4 + 0.6 * smoothstep(0.2, 0.8, hdNoise(q * 3.1 + vec2(17.0, 5.0)));
  float dirt = (1.0 - smoothstep(${f(GRIME_AO_FULL)}, ${f(GRIME_AO_NONE)}, ao)) * patchy;
  return mix(tinted, tinted * vec3(${GRIME_TINT.map(f).join(', ')}), dirt * ${f(GRIME_STRENGTH)});
}
#ifdef HD_ANTI_TILE
  // A texture at the two band offsets, blended. Implicit derivatives: where an
  // offset jumps, its sample's weight is exactly zero (native \`anti_tile\`).
  #define HD_SAMPLE(t, uv) mix(texture2D(t, (uv) + hdOffA), texture2D(t, (uv) + hdOffB), hdTileW)
#else
  #define HD_SAMPLE(t, uv) texture2D(t, uv)
#endif
`;

/** The colour map, anti-tiled: it decides the bands and the weight the other maps reuse. */
const MAP_FRAGMENT = /* glsl */ `
vec2 hdOffA = vec2(0.0);
vec2 hdOffB = vec2(0.0);
float hdTileW = 0.0;
#if defined(USE_MAP) && defined(HD_ANTI_TILE)
  {
    float k = hdNoise(vMapUv * ${f(ANTI_TILE_FREQ)}) * 8.0;
    float i = floor(k);
    hdOffA = sin(vec2(3.0, 7.0) * i);
    hdOffB = sin(vec2(3.0, 7.0) * (i + 1.0));
    hdTileW = smoothstep(0.2, 0.8, k - i);
    diffuseColor *= mix(texture2D(map, vMapUv + hdOffA), texture2D(map, vMapUv + hdOffB), hdTileW);
  }
#elif defined(USE_MAP)
  diffuseColor *= texture2D(map, vMapUv);
#endif
`;

/** After the vertex colour (the AO) has been multiplied in, as native. */
const BREAKUP_FRAGMENT = /* glsl */ `
#include <color_fragment>
#ifdef HD_BREAKUP
  #if defined(USE_COLOR) || defined(USE_COLOR_ALPHA)
    diffuseColor.rgb = hdSurfaceBreakup(diffuseColor.rgb, vHdWorld, vColor.r);
  #else
    diffuseColor.rgb = hdSurfaceBreakup(diffuseColor.rgb, vHdWorld, 1.0);
  #endif
#endif
`;

/** Whether a kind is anti-tiled and broken up. Glass is neither; a site marking
 * is weathered but never offset. */
export function breakupFor(kind: SurfaceKind): { antiTile: boolean; breakup: boolean } {
  if (kind === 'glass') return { antiTile: false, breakup: false };
  if (kind === 'site_a' || kind === 'site_b') return { antiTile: false, breakup: true };
  return { antiTile: true, breakup: true };
}

/**
 * Patch the material for its kind: the full breakup, for the physical tiers.
 * Idempotent.
 */
export function installSurfaceBreakup(
  three: typeof THREE,
  material: THREE.Material,
  kind: SurfaceKind,
): void {
  const spec = breakupFor(kind);
  // Every kind is patched, for the bounce light, even one with no breakup.
  material.userData[SPEC] = spec;
  setPatch(three, material, spec.antiTile, spec.breakup, spec.breakup);
}

const SPEC = 'hdBreakup';

/**
 * Switch a patched physical material between the full breakup (High: `full`)
 * and the grime only (Medium), as native switches on `detail`. A recompile, so
 * it runs on a tier change, never per frame.
 */
export function setBreakupDetail(
  three: typeof THREE,
  material: THREE.Material,
  full: boolean,
): void {
  const spec = material.userData[SPEC] as ReturnType<typeof breakupFor> | undefined;
  if (!spec) return;
  if (full) setPatch(three, material, spec.antiTile, spec.breakup, spec.breakup);
  else if (spec.breakup) setPatch(three, material, false, false, true);
}

/**
 * Low's Lambert twin (`surface-quality.ts`): the grime only, if the material
 * it stands in for has any (`TierSpec.breakup` says why), and the bounce.
 */
export function installLowTierBreakup(
  three: typeof THREE,
  twin: THREE.Material,
  source: THREE.Material,
): void {
  const spec = source.userData[SPEC] as ReturnType<typeof breakupFor> | undefined;
  if (!spec) return;
  setPatch(three, twin, false, false, spec.breakup);
}

function setPatch(
  three: typeof THREE,
  material: THREE.Material,
  antiTile: boolean,
  macro: boolean,
  grime: boolean,
): void {
  const key = `hd-breakup-3${antiTile ? '-tile' : ''}${macro ? '-tint' : ''}${grime ? '-grime' : ''}`;
  if (material.customProgramCacheKey() === key) return;
  material.onBeforeCompile = patchFor(three, antiTile, macro, grime);
  material.customProgramCacheKey = () => key;
  material.needsUpdate = true;
}

/** Every occurrence: `normal_fragment_maps` samples the normal map in both its
 * object-space and its tangent-space branch, and `replace` would take only the
 * first, which is not the one these maps use. */
function swapAll(source: string, from: string, to: string): string {
  return source.split(from).join(to);
}

function patchFor(three: typeof THREE, antiTile: boolean, macro: boolean, breakup: boolean) {
  const normalMaps = swapAll(
    three.ShaderChunk.normal_fragment_maps,
    'texture2D( normalMap, vNormalMapUv )',
    'HD_SAMPLE( normalMap, vNormalMapUv )',
  );
  const roughnessMap = swapAll(
    three.ShaderChunk.roughnessmap_fragment,
    'texture2D( roughnessMap, vRoughnessMapUv )',
    'HD_SAMPLE( roughnessMap, vRoughnessMapUv )',
  );
  return (shader: THREE.WebGLProgramParametersWithUniforms) => {
    const defines = `${antiTile ? '#define HD_ANTI_TILE\n' : ''}${macro ? '#define HD_MACRO\n' : ''}${breakup ? '#define HD_BREAKUP\n' : ''}`;
    shader.vertexShader = shader.vertexShader
      .replace(
        '#include <common>',
        `#include <common>\nvarying vec3 vHdWorld;\n${BOUNCE_VERTEX_COMMON}`,
      )
      .replace(
        '#include <project_vertex>',
        '#include <project_vertex>\n  vHdWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;\n  vHdBounce = hdBounce;',
      );
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `${defines}#include <common>\n${FRAGMENT_COMMON}`)
      .replace('#include <map_fragment>', MAP_FRAGMENT)
      .replace('#include <color_fragment>', BREAKUP_FRAGMENT)
      .replace('#include <normal_fragment_maps>', normalMaps)
      .replace('#include <roughnessmap_fragment>', roughnessMap)
      .replace('#include <lights_fragment_maps>', BOUNCE_FRAGMENT);
  };
}

/**
 * The bounce, decoded at load (`BOUNCE_ATTRIBUTE`). A geometry without the
 * attribute reads WebGL's default of zero, so a material shared with one
 * gathers none there.
 */
const BOUNCE_VERTEX_COMMON = /* glsl */ `attribute vec3 ${BOUNCE_ATTRIBUTE};
varying vec3 vHdBounce;
`;

/**
 * Added to the irradiance the rig's lights already put there, before
 * `BRDF_Lambert` — three's own light-map slot, and native's
 * `bounce_irradiance`. Physical and Lambert both define `RE_IndirectDiffuse`.
 */
const BOUNCE_FRAGMENT = /* glsl */ `#include <lights_fragment_maps>
#if defined( RE_IndirectDiffuse )
  irradiance += vHdBounce;
#endif
`;
