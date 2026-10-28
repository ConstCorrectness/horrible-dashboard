import { describe, expect, it } from 'vitest';

import {
  describeNode,
  featureIndex,
  layout,
  prune,
  topEdges,
  type AttributionGraph,
} from '../graph/model';

const TOKENS = ['<bos>', ' capital', ' of', ' Dallas', ' is'];

function graph(): AttributionGraph {
  return {
    metadata: { scan: 'gemma-2-2b', prompt_tokens: TOKENS },
    nodes: [
      { node_id: 'E_1_3', layer: 'E', ctx_idx: 3, feature_type: 'embedding', feature: 1 },
      {
        node_id: '4_9_3',
        layer: '4',
        ctx_idx: 3,
        feature_type: 'cross layer transcoder',
        feature: 9,
        influence: 0.2,
        clerp: 'Texas',
      },
      {
        node_id: '15_7_4',
        layer: '15',
        ctx_idx: 4,
        feature_type: 'cross layer transcoder',
        feature: 7,
        influence: 0.5,
      },
      {
        node_id: '9_2_4',
        layer: '9',
        ctx_idx: 4,
        feature_type: 'mlp reconstruction error',
        feature: 2,
        influence: 0.95,
      },
      {
        node_id: '27_5_4',
        layer: '27',
        ctx_idx: 4,
        feature_type: 'logit',
        feature: 5,
        clerp: 'Output " Austin"',
        token_prob: 0.4,
        is_target_logit: true,
      },
    ],
    links: [
      { source: 'E_1_3', target: '4_9_3', weight: 1.0 },
      { source: '4_9_3', target: '15_7_4', weight: 0.8 },
      { source: '15_7_4', target: '27_5_4', weight: 0.6 },
      { source: '4_9_3', target: '27_5_4', weight: 0.03 },
      { source: '9_2_4', target: '27_5_4', weight: -0.5 },
    ],
  };
}

describe('prune', () => {
  it('keeps the ends always and features by cumulative influence', () => {
    const p = prune(graph(), 0.6, 0);
    expect(p.nodes.map((n) => n.node_id)).toEqual(['E_1_3', '4_9_3', '15_7_4', '27_5_4']);
    expect(p.hiddenNodes).toBe(1); // the error node, influence 0.95
  });

  it('drops edges weak relative to their target’s strongest input', () => {
    const p = prune(graph(), 1, 0.1);
    // Into the logit the strongest is 0.6, so 0.03 goes and -0.5 (|w| 0.5) stays.
    const into = p.links.filter((l) => l.target === '27_5_4').map((l) => l.weight);
    expect(into.sort()).toEqual([-0.5, 0.6]);
  });

  it('a pinned node survives any threshold; pinned-only keeps just the subgraph', () => {
    const pinned = new Set(['9_2_4']);
    expect(prune(graph(), 0.1, 0, pinned).nodes.some((n) => n.node_id === '9_2_4')).toBe(true);
    const only = prune(graph(), 0.6, 0, pinned, true);
    expect(only.nodes.map((n) => n.node_id).sort()).toEqual(['27_5_4', '9_2_4']);
  });
});

describe('layout', () => {
  it('puts embeddings at the bottom, logits on top, positions left to right', () => {
    const { placed, rows } = layout(graph().nodes, TOKENS.length, 500, 400);
    expect(rows).toEqual([-1, 4, 9, 15, 27]);
    const at = new Map(placed.map((p) => [p.node.node_id, p]));
    expect(at.get('E_1_3')!.y).toBeGreaterThan(at.get('27_5_4')!.y);
    expect(at.get('E_1_3')!.x).toBeLessThan(at.get('27_5_4')!.x);
  });

  it('spreads nodes sharing a cell instead of stacking them', () => {
    const nodes = [
      {
        node_id: 'a',
        layer: '3',
        ctx_idx: 2,
        feature_type: 'cross layer transcoder',
        influence: 0.1,
      },
      {
        node_id: 'b',
        layer: '3',
        ctx_idx: 2,
        feature_type: 'cross layer transcoder',
        influence: 0.2,
      },
    ];
    const { placed } = layout(nodes, 5, 500, 300);
    expect(placed[0].x).not.toBe(placed[1].x);
    expect(placed[0].y).toBe(placed[1].y);
  });
});

describe('edges and names', () => {
  it('ranks a node’s edges by |weight|', () => {
    expect(topEdges(graph().links, '27_5_4', 'in', 2).map((l) => l.weight)).toEqual([0.6, -0.5]);
  });

  it('describes each kind of node', () => {
    const [emb, tex, , err, logit] = graph().nodes;
    expect(describeNode(emb, TOKENS)).toBe('Emb " Dallas"');
    expect(describeNode(tex, TOKENS)).toBe('L4 F9 at " Dallas"');
    expect(describeNode(err, TOKENS)).toBe('error L9 at " is"');
    expect(describeNode(logit, TOKENS)).toBe('Output " Austin"');
  });
});

describe('featureIndex', () => {
  it('reads the index from the id, not an encoded feature number', () => {
    const node = {
      node_id: '20_15589_10',
      layer: '20',
      ctx_idx: 10,
      feature_type: 'cross layer transcoder',
      feature: 2015589,
    };
    expect(featureIndex(node)).toBe(15589);
    expect(describeNode(node, TOKENS.concat(Array(6).fill('x')))).toBe('L20 F15589 at "x"');
  });
});
