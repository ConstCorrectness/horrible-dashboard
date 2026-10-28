/**
 * The Heads section's pure parts: ranking, the measurability rule, and the line
 * the suite digest carries. The rule with teeth is measurability — a trace that
 * repeats nothing must never present an induction ranking, because every head
 * would rank by a score of null and the list would read as "no induction heads".
 */
import { describe, expect, it } from 'vitest';

import type { HeadScore, HeadTable, ModelEntry } from '../../llamacpp/api';
import {
  defaultProbeModel,
  headsDigest,
  measurable,
  rankHeads,
  type HeadsState,
} from '../heads/state';

const head = (layer: number, h: number, over: Partial<HeadScore> = {}): HeadScore => ({
  layer,
  head: h,
  entropy: 1,
  selfWeight: 0.1,
  sink: 0.3,
  prevToken: 0.05,
  induction: 0.01,
  duplicate: 0.01,
  rows: 54,
  ...over,
});

const probeTable: HeadTable = {
  traceId: 'probe-1',
  model: 'Llama-3.2-1B',
  tokens: 54,
  repeatedPositions: 25,
  layers: 16,
  headsPerLayer: 32,
  heads: [
    head(5, 10, { induction: 0.82 }),
    head(10, 23, { induction: 0.89 }),
    head(0, 2, { prevToken: 0.64 }),
  ],
};

describe('rankHeads', () => {
  it('ranks highest first and keeps only the top n', () => {
    expect(
      rankHeads(probeTable.heads!, 'induction', 2).map((h) => `L${h.layer}H${h.head}`),
    ).toEqual(['L10H23', 'L5H10']);
  });

  it('leaves out heads whose score is not measurable', () => {
    const heads = [head(0, 0, { induction: null }), head(1, 0, { induction: 0.4 })];
    expect(rankHeads(heads, 'induction')).toHaveLength(1);
  });
});

describe('measurable', () => {
  it('needs a repeated position for induction and duplicate, not for the rest', () => {
    const plain: HeadTable = { ...probeTable, repeatedPositions: 0 };
    expect(measurable(plain, 'induction')).toBe(false);
    expect(measurable(plain, 'duplicate')).toBe(false);
    expect(measurable(plain, 'prevToken')).toBe(true);
    expect(measurable(probeTable, 'induction')).toBe(true);
  });
});

describe('headsDigest', () => {
  const state = (table: HeadTable | null) => ({ table }) as HeadsState;

  it('names the strongest induction head on a probe', () => {
    expect(headsDigest(state(probeTable))).toBe(
      'Llama-3.2-1B probe-1 (54 tokens): top induction L10H23 0.89',
    );
  });

  it('falls back to previous-token, and says why, on a prompt with no repeats', () => {
    const plain = { ...probeTable, repeatedPositions: 0 };
    expect(headsDigest(state(plain))).toBe(
      'Llama-3.2-1B probe-1 (54 tokens, no repeats: induction not measurable): top previous token L0H2 0.64',
    );
  });

  it('says nothing without a scored trace', () => {
    expect(headsDigest(state(null))).toBeNull();
  });
});

describe('defaultProbeModel', () => {
  // The real catalog's head: the smallest files are not language models at all.
  const entry = (path: string, architecture: string, parameters: number, sizeBytes: number) =>
    ({ path, architecture, parameters, sizeBytes }) as ModelEntry;
  const catalog = [
    entry('D:/m/whisper-base.gguf', '', 72e6, 79e6),
    entry('D:/m/nomic-embed.gguf', 'nomic-bert', 137e6, 80e6),
    entry('D:/m/qwen3-tokenizer-stub.gguf', 'qwen3', 10e6, 19e6),
    entry('D:/m/magpie-tts.gguf', 'magpietts', 224e6, 428e6),
    entry('D:/m/Qwen3-0.6B-Q8_0.gguf', 'qwen3', 751e6, 610e6),
    entry('D:/m/Llama-3.2-1B.gguf', 'llama', 1.2e9, 1.3e9),
  ];

  it("prefers the trace's own model, whatever the spelling of its path", () => {
    expect(defaultProbeModel(catalog, 'd:\\m\\Llama-3.2-1B.gguf')).toBe('D:/m/Llama-3.2-1B.gguf');
  });

  it('ranks the trace above the layout even when the catalog lists the layout first', () => {
    // Found live: a one-pass `find(trace || layout)` returned the layout's Gemma,
    // which sat earlier in the catalog than the trace's Llama.
    expect(defaultProbeModel(catalog, 'D:/m/Llama-3.2-1B.gguf', 'D:/m/Qwen3-0.6B-Q8_0.gguf')).toBe(
      'D:/m/Llama-3.2-1B.gguf',
    );
  });

  it('then the model the attention layout describes', () => {
    expect(defaultProbeModel(catalog, undefined, 'D:/m/Llama-3.2-1B.gguf')).toBe(
      'D:/m/Llama-3.2-1B.gguf',
    );
  });

  it('otherwise the smallest real text decoder, not a speech, embedding or stub file', () => {
    expect(defaultProbeModel(catalog)).toBe('D:/m/Qwen3-0.6B-Q8_0.gguf');
  });
});
