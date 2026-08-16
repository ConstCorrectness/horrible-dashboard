/**
 * What part of the model a traced graph node belongs to, from its name.
 *
 * **Why this is not `gguf._classify`.** That one classifies *tensor* names from the
 * GGUF header (`blk.17.attn_q.weight`) — weights, in a file. This classifies *graph
 * node* names produced by llama.cpp's `cb()` during a forward pass (`kqv_out-17`) —
 * activations, in a run. The two vocabularies overlap in spirit and share almost no
 * strings, so the near-duplicate table is deliberate; merging them would mean one
 * rule list that is wrong about both.
 *
 * **Why the frontend derives it.** Stamping a `component` on `TraceRecord` at write
 * time would only ever reach traces recorded after the change; a trace already on
 * disk would filter as one undifferentiated bucket. The name is on the wire already,
 * so deriving here works retroactively and costs nothing.
 *
 * The block index is *not* parsed here — it arrives as `TraceRecord.layer` from
 * `traces.layer_of()`, and a second parser would be a second thing to get wrong.
 */

export type NodeKind =
  | 'attention'
  | 'ffn'
  | 'moe'
  | 'ssm'
  | 'norm'
  | 'residual'
  | 'embedding'
  | 'output'
  | 'other';

export const KIND_LABELS: Record<NodeKind, string> = {
  attention: 'attention',
  ffn: 'ffn',
  moe: 'moe',
  ssm: 'ssm',
  norm: 'norm',
  residual: 'residual',
  embedding: 'embedding',
  output: 'output',
  other: 'other',
};

/**
 * First match wins, so order carries meaning:
 *
 * - MoE before FFN — `ffn_moe_gate` is a router and starts with `ffn_`, so the plain
 *   prefix would swallow every expert node.
 * - `attn_norm` / `ffn_norm` are matched by their sub-block prefixes above the bare
 *   `norm` rule, so a block's norms stay with the sub-block they belong to. That is
 *   the same choice `gguf._BLOCK_RULES` makes, and for the same reason: otherwise
 *   filtering to "attention" hides half of attention.
 * - `l_out` is the residual stream and gets its own kind rather than being filed as
 *   "other". It is the single most-watched node in the list and burying it in the
 *   catch-all would make the most useful filter the least specific one.
 */
const RULES: ReadonlyArray<readonly [RegExp, NodeKind]> = [
  [/ffn_moe|_exps|ffn_gate_inp|shexp/, 'moe'],
  [/^attn_|^kq|_kq|attn_/, 'attention'],
  [/^ssm_|_ssm/, 'ssm'],
  [/^ffn_/, 'ffn'],
  [/^l_out/, 'residual'],
  [/^inp_embd|^token_embd|^inp_pos/, 'embedding'],
  [/^result_output|^output\b/, 'output'],
  [/norm/, 'norm'],
];

/** Classify one traced node by name. Unknown shapes are `other`, never guessed at. */
export function nodeKind(name: string): NodeKind {
  const lowered = name.toLowerCase();
  for (const [pattern, kind] of RULES) {
    if (pattern.test(lowered)) return kind;
  }
  return 'other';
}

/** The kinds actually present, in `RULES` order — so the filter bar offers the
 * toggles this trace can act on rather than a fixed row of mostly-dead ones. A
 * Mamba trace should not show an "attention" toggle that matches nothing. */
export function kindsPresent(names: readonly string[]): NodeKind[] {
  const seen = new Set<NodeKind>();
  for (const name of names) seen.add(nodeKind(name));
  const order: NodeKind[] = [
    'residual',
    'attention',
    'ffn',
    'moe',
    'ssm',
    'norm',
    'embedding',
    'output',
    'other',
  ];
  return order.filter((kind) => seen.has(kind));
}
