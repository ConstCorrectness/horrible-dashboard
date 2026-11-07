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
 * **The surface** is native's `water_surface` (`shader.wgsl`), patched onto the
 * Lambert material: three travelling waves bend the normal (never the plane),
 * the map's environment — its sky above, `hemiGround` below, the function its
 * prefiltered environment is built from (`environment.ts`) — is reflected by
 * Schlick's fresnel, and the same fresnel raises the alpha at a grazing angle,
 * where a surface is a mirror rather than a window. `browser_parity.rs` pins
 * the numbers in both.
 *
 * Takes the three namespace as a parameter rather than importing it, so this file
 * never pulls three into the bundle — the same contract as `avatars.ts`,
 * `effects.ts`, `backdrop.ts`, `nades.ts`, `items.ts` and `surfaces.ts`.
 */
import type * as THREE from 'three';

import type { Atmosphere } from './api';
import { srgbHexToLinear } from './environment';
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

/** Water's reflectance head-on (native `WATER_F0`). */
export const WATER_F0 = 0.02;
/** The wave clock wraps here (native `WATER_CLOCK_WRAP`): every wave's
 *  frequency divides 8 s, so the wrap does not show. */
export const WATER_CLOCK_WRAP = 64;

/** The waves, the environment and the fresnel: native `water_wave_slope`,
 *  `environment_radiance` and `water_surface`, line for line. */
const WATER_COMMON = /* glsl */ `
uniform float hdWaterTime;
uniform vec3 hdSkyZenith;
uniform vec3 hdSkyHorizon;
uniform vec3 hdGround;
varying vec3 vHdWorld;
vec2 hdWaveSlope(vec2 p, float t) {
  float tau = 6.2831853;
  vec2 d1 = vec2(0.80, 0.60);
  vec2 d2 = vec2(-0.60, 0.80);
  vec2 d3 = vec2(0.28, -0.96);
  return d1 * (0.045 * cos(tau * (dot(d1, p) / 4.0 - 0.25 * t)))
    + d2 * (0.035 * cos(tau * (dot(d2, p) / 2.3 - 0.375 * t)))
    + d3 * (0.025 * cos(tau * (dot(d3, p) / 1.1 - 0.5 * t)));
}
vec3 hdEnvironment(vec3 d) {
  vec3 far = d.y >= 0.0 ? hdSkyZenith : hdGround;
  return mix(hdSkyHorizon, far, sqrt(min(1.0, abs(d.y))));
}
`;

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
  atmosphere: Pick<Atmosphere, 'skyZenith' | 'skyHorizon' | 'hemiGround'>,
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
  const uniforms = {
    hdWaterTime: { value: 0 },
    hdSkyZenith: { value: new three.Vector3(...srgbHexToLinear(atmosphere.skyZenith)) },
    hdSkyHorizon: { value: new three.Vector3(...srgbHexToLinear(atmosphere.skyHorizon)) },
    hdGround: { value: new three.Vector3(...srgbHexToLinear(atmosphere.hemiGround)) },
  };
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vHdWorld;')
      .replace(
        '#include <project_vertex>',
        '#include <project_vertex>\n  vHdWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;',
      );
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${WATER_COMMON}`)
      // The wave normal, in place of the plane's, for the lighting too.
      .replace(
        '#include <normal_fragment_maps>',
        `#include <normal_fragment_maps>
  vec2 hdSlope = hdWaveSlope(vHdWorld.xz, hdWaterTime);
  vec3 hdN = normalize(vec3(-hdSlope.x, 1.0, -hdSlope.y));
  // The underside, seen from in the pool, faces down.
  if (!gl_FrontFacing) hdN = -hdN;
  normal = normalize((viewMatrix * vec4(hdN, 0.0)).xyz);`,
      )
      .replace(
        '#include <opaque_fragment>',
        `vec3 hdV = normalize(cameraPosition - vHdWorld);
  float hdF = ${WATER_F0} + (1.0 - ${WATER_F0}) * pow(1.0 - max(dot(hdN, hdV), 0.0), 5.0);
  outgoingLight = mix(outgoingLight, hdEnvironment(reflect(-hdV, hdN)), hdF);
  diffuseColor.a = mix(diffuseColor.a, 1.0, hdF);
#include <opaque_fragment>`,
      );
  };
  material.customProgramCacheKey = () => 'hd-water-1';
  const mesh = new three.Mesh(geometry, material);
  // `PlaneGeometry` lies in XY, and the scene is y-up: laid flat, then
  // placed at `(x, height, y)` like every other map placement. Left in XY at
  // `z = waterlevel` it was a vertical sheet across the map a few cubes from
  // one edge — water nobody could stand in, and none where the pools were.
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.set(size / 2, world.waterlevel, size / 2);
  // Drawn after the world so blending has something to blend with.
  mesh.renderOrder = 1;
  scene.add(mesh);

  return {
    update(elapsed: number) {
      mesh.position.y = world.waterlevel + Math.sin(elapsed * RIPPLE_SPEED) * RIPPLE;
      uniforms.hdWaterTime.value = ((elapsed % WATER_CLOCK_WRAP) + WATER_CLOCK_WRAP) % WATER_CLOCK_WRAP;
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
