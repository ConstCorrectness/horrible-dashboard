/**
 * CPU reference dequantizers for the ggml types the engine runs. They are the test
 * oracle for the WGSL kernels, which dequantize the same block layouts on the GPU,
 * and the loader's path for small tensors (norm weights) that are uploaded as f32.
 * Never the runtime path for the large matrices.
 *
 * Block layouts (ggml-common.h):
 *   Q8_0  34 bytes / 32 values: f16 d, i8 q[32]            x = d * q
 *   Q4_0  18 bytes / 32 values: f16 d, u8 qs[16]            x[j]    = d * ((qs[j] & 15) - 8)
 *                                                           x[j+16] = d * ((qs[j] >> 4) - 8)
 */
import { GGML_TYPES } from './ggml';

export const F32 = 0;
export const F16 = 1;
export const Q4_0 = 2;
export const Q8_0 = 8;

/** The types `dequantize` handles, which is also what 6.1's kernels handle. */
export const SUPPORTED_TYPES: ReadonlySet<number> = new Set([F32, F16, Q4_0, Q8_0]);

/** IEEE half → number, exactly (subnormals, infinities, NaN). */
export function f16ToF32(h: number): number {
  const sign = h & 0x8000 ? -1 : 1;
  const exp = (h >> 10) & 0x1f;
  const frac = h & 0x3ff;
  if (exp === 0) return sign * frac * 2 ** -24;
  if (exp === 31) return frac ? NaN : sign * Infinity;
  return sign * (1 + frac / 1024) * 2 ** (exp - 15);
}

/**
 * `count` values of `type` starting at `byteOffset` in `bytes`, as f32. `count` must be
 * a whole number of the type's blocks.
 */
export function dequantize(
  type: number,
  bytes: Uint8Array,
  byteOffset: number,
  count: number,
): Float32Array<ArrayBuffer> {
  const info = GGML_TYPES[type];
  if (!info || !SUPPORTED_TYPES.has(type)) {
    throw new Error(`no dequantizer for ${info?.name ?? `type ${type}`}`);
  }
  if (count % info.blockElems) {
    throw new Error(`${count} values is not a whole number of ${info.name} blocks`);
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const out = new Float32Array(count);
  switch (type) {
    case F32:
      for (let i = 0; i < count; i++) out[i] = view.getFloat32(byteOffset + 4 * i, true);
      break;
    case F16:
      for (let i = 0; i < count; i++) out[i] = f16ToF32(view.getUint16(byteOffset + 2 * i, true));
      break;
    case Q8_0:
      for (let b = 0; b < count / 32; b++) {
        const at = byteOffset + b * 34;
        const d = f16ToF32(view.getUint16(at, true));
        for (let j = 0; j < 32; j++) out[b * 32 + j] = d * view.getInt8(at + 2 + j);
      }
      break;
    case Q4_0:
      for (let b = 0; b < count / 32; b++) {
        const at = byteOffset + b * 18;
        const d = f16ToF32(view.getUint16(at, true));
        for (let j = 0; j < 16; j++) {
          const q = bytes[at + 2 + j];
          out[b * 32 + j] = d * ((q & 15) - 8);
          out[b * 32 + j + 16] = d * ((q >> 4) - 8);
        }
      }
      break;
  }
  return out;
}
