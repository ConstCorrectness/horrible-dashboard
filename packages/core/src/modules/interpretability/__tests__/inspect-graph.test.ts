import { describe, expect, it } from 'vitest';

import {
  buildInspectGraph,
  headGroups,
  inspectGraphKey,
  layerSignatures,
  nodeIdFor,
  selectionFor,
  type Selection,
} from '../inspect/graph';
import type { AttentionSpec, ModelArchitecture, ModelTensors, TensorEntry } from '../store';

function arch(over: Partial<ModelArchitecture> = {}): ModelArchitecture {
  return {
    source: 'ollama',
    sourceDetail: 'test',
    model: 'test-model',
    family: 'llama',
    parameterCount: 3e9,
    layers: 32,
    hiddenSize: 3072,
    vocabSize: 128256,
    contextLength: 8192,
    tiedEmbeddings: true,
    normType: 'rmsnorm',
    attention: {
      heads: 24,
      kvHeads: 8,
      headDim: 128,
      headDimDerived: false,
      kind: 'gqa',
      groupRatio: 3,
      slidingWindow: null,
      ropeTheta: 500000,
    },
    ffn: { intermediateSize: 8192, activation: 'silu', expansionRatio: 2.67, gated: true },
    moe: null,
    notes: [],
    error: '',
    ...over,
  } as ModelArchitecture;
}

function tensor(
  name: string,
  layer: number | null,
  shape: number[],
  component: string,
): TensorEntry {
  return {
    name,
    shape,
    dtype: 'Q4_K',
    elements: shape.reduce((a, b) => a * b, 1),
    byteSize: shape.reduce((a, b) => a * b, 1) / 2,
    layer,
    component,
  } as TensorEntry;
}

/** A uniform stack: every block carries the same roles at the same shapes. */
function uniformInventory(layers: number): ModelTensors {
  const tensors: TensorEntry[] = [];
  for (let i = 0; i < layers; i += 1) {
    tensors.push(tensor(`blk.${i}.attn_q.weight`, i, [3072, 3072], 'attention'));
    tensors.push(tensor(`blk.${i}.ffn_up.weight`, i, [3072, 8192], 'ffn'));
  }
  return {
    source: 'gguf',
    path: '/x.gguf',
    fileSize: 2e9,
    ggufVersion: 3,
    tensorCount: tensors.length,
    layerCount: layers,
    totalParameters: 3e9,
    totalBytes: 2e9,
    bytesComplete: true,
    quantTypes: { Q4_K: tensors.length },
    tensors,
    error: '',
  } as ModelTensors;
}

const AT_MODEL: Selection = { stage: 'model', layer: null };

describe('layerSignatures', () => {
  it('sees one shape group when every block is alike', () => {
    expect(layerSignatures(uniformInventory(8).tensors).bands).toBe(1);
  });

  /* Gemma 4 alternates its attention shape by layer. Every scalar source — GGUF
     metadata, a repo's config.json — reports ONE head count for the whole model,
     so the inventory is the only thing that can see this at all. */
  it('sees two groups when blocks alternate their attention shape', () => {
    const tensors: TensorEntry[] = [];
    for (let i = 0; i < 8; i += 1) {
      const wide = i % 2 === 1;
      tensors.push(tensor(`blk.${i}.attn_q.weight`, i, [3840, wide ? 8192 : 4096], 'attention'));
    }
    const out = layerSignatures(tensors);
    expect(out.bands).toBe(2);
    expect(out.bandOf[0]).not.toBe(out.bandOf[1]);
    expect(out.bandOf[0]).toBe(out.bandOf[2]);
  });

  /* Tensor order within a block is an artefact of the file's directory, not a
     property of the block. Two identical blocks listed differently must not read
     as two different shapes. */
  it('is insensitive to the order tensors are listed in', () => {
    const a = [
      tensor('blk.0.attn_q.weight', 0, [4, 4], 'attention'),
      tensor('blk.0.ffn_up.weight', 0, [4, 8], 'ffn'),
      tensor('blk.1.ffn_up.weight', 1, [4, 8], 'ffn'),
      tensor('blk.1.attn_q.weight', 1, [4, 4], 'attention'),
    ];
    expect(layerSignatures(a).bands).toBe(1);
  });

  it('reports nothing rather than one empty group with no inventory', () => {
    expect(layerSignatures([])).toEqual({ bands: 0, bandOf: [] });
  });
});

