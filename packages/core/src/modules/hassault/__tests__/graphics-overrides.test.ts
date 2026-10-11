/**
 * The browser's graphics rows: a level is a preset, and each row a player
 * changes is kept as an override on top of it.
 */
import { describe, expect, it } from 'vitest';

import {
  GRAPHICS_OPTIONS,
  GRAPHICS_TOGGLES,
  MAX_LIGHTS,
  TIERS,
  effectiveSpec,
  parseOverrides,
  withOverride,
} from '../graphics';

describe('graphics overrides', () => {
  it('lays a row over its level and leaves the others alone', () => {
    const spec = effectiveSpec('low', { shadowSize: 4096 });
    expect(spec.shadowSize).toBe(4096);
    expect(spec.lights).toBe(TIERS.low.lights);
    expect(spec.lambert).toBe(true);
  });

  it('drops an override that is moved back to the level, so the menu leaves Custom', () => {
    const changed = withOverride({}, 'medium', 'lights', 8);
    expect(changed).toEqual({ lights: 8 });
    expect(withOverride(changed, 'medium', 'lights', TIERS.medium.lights)).toEqual({});
  });

  it('trusts nothing it reads back: unknown rows and off-list values are dropped', () => {
    const parsed = parseOverrides(
      JSON.stringify({
        lights: 8,
        shadowSize: 7000,
        pixelRatio: '2',
        sky: false,
        fogScale: 3,
        photos: 'yes',
      }),
    );
    expect(parsed).toEqual({ lights: 8, sky: false });
    expect(parseOverrides('not json')).toEqual({});
    expect(parseOverrides(undefined)).toEqual({});
  });

  it('every level is one of its own rows’ values, so a preset reads back as itself', () => {
    for (const spec of Object.values(TIERS)) {
      for (const [key, options] of Object.entries(GRAPHICS_OPTIONS)) {
        expect(options as readonly number[]).toContain(spec[key as keyof typeof spec]);
      }
      expect(parseOverrides(spec)).toEqual(spec);
    }
  });

  it('never asks for more lamps than the scene has in its pool', () => {
    expect(MAX_LIGHTS).toBeGreaterThanOrEqual(TIERS.high.lights);
  });

  it('offers no row that could change what a player is able to see', () => {
    const forbidden =
      /fog|smoke|flash|utility|player|character|avatar|prop|cull|draw.?distance|^shadows$/i;
    for (const key of [...Object.keys(GRAPHICS_OPTIONS), ...GRAPHICS_TOGGLES]) {
      expect(key).not.toMatch(forbidden);
    }
  });
});
