/**
 * Cross-reader conformance: a GGUF written by llama.cpp's own writer, parsed here
 * and by the backend's `gguf.py`, must read the same. Both the file and gguf.py's
 * reading of it come from scripts/gen_webml_gguf_fixture.py.
 */
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { layoutProblems, readGgufHeader, tensorDataBytes, type GgufValue } from '../parse';
import { bytesSource } from '../source';

const dir = new URL('./fixtures/', import.meta.url);
const file = new Uint8Array(readFileSync(new URL('tiny-qwen3.gguf', dir)));
const expected = JSON.parse(readFileSync(new URL('tiny-qwen3.expected.json', dir), 'utf-8'));

/** The JSON shape the Python dump uses: arrays as lists, big ints as {bigint}. */
function plain(value: GgufValue): unknown {
  if (typeof value === 'bigint') return { bigint: String(value) };
  if (ArrayBuffer.isView(value)) return Array.from(value as ArrayLike<number>);
  if (Array.isArray(value)) return value.map(plain);
  return value;
}

describe('the llama.cpp-written fixture', () => {
  it('reads exactly as gguf.py reads it', async () => {
    const h = await readGgufHeader(bytesSource(file));
    expect({
      version: h.version,
      alignment: h.alignment,
      dataOffset: h.dataOffset,
      fileSize: h.fileSize,
      metadata: Object.fromEntries(Object.entries(h.metadata).map(([k, v]) => [k, plain(v)])),
      tensors: h.tensors,
    }).toEqual(expected);
  });

  it('has a sound layout whose tensors fill the data section', async () => {
    const h = await readGgufHeader(bytesSource(file));
    expect(layoutProblems(h)).toEqual([]);
    const last = h.tensors.reduce((a, b) => (b.offset > a.offset ? b : a));
    const end = h.dataOffset + last.offset + (last.bytes ?? 0);
    // The writer pads the last tensor to the alignment too.
    expect(file.length - end).toBeGreaterThanOrEqual(0);
    expect(file.length - end).toBeLessThan(h.alignment);
    expect(tensorDataBytes(h)).toBeLessThanOrEqual(file.length - h.dataOffset);
  });
});
