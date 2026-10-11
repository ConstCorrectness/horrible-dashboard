/**
 * The flashbang curve, replayed from the fixture the server generated.
 *
 * `flash-vectors.json` is written by `scripts/gen_hassault_flash_vectors.py` from
 * `grenades.flash_alpha` and `MatchPlayer.step_flash`, and replayed by the backend
 * suite and the native client's `tests/conformance.rs` too. If this fails after a
 * change to the curve, regenerate the fixture and change all three ports.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import {
  FLASH_BLIND,
  FlashState,
  flashAlpha,
  flashFieldsFrom,
  stepFlash,
  type FlashFields,
} from '../flashbang';

interface Vectors {
  tolerance: number;
  blind: number;
  alpha: { peak: number; hold: number; fade: number; fadeTotal: number; alpha: number }[];
  steps: {
    peak: number;
    hold: number;
    fade: number;
    dt: number;
    series: { hold: number; fade: number; alpha: number }[];
  }[];
}

const vectors = JSON.parse(
  readFileSync(new URL('./flash-vectors.json', import.meta.url), 'utf-8'),
) as Vectors;

describe('flashbang curve', () => {
  it('agrees with the server on the blind threshold', () => {
    expect(FLASH_BLIND).toBe(vectors.blind);
  });

  it('draws the same white as the server for every case', () => {
    for (const c of vectors.alpha) {
      expect(flashAlpha(c.peak, c.hold, c.fade, c.fadeTotal)).toBeCloseTo(c.alpha, 9);
    }
  });

  it('counts the hold then the fade down exactly as the server does', () => {
    for (const series of vectors.steps) {
      let state: FlashFields = {
        peak: series.peak,
        hold: series.hold,
        fade: series.fade,
        fadeTotal: series.fade,
      };
      for (const want of series.series) {
        state = stepFlash(state, series.dt);
        expect(state.hold).toBeCloseTo(want.hold, 9);
        expect(state.fade).toBeCloseTo(want.fade, 9);
        expect(flashAlpha(state.peak, state.hold, state.fade, state.fadeTotal)).toBeCloseTo(
          want.alpha,
          9,
        );
      }
    }
  });
});

describe('FlashState', () => {
  it('holds white, then fades smoothly between snapshots', () => {
    const state = new FlashState();
    state.seed({ peak: 1, hold: 0.5, fade: 1, fadeTotal: 1 });
    state.step(0.25);
    expect(state.alpha).toBe(1);
    expect(state.remaining).toBe(1);
    state.step(0.5);
    expect(state.alpha).toBeGreaterThan(0);
    expect(state.alpha).toBeLessThan(1);
    state.step(2);
    expect(state.alpha).toBe(0);
  });

  it('takes an older server that sends only `flash` as a plain fade', () => {
    const fields = flashFieldsFrom({ flash: 0.5 });
    expect(fields.hold).toBe(0);
    expect(flashAlpha(fields.peak, fields.hold, fields.fade, fields.fadeTotal)).toBeCloseTo(0.5);
    expect(flashFieldsFrom({}).peak).toBe(0);
  });
});
