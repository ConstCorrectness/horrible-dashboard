/**
 * The suite digest: what the agent is told the suite is looking at.
 *
 * The rules with teeth are the honesty ones — a model that did not resolve says
 * so instead of vanishing, a layer known only by family rule carries that caveat,
 * a turn whose tools were dropped says DROPPED — because a digest that reads
 * confidently while being wrong is the failure the whole suite exists to prevent.
 */
import { describe, expect, it } from 'vitest';

import type {
  AttentionLayout,
  LayerAttention,
  ModelArchitecture,
  TurnSnapshot,
} from '../../interpretability/store';
import { buildDigest, describeLayer, type DigestInputs } from '../suite-state';

const layer = (n: number, over: Partial<LayerAttention> = {}): LayerAttention => ({
  layer: n,
  kind: (n + 1) % 6 === 0 ? 'global' : 'sliding',
  basis: 'metadata',
  window: (n + 1) % 6 === 0 ? null : 1024,
  heads: 16,
  kvHeads: 8,
  headDimK: 256,
  headDimV: 256,
  ropeBase: 10000,
  ropeDims: 256,
  nope: false,
  kvSourceLayer: null,
  kEqV: false,
  outputGate: false,
  sinks: false,
  mtp: false,
  kvBytesPerToken: 8192,
  reach: null,
  ...over,
});

const layout = (over: Partial<AttentionLayout> = {}): AttentionLayout => ({
  source: 'gguf',
  path: 'D:\\models\\gemma-4-12B-it-QAT-Q4_0.gguf',
  family: 'gemma4',
  layers: Array.from({ length: 12 }, (_, i) => layer(i)),
  counts: { sliding: 10, global: 2 },
  pattern: '5 sliding : 1 global, ×2',
  kvCache: [
    { context: 8192, bytes: 100 * 1024 ** 2, bytesIfGlobal: 400 * 1024 ** 2 },
    { context: 32768, bytes: 200 * 1024 ** 2, bytesIfGlobal: 1600 * 1024 ** 2 },
    { context: 131072, bytes: 500 * 1024 ** 2, bytesIfGlobal: 6400 * 1024 ** 2 },
  ],
  conflicts: [],
  notes: ['Layers 5, 11 have no V projection: the value is the key (K=V).'],
  error: null,
  ...over,
});

const arch = {
  source: 'gguf',
  sourceDetail: 'x.gguf',
  model: 'google/gemma-4-12b-qat',
  family: 'gemma4',
  layers: 12,
  hiddenSize: 3840,
  vocabSize: 262144,
  attention: { heads: 16, kvHeads: 8 },
  moe: null,
  error: null,
} as unknown as ModelArchitecture;

const inputs = (over: Partial<DigestInputs> = {}): DigestInputs => ({
  arch,
  attention: layout(),
  locus: {},
  turns: [],
  lensRun: null,
  ...over,
});

describe('buildDigest', () => {
  it('names the model, where its description came from, and its shape', () => {
    const d = buildDigest(inputs());
    expect(d.model).toBe('google/gemma-4-12b-qat (gemma4) — from GGUF header');
    expect(d.shape).toContain('12 blocks');
    expect(d.attention).toBe('5 sliding : 1 global, ×2 (gemma-4-12B-it-QAT-Q4_0.gguf)');
    expect(d.kvCache).toBe('200 MB fp16 KV at 32k (8.0× smaller than all-global)');
  });

  it('says a repo-described model is the repo, not the weights', () => {
    const d = buildDigest(
      inputs({ arch: { ...arch, source: 'huggingface', sourceDetail: 'google/gemma-4' } }),
    );
    expect(d.model).toContain('describes the repo');
  });

  it('reports an unresolved model instead of dropping it', () => {
    const d = buildDigest(
      inputs({ arch: { error: 'No agent provider configured' } as ModelArchitecture }),
    );
    expect(d.model).toBe('unresolved: No agent provider configured');
  });

  it('describes the layer the locus points at', () => {
    const d = buildDigest(inputs({ locus: { layer: 5, position: 3, traceId: 't1' } }));
    expect(d.locus).toBe('layer 5, position 3, trace t1');
    expect(d.atLocus).toBe('L5 global, 16q/8kv × 256, (metadata)');
  });

  it('carries a caveat when a kind is only known by family rule, or sources conflict', () => {
    const ruled = layout({
      layers: [layer(0, { basis: 'rule' })],
      conflicts: ['Layers 3: disagree'],
    });
    const d = buildDigest(inputs({ attention: ruled }));
    expect(d.caveats).toEqual([
      'Layers 3: disagree',
      'Some layer kinds come from the family rule, not per-layer metadata.',
    ]);
  });

  it('flags a turn whose tools were dropped by the budget', () => {
    const turn = {
      turnId: 't',
      agentId: 'main',
      agentName: 'Orchestrator',
      model: 'gemma',
      exact: true,
      rounds: [{ totalTokens: 4812, toolsTruncated: true }],
    } as unknown as TurnSnapshot;
    expect(buildDigest(inputs({ turns: [turn] })).lastTurn).toBe(
      'Orchestrator on gemma: 1 round, 4812 tokens, tools DROPPED by budget',
    );
  });

  it('keeps the per-layer rows out of the compact digest and in the full one', () => {
    expect(buildDigest(inputs()).layers).toBeUndefined();
    const full = buildDigest(inputs(), 'full');
    expect(full.layers).toHaveLength(12);
    expect(full.notes).toHaveLength(1);
  });

  it('stays inside the per-pane workspace-context budget', () => {
    const busy = inputs({
      locus: { layer: 5, position: 3, stage: 'attention', traceId: 'trace-0123456789' },
      attention: layout({ conflicts: ['a'.repeat(200), 'b'.repeat(200), 'c'.repeat(200)] }),
    });
    expect(JSON.stringify({ view: 'Attention', ...buildDigest(busy) }).length).toBeLessThan(1500);
  });
});

describe('describeLayer', () => {
  it('reads as a sentence with its basis', () => {
    expect(describeLayer(layer(16, { kvSourceLayer: 13, kEqV: true }))).toBe(
      'L16 sliding, window 1024, 16q/8kv × 256, KV from L13, K=V, (metadata)',
    );
  });
});
