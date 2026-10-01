import { describe, expect, it } from 'vitest';

import {
  CUBE_DEFAULT,
  GLTF_DEFAULT,
  candela,
  lightPosition,
  nearestLights,
  pointAttenuation,
  resolveAtmosphere,
  unit,
} from '../atmosphere';
import { TIERS, autoTier, isGraphicsChoice, resolveTier } from '../graphics';
import type { MapLight } from '../api';

const light = (x: number, radius = 10, intensity = 1): MapLight => ({
  x,
  y: 0,
  z: 0,
  radius,
  color: 0xffffff,
  intensity,
});

describe('atmosphere', () => {
  it('uses the served block, and the format default only without one', () => {
    const served = { ...GLTF_DEFAULT, sunColor: 0xff0000 };
    expect(resolveAtmosphere({ atmosphere: served, format: 'gltf' })).toBe(served);
    expect(resolveAtmosphere({ format: 'gltf' })).toBe(GLTF_DEFAULT);
    expect(resolveAtmosphere({ format: 'cube' })).toBe(CUBE_DEFAULT);
    expect(resolveAtmosphere({ atmosphere: null })).toBe(CUBE_DEFAULT);
  });

  it('keeps the rig the pane always had as the cube default', () => {
    // These were literals in HorribleAssaultPanel.tsx; a map with no
    // atmosphere must look exactly as it did.
    expect(CUBE_DEFAULT.hemiSky).toBe(0xbfd4ff);
    expect(CUBE_DEFAULT.sunIntensity).toBe(1.75);
    expect(CUBE_DEFAULT.fogDensity).toBe(0.0055);
    expect(CUBE_DEFAULT.exposure).toBe(1.15);
  });

  it('attenuates like three: inverse square, windowed to the range', () => {
    expect(pointAttenuation(2, 1000)).toBeCloseTo(0.25, 4);
    expect(pointAttenuation(10, 10)).toBe(0);
    expect(pointAttenuation(12, 10)).toBe(0);
    const w = (1 - 1 / 16) ** 2;
    expect(pointAttenuation(5, 10)).toBeCloseTo(w / 25, 6);
  });

  it('scales a light with its reach linearly, never quadratically', () => {
    // A lamp twice as wide is twice as bright at the source, not four times —
    // the quadratic convention blew every wide lamp out to white.
    expect(candela(light(0, 16))).toBeCloseTo(2 * candela(light(0, 8)), 6);
    expect(candela(light(0, 8, 2))).toBeCloseTo(8, 6);
  });

  it('maps a light z-up to y-up like every other placement', () => {
    expect(lightPosition({ ...light(10), y: 20, z: 5 })).toEqual([10, 5, 20]);
  });

  it('keeps the lights whose reach comes nearest the eye', () => {
    const lights = [light(300), light(0), light(100), light(200)];
    const kept = nearestLights(lights, [0, 0, 0], 2).map((l) => l.x);
    expect(kept).toEqual([0, 100]);
    expect(nearestLights(lights, [0, 0, 0], 0)).toEqual([]);
    // A dark or zero-radius light is never worth a slot.
    expect(nearestLights([light(0, 0), light(5, 10, 0)], [0, 0, 0], 4)).toEqual([]);
  });

  it('normalises a served direction and survives a zero one', () => {
    const [x, y, z] = unit([0, 2, 0]);
    expect([x, y, z]).toEqual([0, 1, 0]);
    expect(unit([0, 0, 0])).toEqual([0, 1, 0]);
  });
});

describe('graphics tiers', () => {
  it('high draws the map the way the native client does at High', () => {
    expect(TIERS.high.fogScale).toBe(1);
    expect(TIERS.high.shadowSize).toBe(2048);
    // Every tier draws the same sky, fog and lamps — fewer lamps, never others.
    expect(TIERS.low.lights).toBeLessThan(TIERS.medium.lights);
    expect(TIERS.medium.lights).toBeLessThan(TIERS.high.lights);
  });

  it('auto is conservative about what it cannot see', () => {
    expect(autoTier({ devicePixelRatio: 1, renderer: 'Google SwiftShader' })).toBe('low');
    expect(autoTier({ devicePixelRatio: 1, renderer: 'NVIDIA GeForce RTX 4080' })).toBe('high');
    // Entry-level integrated: Medium measured 43 fps on a UHD, Low 162.
    expect(autoTier({ devicePixelRatio: 1, renderer: 'Intel(R) UHD Graphics 620' })).toBe('low');
    expect(autoTier({ devicePixelRatio: 1, renderer: 'Intel(R) Iris(R) Xe Graphics' })).toBe('medium');
    expect(autoTier({ devicePixelRatio: 3 })).toBe('medium');
    expect(autoTier({ devicePixelRatio: 1, cores: 2 })).toBe('low');
  });

  it('an explicit choice outranks the hints', () => {
    expect(resolveTier('low', { devicePixelRatio: 1, renderer: 'RTX 4090' })).toBe('low');
    expect(isGraphicsChoice('ultra')).toBe(false);
    expect(isGraphicsChoice('auto')).toBe(true);
  });
});
