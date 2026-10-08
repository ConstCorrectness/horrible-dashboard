/**
 * ggml tensor types → (name, elements per block, bytes per block).
 *
 * The same table as `backend/modules/interpretability/gguf.py`'s `_GGML_TYPES`; the
 * two readers must agree on what a file holds. The pair is what turns a shape into
 * a byte count — a quantized tensor's footprint is not derivable from its shape
 * alone. Unknown ids get a null size rather than a guess: a wrong byte count would
 * upload the wrong bytes as a tensor, which fails as garbage output, not an error.
 */

export interface GgmlType {
  name: string;
  blockElems: number;
  blockBytes: number;
}

export const GGML_TYPES: Readonly<Record<number, GgmlType>> = {
  0: { name: 'F32', blockElems: 1, blockBytes: 4 },
  1: { name: 'F16', blockElems: 1, blockBytes: 2 },
  2: { name: 'Q4_0', blockElems: 32, blockBytes: 18 },
  3: { name: 'Q4_1', blockElems: 32, blockBytes: 20 },
  6: { name: 'Q5_0', blockElems: 32, blockBytes: 22 },
  7: { name: 'Q5_1', blockElems: 32, blockBytes: 24 },
  8: { name: 'Q8_0', blockElems: 32, blockBytes: 34 },
  9: { name: 'Q8_1', blockElems: 32, blockBytes: 36 },
  10: { name: 'Q2_K', blockElems: 256, blockBytes: 84 },
  11: { name: 'Q3_K', blockElems: 256, blockBytes: 110 },
  12: { name: 'Q4_K', blockElems: 256, blockBytes: 144 },
  13: { name: 'Q5_K', blockElems: 256, blockBytes: 176 },
  14: { name: 'Q6_K', blockElems: 256, blockBytes: 210 },
  15: { name: 'Q8_K', blockElems: 256, blockBytes: 292 },
  16: { name: 'IQ2_XXS', blockElems: 256, blockBytes: 66 },
  17: { name: 'IQ2_XS', blockElems: 256, blockBytes: 74 },
  18: { name: 'IQ3_XXS', blockElems: 256, blockBytes: 98 },
  19: { name: 'IQ1_S', blockElems: 256, blockBytes: 50 },
  20: { name: 'IQ4_NL', blockElems: 32, blockBytes: 18 },
  21: { name: 'IQ3_S', blockElems: 256, blockBytes: 110 },
  22: { name: 'IQ2_S', blockElems: 256, blockBytes: 82 },
  23: { name: 'IQ4_XS', blockElems: 256, blockBytes: 136 },
  24: { name: 'I8', blockElems: 1, blockBytes: 1 },
  25: { name: 'I16', blockElems: 1, blockBytes: 2 },
  26: { name: 'I32', blockElems: 1, blockBytes: 4 },
  27: { name: 'I64', blockElems: 1, blockBytes: 8 },
  28: { name: 'F64', blockElems: 1, blockBytes: 8 },
  29: { name: 'IQ1_M', blockElems: 256, blockBytes: 56 },
  30: { name: 'BF16', blockElems: 1, blockBytes: 2 },
  34: { name: 'TQ1_0', blockElems: 256, blockBytes: 54 },
  35: { name: 'TQ2_0', blockElems: 256, blockBytes: 66 },
  39: { name: 'MXFP4', blockElems: 32, blockBytes: 17 },
};

export function ggmlTypeName(typeId: number): string {
  return GGML_TYPES[typeId]?.name ?? `type:${typeId}`;
}

/**
 * Bytes a tensor of `elements` occupies, or null when the type is unknown or the
 * count is not a whole number of blocks (a misread type or shape — report nothing
 * rather than a rounded fiction).
 */
export function tensorBytes(typeId: number, elements: number): number | null {
  const t = GGML_TYPES[typeId];
  if (!t || elements % t.blockElems) return null;
  return (elements / t.blockElems) * t.blockBytes;
}
