/**
 * The modelled maps' image-based light (`environment.ts`): the served sky
 * above, the served ground below, written into an equirectangular source the
 * right way up. The native client's copy of the same function is pinned
 * against this one by `apps/native-fps/tests/browser_parity.rs`.
 */
import * as THREE from 'three';
import { describe, expect, it } from 'vitest';

import { ENV_HEIGHT, environmentRadiance, rowHeight, srgbHexToLinear } from '../environment';

const DUST2 = { skyZenith: 0x2e6cc7, skyHorizon: 0xd6dce0, hemiGround: 0xb58c5c };

describe('the map environment', () => {
  it('decodes a served hex exactly as three does', () => {
    for (const hex of [0x000000, 0x2e6cc7, 0xd6dce0, 0xb58c5c, 0xffffff]) {
      const color = new THREE.Color().setHex(hex);
      const [r, g, b] = srgbHexToLinear(hex);
      expect(r).toBeCloseTo(color.r, 6);
      expect(g).toBeCloseTo(color.g, 6);
      expect(b).toBeCloseTo(color.b, 6);
    }
  });

  it('is the zenith straight up, the horizon level and the ground straight down', () => {
    const near = (got: number[], want: number[]) =>
      want.forEach((w, i) => expect(got[i]).toBeCloseTo(w, 6));
    near(environmentRadiance(DUST2, 1), srgbHexToLinear(DUST2.skyZenith));
    near(environmentRadiance(DUST2, 0), srgbHexToLinear(DUST2.skyHorizon));
    near(environmentRadiance(DUST2, -1), srgbHexToLinear(DUST2.hemiGround));
  });

  it('leaves the horizon by the square root of the height', () => {
    const horizon = srgbHexToLinear(DUST2.skyHorizon);
    const zenith = srgbHexToLinear(DUST2.skyZenith);
    const quarter = environmentRadiance(DUST2, 0.25);
    for (let i = 0; i < 3; i++) {
      expect(quarter[i]).toBeCloseTo(horizon[i] + (zenith[i] - horizon[i]) * 0.5, 6);
    }
  });

  it('writes row 0 as straight down, where three samples it', () => {
    // flipY is off, so row 0 is v = 0, and three's equirectUv puts v = 0 at
    // the nadir. The generic gun environment once had this backwards.
    expect(rowHeight(0)).toBeLessThan(-0.99);
    expect(rowHeight(ENV_HEIGHT - 1)).toBeGreaterThan(0.99);
    expect(rowHeight(ENV_HEIGHT / 2 - 1)).toBeLessThan(0);
    expect(rowHeight(ENV_HEIGHT / 2)).toBeGreaterThan(0);
  });
});
