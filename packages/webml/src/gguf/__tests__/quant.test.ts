import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { dequantize, f16ToF32 } from '../quant';

/** Blocks quantized and dequantized by the `gguf` package (scripts/gen_webml_gguf_fixture.py). */
const golden: Record<string, { type: number; hex: string; values: number[] }> = JSON.parse(
  readFileSync(new URL('./fixtures/quant-golden.json', import.meta.url), 'utf-8'),
);

function bytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(2 * i, 2 * i + 2), 16);
  return out;
}

describe('dequantize', () => {
  for (const [name, g] of Object.entries(golden)) {
    it(`${name} matches gguf's dequantizer exactly`, () => {
      const got = dequantize(g.type, bytes(g.hex), 0, g.values.length);
      expect(Array.from(got)).toEqual(g.values.map(Math.fround));
    });
  }

  it('reads from an offset, and from a view into a larger buffer', () => {
    const g = golden.Q8_0;
    const raw = bytes(g.hex);
    const padded = new Uint8Array(raw.length + 10);
    padded.set(raw, 7);
    const view = padded.subarray(3);
    // Skip the first block (34 bytes): values 32.. of the golden row.
    expect(Array.from(dequantize(g.type, view, 4 + 34, 32))).toEqual(
      g.values.slice(32, 64).map(Math.fround),
    );
  });

  it('refuses unsupported types and partial blocks', () => {
    expect(() => dequantize(10, new Uint8Array(84), 0, 256)).toThrow(/no dequantizer for Q2_K/);
    expect(() => dequantize(8, new Uint8Array(34), 0, 16)).toThrow(/whole number of Q8_0 blocks/);
  });
});

describe('f16ToF32', () => {
  it('decodes normals, subnormals, zeros and specials', () => {
    expect(f16ToF32(0x3c00)).toBe(1);
    expect(f16ToF32(0xc000)).toBe(-2);
    expect(f16ToF32(0x7bff)).toBe(65504);
    expect(f16ToF32(0x0001)).toBe(2 ** -24);
    expect(Object.is(f16ToF32(0x8000), -0)).toBe(true);
    expect(f16ToF32(0x7c00)).toBe(Infinity);
    expect(f16ToF32(0x7e00)).toBeNaN();
  });
});