describe('buildInspectGraph', () => {
  /* The whole reason the stack is one node with a rail. A 48-layer model has ~290
     sublayers, and 290 nodes is both unreadable and a rebuild storm on every poll. */
  it('does not grow its node count with the layer count', () => {
    const small = buildInspectGraph(arch({ layers: 4 }), null, AT_MODEL, false);
    const huge = buildInspectGraph(arch({ layers: 262 }), null, AT_MODEL, false);
    expect(huge.nodes).toHaveLength(small.nodes.length);
    expect(huge.nodes.length).toBeLessThan(8);
  });

  it('puts one tick per block on the rail', () => {
    const g = buildInspectGraph(arch({ layers: 32 }), uniformInventory(32), AT_MODEL, false);
    expect(g.nodes.find((n) => n.id === 'stack')?.rail?.ticks).toHaveLength(32);
  });

  it('expands exactly one block, and only when it is open', () => {
    const shut = buildInspectGraph(arch(), null, AT_MODEL, false);
    const open = buildInspectGraph(arch(), null, { stage: 'block', layer: 7 }, true);
    expect(shut.nodes.map((n) => n.id)).not.toContain('attention');
    expect(open.nodes.map((n) => n.id)).toContain('attention');
    // Still O(1): norm + attention + ffn on top of the four fixed nodes.
    expect(open.nodes).toHaveLength(7);
  });

  /* The skip edge is the entire argument for drawing this as a graph. The DOM
     stack it replaced rendered "(+) residual" as a text row connecting nothing. */
  it('draws the residual as a real skip edge past the chain', () => {
    const g = buildInspectGraph(arch(), null, { stage: 'block', layer: 0 }, true);
    const residual = g.edges.find((e) => e.residual);
    expect(residual).toBeDefined();
    expect(residual?.source).toBe('stack');
    expect(residual?.target).toBe('ffn');
  });

  it('draws MoE instead of the feed-forward when the model has experts', () => {
    const g = buildInspectGraph(
      arch({
        moe: {
          experts: 8,
          expertsPerToken: 2,
          expertIntermediateSize: 4096,
          sharedExperts: 1,
          activeFraction: 0.25,
        },
      }),
      null,
      { stage: 'block', layer: 0 },
      true,
    );
    const ids = g.nodes.map((n) => n.id);
    expect(ids).toContain('moe');
    expect(ids).not.toContain('ffn');
  });

  /* With no GGUF the pane must still draw the architecture, minus the measured
     facts — a gap is never rendered as a number. */
  it('builds from the architecture alone when there is no inventory', () => {
    const g = buildInspectGraph(arch(), null, AT_MODEL, false);
    expect(g.nodes.find((n) => n.id === 'model')?.facts).not.toContain('0 tensors');
    expect(g.nodes.find((n) => n.id === 'stack')?.rail?.count).toBe(32);
  });

  it('gives every block a visible tick even when its bytes are unknown', () => {
    const inventory = uniformInventory(4);
    for (const t of inventory.tensors) t.byteSize = null;
    const rail = buildInspectGraph(arch({ layers: 4 }), inventory, AT_MODEL, false).nodes.find(
      (n) => n.id === 'stack',
    )?.rail;
    expect(rail?.ticks.every((t) => t.weight > 0)).toBe(true);
  });
});

describe('focus', () => {
  /* The regression test for the cross-pane wiring: the lens publishes a layer and
     the explorer must both select the block AND be able to bring it into view. */
  it('reports the stack as the focus when the locus names a layer', () => {
    const g = buildInspectGraph(arch(), uniformInventory(32), { stage: 'block', layer: 15 }, true);
    expect(g.focusId).toBe('stack');
    expect(g.nodes.find((n) => n.id === 'stack')?.rail?.selected).toBe(15);
  });

  it('reports the embedding when the locus names layer -1', () => {
    const g = buildInspectGraph(arch(), null, { stage: 'embedding', layer: null }, false);
    expect(g.focusId).toBe('embedding');
  });

  /* A sublayer selected while the block is shut has no node to focus, and saying
     so is better than pointing the viewport at something that is not drawn. */
  it('reports no focus for a node that is not on the canvas', () => {
    expect(
      buildInspectGraph(arch(), null, { stage: 'attention', layer: 3 }, false).focusId,
    ).toBeNull();
  });
});

describe('nodeIdFor / selectionFor', () => {
  it('round-trips every stage', () => {
    const cases: Selection[] = [
      { stage: 'model', layer: null },
      { stage: 'embedding', layer: null },
      { stage: 'output', layer: null },
      { stage: 'block', layer: 12 },
      { stage: 'attention', layer: 12 },
      { stage: 'ffn', layer: 12 },
      { stage: 'norm', layer: 12 },
    ];
    for (const selection of cases) {
      expect(selectionFor(nodeIdFor(selection), selection.layer)).toEqual(selection);
    }
  });

  /* The layer is deliberately not in a sublayer's id: stepping through layers must
     not tear the node down and rebuild it. */
  it('keeps a sublayer id stable across layers', () => {
    expect(nodeIdFor({ stage: 'attention', layer: 1 })).toBe(
      nodeIdFor({ stage: 'attention', layer: 40 }),
    );
  });
});

describe('headGroups', () => {
  const gqa = (over: Partial<AttentionSpec>) =>
    ({ heads: 24, kvHeads: 8, groupRatio: 3, ...over }) as AttentionSpec;

  it('draws one group per KV head under the cap', () => {
    expect(headGroups(gqa({}))).toEqual({ groups: 8, perGroup: 3, hidden: 0 });
  });

  it('caps the drawing and reports what it left out', () => {
    expect(headGroups(gqa({ kvHeads: 32, groupRatio: 32 }))).toEqual({
      groups: 8,
      perGroup: 8,
      hidden: 24,
    });
  });

  it('declines to draw when the ratio is unknown', () => {
    expect(headGroups(gqa({ groupRatio: null }))).toBeNull();
    expect(headGroups(gqa({ kvHeads: null }))).toBeNull();
  });
});

describe('inspectGraphKey', () => {
  /* An identical refetch produces new objects; keying on identity would rebuild
     every node on every poll and drop the viewport mid-pan. */
  it('is unchanged by a refetch that changes nothing drawn', () => {
    const a = inspectGraphKey(arch(), uniformInventory(32), AT_MODEL, false);
    const b = inspectGraphKey(arch(), uniformInventory(32), AT_MODEL, false);
    expect(a).toBe(b);
  });

  it('changes when the selection moves to another layer', () => {
    expect(inspectGraphKey(arch(), null, { stage: 'block', layer: 1 }, true)).not.toBe(
      inspectGraphKey(arch(), null, { stage: 'block', layer: 2 }, true),
    );
  });

  it('changes when the model does', () => {
    expect(inspectGraphKey(arch(), null, AT_MODEL, false)).not.toBe(
      inspectGraphKey(arch({ model: 'other' }), null, AT_MODEL, false),
    );
  });
});
