/**
 * Splitting a tensor that is too large to bind as one storage buffer.
 *
 * A tensor is split along rows (ggml's `shape[0]` is the row length), so each chunk
 * is a whole number of rows and therefore of quant blocks, and a matvec over the
 * tensor becomes one dispatch per chunk writing its own slice of the output. Each
 * chunk is its own `GPUBuffer`, sized to a multiple of 4 bytes as WebGPU requires;
 * kernels address rows by byte offset, so the padding is never read.
 */
import type { GgufTensor } from '../gguf/parse';
import { tensorBytes } from '../gguf/ggml';

export interface RowChunk {
  firstRow: number;
  rows: number;
  /** Offset of the chunk's first byte within the tensor's data. */
  byteOffset: number;
  byteLength: number;
}

/** `n` rounded up to a multiple of 4: the size of the buffer holding `n` bytes. */
export function paddedSize(n: number): number {
  return Math.ceil(n / 4) * 4;
}

/**
 * Chunks of at most `maxBytes` (after padding) covering `rows` rows of `rowBytes`.
 * One chunk when everything fits. Throws when a single row does not fit.
 */
export function planRowChunks(rows: number, rowBytes: number, maxBytes: number): RowChunk[] {
  if (rows <= 0) return [];
  // Rows per chunk such that the padded size still fits: a multiple of 4 that is at
  // least `perChunk * rowBytes` and at most floor4(maxBytes).
  const usable = Math.floor(maxBytes / 4) * 4;
  const perChunk = Math.floor(usable / rowBytes);
  if (perChunk < 1) {
    throw new Error(`one row is ${rowBytes} bytes, more than a binding can hold (${maxBytes})`);
  }
  const chunks: RowChunk[] = [];
  for (let first = 0; first < rows; first += perChunk) {
    const n = Math.min(perChunk, rows - first);
    chunks.push({
      firstRow: first,
      rows: n,
      byteOffset: first * rowBytes,
      byteLength: n * rowBytes,
    });
  }
  return chunks;
}

/** `planRowChunks` for a GGUF tensor. Throws for a type with no known row size. */
export function tensorChunks(tensor: GgufTensor, maxBytes: number): RowChunk[] {
  const rowElems = tensor.shape[0] ?? 1;
  const rowBytes = tensorBytes(tensor.type, rowElems);
  if (rowBytes === null) {
    throw new Error(`${tensor.name}: no row size for ${tensor.typeName} rows of ${rowElems}`);
  }
  return planRowChunks(tensor.elements / rowElems, rowBytes, maxBytes);
}
