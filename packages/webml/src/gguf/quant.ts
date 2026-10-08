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
 *   Q5_0  22 bytes / 32 values: f16 d, u32 qh, u8 qs[16]    as Q4_0 with a fifth bit, bit j
 *         of qh for value j (bit j + 16 for value j + 16):  x = d * (q - 16)
 *         llama.cpp's K-quant files use it where a row is not a multiple of 256 (SmolLM2)
 *
 * K-quants, 256 values per super-block (ggml-quants.c, dequantize_row_q*_K):
 *   Q4_K  144 bytes: f16 d, f16 dmin, u8 scales[12], u8 qs[128]
 *         eight sub-blocks of 32, each x = d·sc·q − dmin·m with 6-bit sc, m
 *   Q5_K  176 bytes: f16 d, f16 dmin, u8 scales[12], u8 qh[32], u8 qs[128]
 *         as Q4_K with a fifth bit per value from qh
 *   Q6_K  210 bytes: u8 ql[128], u8 qh[64], i8 scales[16], f16 d
 *         sixteen sub-blocks of 16, x = d·sc·(q − 32) with 6-bit q
 */
import { GGML_TYPES } from './ggml';

export const F32 = 0;
export const F16 = 1;
export const Q4_0 = 2;
export const Q5_0 = 6;
export const Q8_0 = 8;
export const Q4_K = 12;
export const Q5_K = 13;
export const Q6_K = 14;

/** The types `dequantize` handles, which is also what the kernels handle. */
export const SUPPORTED_TYPES: ReadonlySet<number> = new Set([
  F32,
  F16,
  Q4_0,
  Q5_0,
  Q8_0,
  Q4_K,
  Q5_K,
  Q6_K,
]);

/**
 * The 6-bit scale and min of sub-block `j` (0–7) of a Q4_K/Q5_K super-block,
 * packed into 12 bytes at `at` (ggml's get_scale_min_k4).
 */
export function scaleMinK4(bytes: Uint8Array, at: number, j: number): [sc: number, m: number] {
  if (j < 4) return [bytes[at + j] & 63, bytes[at + j + 4] & 63];
  return [
    (bytes[at + j + 4] & 0xf) | ((bytes[at + j - 4] >> 6) << 4),
    (bytes[at + j + 4] >> 4) | ((bytes[at + j] >> 6) << 4),
  ];
}

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
    case Q5_0:
      for (let b = 0; b < count / 32; b++) {
        const at = byteOffset + b * 22;
        const d = f16ToF32(view.getUint16(at, true));
        const qh = view.getUint32(at + 2, true);
        for (let j = 0; j < 16; j++) {
          const q = bytes[at + 6 + j];
          out[b * 32 + j] = d * (((q & 15) | (((qh >>> j) & 1) << 4)) - 16);
          out[b * 32 + j + 16] = d * (((q >> 4) | (((qh >>> (j + 16)) & 1) << 4)) - 16);
        }
      }
      break;
    case Q4_K:
    case Q5_K:
      for (let b = 0; b < count / 256; b++) {
        const five = type === Q5_K;
        const at = byteOffset + b * (five ? 176 : 144);
        const d = f16ToF32(view.getUint16(at, true));
        const dmin = f16ToF32(view.getUint16(at + 2, true));
        const qh = at + 16;
        const qs = at + (five ? 48 : 16);
        for (let s = 0; s < 8; s++) {
          const [sc, m] = scaleMinK4(bytes, at + 4, s);
          const q = qs + 32 * (s >> 1);
          for (let l = 0; l < 32; l++) {
            let v = s & 1 ? bytes[q + l] >> 4 : bytes[q + l] & 0xf;
            if (five && (bytes[qh + l] >> s) & 1) v += 16;
            out[b * 256 + s * 32 + l] = d * sc * v - dmin * m;
          }
        }
      }
      break;
    case Q6_K:
      for (let b = 0; b < count / 256; b++) {
        const at = byteOffset + b * 210;
        const d = f16ToF32(view.getUint16(at + 208, true));
        for (let n = 0; n < 2; n++) {
          const ql = at + 64 * n;
          const qh = at + 128 + 32 * n;
          const sc = at + 192 + 8 * n;
          const y = b * 256 + 128 * n;
          for (let l = 0; l < 32; l++) {
            const is = l >> 4;
            const h = bytes[qh + l];
            const q1 = ((bytes[ql + l] & 0xf) | ((h & 3) << 4)) - 32;
            const q2 = ((bytes[ql + l + 32] & 0xf) | (((h >> 2) & 3) << 4)) - 32;
            const q3 = ((bytes[ql + l] >> 4) | (((h >> 4) & 3) << 4)) - 32;
            const q4 = ((bytes[ql + l + 32] >> 4) | (((h >> 6) & 3) << 4)) - 32;
            out[y + l] = d * view.getInt8(sc + is) * q1;
            out[y + l + 32] = d * view.getInt8(sc + is + 2) * q2;
            out[y + l + 64] = d * view.getInt8(sc + is + 4) * q3;
            out[y + l + 96] = d * view.getInt8(sc + is + 6) * q4;
          }
        }
      }
      break;
  }
  return out;
}
