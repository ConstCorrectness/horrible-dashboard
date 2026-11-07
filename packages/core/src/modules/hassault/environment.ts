/**
 * The light a modelled map's surfaces reflect and take ambient from: its own
 * sky above the horizon and its ground below, image-based through three's
 * `scene.environment`.
 *
 * **What it is made of.** Above the horizon, the served sky — the same
 * horizon-to-zenith gradient the dome draws (`createSky`, `sky_radiance` in
 * `lighting.wgsl.inc`), square-rooted so the horizon band stays narrow. Below
 * it, the same shape down to the served hemisphere *ground* colour, because
 * what a floor's underside or a gun's lower half sees is the ground, not more
 * horizon. **No sun disc**: the sun is already an analytic light with a GGX
 * highlight, and putting it in the environment too would light every glossy
 * surface with it twice.
 *
 * **Why a function of height only.** Nothing in it depends on azimuth, so the
 * environment is the same whichever way a map faces, and the native client's
 * cube (`environment.rs`) is the same function sampled another way. Both are
 * pinned to this one by `browser_parity.rs`.
 *
 * **Prefiltered** through `PMREMGenerator` (three's GGX importance sampling),
 * which is what lets a matte wall take the blurred sky and a polished floor a
 * sharp one from one texture. The source is half float, linear, 2:1: a source
 * much smaller than this drives PMREM to a degenerate, black output (see
 * `createPropEnvironment`).
 *
 * Takes three as a parameter, like `atmosphere.ts`, so the radiance function is
 * testable with no WebGL context.
 */
import type * as THREE from 'three';

import type { Atmosphere } from './api';

type Vec3 = [number, number, number];

/** The source's size: 2:1, as an equirectangular map must be. */
export const ENV_WIDTH = 256;
export const ENV_HEIGHT = 128;

/** three's `SRGBToLinear`, per channel of a hex colour (`Color.setHex`). */
export function srgbHexToLinear(hex: number): Vec3 {
  const decode = (c: number) =>
    c < 0.04045 ? c * 0.0773993808 : Math.pow(c * 0.9478672986 + 0.0521327014, 2.4);
  return [decode(((hex >> 16) & 255) / 255), decode(((hex >> 8) & 255) / 255), decode((hex & 255) / 255)];
}

/**
 * The environment's radiance looking along a direction whose height is `y`
 * (the direction's y component, -1 straight down to 1 straight up). Linear,
 * before exposure, like the sky dome's.
 */
export function environmentRadiance(
  a: Pick<Atmosphere, 'skyZenith' | 'skyHorizon' | 'hemiGround'>,
  y: number,
): Vec3 {
  const horizon = srgbHexToLinear(a.skyHorizon);
  const far = srgbHexToLinear(y >= 0 ? a.skyZenith : a.hemiGround);
  const t = Math.sqrt(Math.min(1, Math.abs(y)));
  return [0, 1, 2].map((i) => horizon[i] + (far[i] - horizon[i]) * t) as Vec3;
}

/**
 * The direction height a row of the equirectangular source stands for. A
 * `DataTexture` is uploaded with `flipY` off, so row 0 is `v = 0`, and three's
 * `equirectUv` puts `v = 0` at **straight down** (`v = asin(y) / PI + 0.5`).
 * `createPropEnvironment` once wrote its sky into row 0 and lit every gun
 * from below; this is the inverse of three's mapping, so it cannot.
 */
export function rowHeight(row: number, height = ENV_HEIGHT): number {
  const v = (row + 0.5) / height;
  return Math.sin((v - 0.5) * Math.PI);
}

/**
 * The map's prefiltered environment, for `scene.environment`. The caller owns
 * the returned target and disposes it when the map changes.
 */
export function createMapEnvironment(
  three: typeof THREE,
  renderer: THREE.WebGLRenderer,
  atmosphere: Atmosphere,
): THREE.WebGLRenderTarget {
  const data = new Uint16Array(ENV_WIDTH * ENV_HEIGHT * 4);
  const one = three.DataUtils.toHalfFloat(1);
  for (let row = 0; row < ENV_HEIGHT; row++) {
    const c = environmentRadiance(atmosphere, rowHeight(row)).map((x) => three.DataUtils.toHalfFloat(x));
    for (let x = 0; x < ENV_WIDTH; x++) {
      const i = (row * ENV_WIDTH + x) * 4;
      data[i] = c[0];
      data[i + 1] = c[1];
      data[i + 2] = c[2];
      data[i + 3] = one;
    }
  }
  const source = new three.DataTexture(data, ENV_WIDTH, ENV_HEIGHT, three.RGBAFormat, three.HalfFloatType);
  source.mapping = three.EquirectangularReflectionMapping;
  // Radiance, already linear: decoded from the served hex above.
  source.colorSpace = three.LinearSRGBColorSpace;
  source.minFilter = three.LinearFilter;
  source.magFilter = three.LinearFilter;
  source.needsUpdate = true;
  const pmrem = new three.PMREMGenerator(renderer);
  const target = pmrem.fromEquirectangular(source);
  pmrem.dispose();
  source.dispose();
  return target;
}
