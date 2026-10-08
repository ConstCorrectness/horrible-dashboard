import { describe, expect, it } from 'vitest';

import { GgufError, layoutProblems, readGgufHeader, type ByteSource } from '../parse';
import { bytesSource } from '../source';
import { Bytes, gguf, preamble } from './write';

const STRING = 8;
const ARRAY = 9;
const UINT32 = 4;
const UINT64 = 10;
const FLOAT32 = 6;

/** A source that counts its reads. */
function counting(bytes: Uint8Array): ByteSource & { reads: [number, number][] } {
  const reads: [number, number][] = [];
  return {
    size: bytes.length,
    reads,
    read: async (offset, length) => {
      reads.push([offset, length]);
      return bytes.slice(offset, offset + length);
    },
  };
}

describe('readGgufHeader', () => {
  it('reads metadata, tensors and the aligned data offset', async () => {
    const file = gguf({
      kvs: 3,
      kv: (b) => {
        b.str('general.architecture').u32(STRING).str('llama');
        b.str('llama.block_count').u32(UINT32).u32(2);
        b.str('general.alignment').u32(UINT32).u32(64);
      },
      tensors: [
        { name: 'a', shape: [32, 2], type: 8, offset: 0 }, // Q8_0: 2 blocks = 68 bytes
        { name: 'b', shape: [4], type: 0, offset: 128 }, // F32: 16 bytes
      ],
      alignment: 64,
      dataBytes: 144,
    });
    const h = await readGgufHeader(bytesSource(file));
    expect(h.version).toBe(3);
    expect(h.alignment).toBe(64);
    expect(h.dataOffset % 64).toBe(0);
    expect(h.dataOffset + 144).toBe(file.length);
    expect(h.metadata['general.architecture']).toBe('llama');
    expect(h.metadata['llama.block_count']).toBe(2);
    expect(h.tensors.map((t) => [t.name, t.typeName, t.elements, t.bytes])).toEqual([
      ['a', 'Q8_0', 64, 68],
      ['b', 'F32', 4, 16],
    ]);
    expect(layoutProblems(h)).toEqual([]);
  });

  it('keeps numeric arrays typed, and u64 past 2^53 exact', async () => {
    const file = gguf({
      kvs: 3,
      kv: (b) => {
        b.str('scores').u32(ARRAY).u32(FLOAT32).u64(3).f32(0).f32(-1.5).f32(2.25);
        b.str('ids').u32(ARRAY).u32(UINT32).u64(2).u32(7).u32(9);
        b.str('big')
          .u32(UINT64)
          .u64(2n ** 60n + 1n);
      },
    });
    const h = await readGgufHeader(bytesSource(file));
    expect(h.metadata.scores).toBeInstanceOf(Float32Array);
    expect([...(h.metadata.scores as Float32Array)]).toEqual([0, -1.5, 2.25]);
    expect(h.metadata.ids).toBeInstanceOf(Uint32Array);
    expect(h.metadata.big).toBe(2n ** 60n + 1n);
  });

  it('replaces invalid UTF-8 instead of losing the directory after it', async () => {
    const file = gguf({
      kvs: 1,
      kv: (b) =>
        b
          .str('tok')
          .u32(STRING)
          .str(new Uint8Array([0x61, 0xff, 0x62])),
      tensors: [{ name: 'after', shape: [1], type: 0, offset: 0 }],
      dataBytes: 4,
    });
    const h = await readGgufHeader(bytesSource(file));
    expect(h.metadata.tok).toBe('a�b');
    expect(h.tensors[0].name).toBe('after');
  });

  it('keeps a leading byte-order mark (Gemma has both `\ufeff#` and `#`)', async () => {
    const file = gguf({
      kvs: 1,
      kv: (b) =>
        b
          .str('tok')
          .u32(STRING)
          .str(new Uint8Array([0xef, 0xbb, 0xbf, 0x23])),
      tensors: [],
      dataBytes: 0,
    });
    const h = await readGgufHeader(bytesSource(file));
    expect(h.metadata.tok).toBe('\ufeff#');
  });

  it('reads a header larger than its first fetch, in growing chunks', async () => {
    // ~3.4 MB of vocabulary: more than the 2 MiB first read.
    const n = 200_000;
    const file = gguf({
      kvs: 1,
      kv: (b) => {
        b.str('tokenizer.ggml.tokens').u32(ARRAY).u32(STRING).u64(n);
        for (let i = 0; i < n; i++) b.str(`token-${i}`);
      },
      tensors: [{ name: 'w', shape: [8], type: 0, offset: 0 }],
      dataBytes: 32,
    });
    const src = counting(file);
    const h = await readGgufHeader(src);
    const tokens = h.metadata['tokenizer.ggml.tokens'] as string[];
    expect(tokens).toHaveLength(n);
    expect(tokens[n - 1]).toBe(`token-${n - 1}`);
    expect(h.tensors[0].name).toBe('w');
    expect(src.reads.length).toBeGreaterThan(1);
    expect(src.reads.length).toBeLessThan(5);
    // Never asks for the same byte twice.
    for (let i = 1; i < src.reads.length; i++) {
      const [prevOffset, prevLength] = src.reads[i - 1];
      expect(src.reads[i][0]).toBe(prevOffset + prevLength);
    }
  });

  it('refuses what is not a GGUF it can read', async () => {
    const parse = (bytes: Uint8Array) => readGgufHeader(bytesSource(bytes));
    await expect(parse(new TextEncoder().encode('GGML0000'))).rejects.toThrow(/Not a GGUF/);
    await expect(parse(preamble(0, 0, 1).done())).rejects.toThrow(/version 1/);
    // A big-endian file's version reads as 0x03000000.
    await expect(parse(preamble(0, 0, 0x03000000).done())).rejects.toThrow(/version/);
    await expect(parse(preamble(1_000_000, 0).done())).rejects.toThrow(/Implausible header/);
    await expect(parse(preamble(0, 1).str('k').u32(STRING).done())).rejects.toThrow(/Truncated/);
    await expect(parse(preamble(0, 1).str('k').u32(77).done())).rejects.toThrow(
      /Unknown metadata value type 77/,
    );
    await expect(
      parse(
        preamble(0, 1)
          .str('k')
          .u32(STRING)
          .u64(2n ** 40n)
          .done(),
      ),
    ).rejects.toThrow(/Implausible string length/);
    await expect(parse(new Bytes().raw([1, 2]).done())).rejects.toBeInstanceOf(GgufError);
  });
});

