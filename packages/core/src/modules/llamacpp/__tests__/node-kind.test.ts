import { describe, expect, it } from 'vitest';

import { kindsPresent, nodeKind } from '../node-kind';

describe('nodeKind', () => {
  it('classifies the transformer nodes a real gemma trace produces', () => {
    // These names are copied from an actual trace of gemma-4-E4B, not invented.
    expect(nodeKind('attn_norm-0')).toBe('attention');
    expect(nodeKind('kq-0')).toBe('attention');
    expect(nodeKind('kq_soft_max-17')).toBe('attention');
    expect(nodeKind('kqv_out-41')).toBe('attention');
    expect(nodeKind('ffn_norm-3')).toBe('ffn');
    expect(nodeKind('ffn_out-3')).toBe('ffn');
    expect(nodeKind('l_out-41')).toBe('residual');
    expect(nodeKind('inp_embd')).toBe('embedding');
    expect(nodeKind('result_norm')).toBe('norm');
    expect(nodeKind('result_output')).toBe('output');
  });

  it('keeps a sub-block norm with its sub-block, not in the bare norm bucket', () => {
    // Filtering to "attention" must not hide half of attention. Same rule as
    // gguf._BLOCK_RULES, enforced here for graph nodes.
    expect(nodeKind('attn_norm-9')).not.toBe('norm');
    expect(nodeKind('ffn_norm-9')).not.toBe('norm');
    // …while a norm that belongs to no sub-block still lands there.
    expect(nodeKind('result_norm')).toBe('norm');
  });

  it('routes MoE nodes away from plain ffn, which their names start with', () => {
    expect(nodeKind('ffn_moe_gate-5')).toBe('moe');
    expect(nodeKind('ffn_gate_inp-5')).toBe('moe');
    expect(nodeKind('ffn_moe_down_exps-5')).toBe('moe');
    // The non-MoE sibling must stay put, or the whole distinction is pointless.
    expect(nodeKind('ffn_gate-5')).toBe('ffn');
  });

  it('classifies the state-space nodes a Mamba graph produces', () => {
    expect(nodeKind('ssm_in-0')).toBe('ssm');
    expect(nodeKind('ssm_conv-0')).toBe('ssm');
    expect(nodeKind('ssm_scan-12')).toBe('ssm');
    expect(nodeKind('ssm_out-12')).toBe('ssm');
  });

  it('falls back to other rather than guessing', () => {
    expect(nodeKind('some_future_op-2')).toBe('other');
  });
});

describe('kindsPresent', () => {
  it('offers only the toggles this trace can act on', () => {
    // A Mamba trace must not show an attention toggle that matches nothing.
    expect(kindsPresent(['inp_embd', 'ssm_in-0', 'l_out-0'])).toEqual([
      'residual',
      'ssm',
      'embedding',
    ]);
  });

  it('returns a stable order regardless of the order nodes were captured in', () => {
    const forward = kindsPresent(['inp_embd', 'attn_norm-0', 'ffn_out-0', 'l_out-0']);
    const shuffled = kindsPresent(['l_out-0', 'ffn_out-0', 'inp_embd', 'attn_norm-0']);
    expect(forward).toEqual(shuffled);
    expect(forward).toEqual(['residual', 'attention', 'ffn', 'embedding']);
  });
});
