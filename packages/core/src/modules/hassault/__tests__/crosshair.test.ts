/**
 * The crosshair, replayed from the fixture the reference generated.
 *
 * `crosshair-vectors.json` is written by `scripts/gen_hassault_crosshair_vectors.py`
 * from `backend/modules/hassault/crosshair.py`, and replayed by the backend suite
 * and the native client's `tests/conformance.rs` too. If this fails after a change
 * to the crosshair, change the Python, regenerate, and port it to both clients.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import {
  CONE_PX_PER_RAD,
  DEFAULT_SPEC,
  LUMA_FLOOR,
  NAMED_COLORS,
  OUTLINE_ALPHA,
  PIP_ALPHA,
  PREFIX,
  PRESETS,
  STYLES,
  VERSION,
  coneGapPx,
  crc8,
  crosshairRects,
  decodeCrosshair,
  encodeCrosshair,
  hudUnit,
  normalizeSpec,
  settingsFromSpec,
  specFromSettings,
  type CrosshairSpec,
} from '../crosshair';

/* eslint-disable @typescript-eslint/no-explicit-any */
const vectors = JSON.parse(
  readFileSync(new URL('./crosshair-vectors.json', import.meta.url), 'utf-8'),
) as Record<string, any>;

describe('crosshair spec v1', () => {
  it('agrees with the reference on every constant', () => {
    expect(PREFIX).toBe(vectors.prefix);
    expect(VERSION).toBe(vectors.version);
    expect([...STYLES]).toEqual(vectors.styles);
    expect(NAMED_COLORS).toEqual(vectors.namedColors);
    expect(LUMA_FLOOR).toBe(vectors.lumaFloor);
    expect(OUTLINE_ALPHA).toBe(vectors.outlineAlpha);
    expect(PIP_ALPHA).toBe(vectors.pipAlpha);
    expect(CONE_PX_PER_RAD).toBe(vectors.conePxPerRad);
    expect(DEFAULT_SPEC).toEqual(vectors.default);
    expect(PRESETS).toEqual(vectors.presets);
  });

  it('computes the same CRC, HUD unit and cone opening', () => {
    for (const c of vectors.crc8) expect(crc8(c.bytes)).toBe(c.crc);
    for (const c of vectors.hudUnit) expect(hudUnit(c.height, c.hudScale)).toBe(c.unit);
    for (const c of vectors.coneGap) expect(coneGapPx(c.cone, c.unit)).toBeCloseTo(c.px, 9);
  });

  it('normalizes and encodes every spec to the same code', () => {
    for (const c of vectors.encode) {
      expect(normalizeSpec(c.spec)).toEqual(c.normalized);
      expect(encodeCrosshair(c.spec as CrosshairSpec)).toBe(c.code);
    }
  });

  it('decodes every paste the reference decodes', () => {
    for (const c of vectors.decode) {
      expect(decodeCrosshair(c.code), c.code).toEqual({ ok: true, spec: c.spec });
    }
  });

  it('refuses every code the reference refuses, for the same reason', () => {
    for (const c of vectors.rejects) {
      expect(decodeCrosshair(c.code), c.code).toEqual({ ok: false, reason: c.reason });
    }
  });

  it('draws the same shapes', () => {
    for (const c of vectors.rects) {
      const got = crosshairRects(c.spec, c.coneGap, c.unit);
      expect(got.length).toBe(c.shapes.length);
      got.forEach((g, i) => {
        const want = c.shapes[i];
        expect(Object.keys(g).sort()).toEqual(Object.keys(want).sort());
        for (const [k, v] of Object.entries(want)) {
          if (typeof v === 'number') expect((g as any)[k]).toBeCloseTo(v, 9);
          else expect((g as any)[k]).toBe(v);
        }
      });
    }
  });

  it('reads and writes the settings bag the same way', () => {
    for (const c of vectors.fromSettings) expect(specFromSettings(c.bag)).toEqual(c.spec);
    for (const c of vectors.toSettings) expect(settingsFromSpec(c.spec)).toEqual(c.rows);
  });
});