describe('layoutProblems', () => {
  const header = async (
    tensors: { offset: number; type?: number; elems?: number }[],
    data: number,
  ) =>
    readGgufHeader(
      bytesSource(
        gguf({
          tensors: tensors.map((t, i) => ({
            name: `t${i}`,
            shape: [t.elems ?? 8],
            type: t.type ?? 0,
            offset: t.offset,
          })),
          dataBytes: data,
        }),
      ),
    );

  it('flags overlap, misalignment, unknown types and a truncated file', async () => {
    expect(layoutProblems(await header([{ offset: 0 }, { offset: 32 }], 64))).toEqual([]);
    expect(layoutProblems(await header([{ offset: 0, elems: 16 }, { offset: 32 }], 64))).toEqual([
      't1: overlaps t0',
    ]);
    expect(layoutProblems(await header([{ offset: 4 }], 64))).toEqual([
      't0: offset 4 is not 32-aligned',
    ]);
    expect(layoutProblems(await header([{ offset: 0, type: 99 }], 64))).toEqual([
      't0: no size for type type:99 with 8 elements',
    ]);
    const truncated = layoutProblems(await header([{ offset: 0 }, { offset: 32 }], 40));
    expect(truncated).toHaveLength(1);
    expect(truncated[0]).toMatch(/past the end of the file/);
  });
});
