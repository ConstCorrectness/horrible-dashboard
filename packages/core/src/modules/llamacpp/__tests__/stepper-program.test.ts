import { describe, expect, it } from 'vitest';

import type { TraceRecord } from '../api';
import {
  buildProgram,
  callStack,
  continueBack,
  continueForward,
  lensKey,
  reverseStepOver,
  stepBack,
  stepForLayer,
  stepInto,
  stepOut,
  stepOver,
  type Breakpoint,
  type HitContext,
} from '../stepper/program';

/** Node names in the order a real Llama-3.2 trace captured them (two blocks). */
const PASS_NODES = [
  'attn_norm-0',
  'kq_soft_max-0',
  'kqv_out-0',
  'ffn_inp-0',
  'ffn_out-0',
  'l_out-0',
  'attn_norm-1',
  'kq_soft_max-1',
  'kqv_out-1',
  'ffn_inp-1',
  'ffn_out-1',
  'l_out-1',
  'result_norm',
  'result_output',
];

function record(index: number, name: string, passIndex: number): TraceRecord {
  const m = /-(\d+)$/.exec(name);
  return {
    index,
    name,
    op: 'ADD',
    dtype: 'f16',
    ne: [8, 4, 1, 1],
    nb: [4, 32, 128, 128],
    layer: m ? Number(m[1]) : null,
    passIndex,
    fidelity: 'fp16',
    offset: 0,
    length: 64,
    summary: {},
  };
}

function twoPassProgram() {
  const records: TraceRecord[] = [];
  for (const pass of [0, 1]) {
    for (const name of PASS_NODES) records.push(record(records.length, name, pass));
  }
  // Shuffled on purpose: the program is built in *capture* order, not array order.
  return buildProgram([...records].reverse());
}

const noCtx: HitContext = { stats: {} };

describe('stepper program', () => {
  it('orders steps by capture index and drops trailing unit dims from shapes', () => {
    const p = twoPassProgram();
    expect(p.steps.map((s) => s.name).slice(0, 3)).toEqual([
      'attn_norm-0',
      'kq_soft_max-0',
      'kqv_out-0',
    ]);
    expect(p.steps[0].shape).toEqual([8, 4]);
    expect(p.passes).toEqual([0, 1]);
    expect(p.layers).toEqual([0, 1]);
    expect(p.steps[5].stage).toBe('residual');
  });

  it('steps into, back, and clamps at both ends', () => {
    const p = twoPassProgram();
    expect(stepInto(p, 0)).toBe(1);
    expect(stepBack(p, 0)).toBe(0);
    expect(stepInto(p, p.steps.length - 1)).toBe(p.steps.length - 1);
  });

  it('steps over a whole block, and treats the output head as frames of its own', () => {
    const p = twoPassProgram();
    expect(stepOver(p, 2)).toBe(6); // block 0 -> first node of block 1
    expect(stepOver(p, 6)).toBe(12); // block 1 -> result_norm, not skipped
    expect(stepOver(p, 12)).toBe(13); // result_norm -> result_output
    expect(stepOver(p, 13)).toBe(14); // -> the next pass's first block
  });

  it('reverse step over goes to the frame start, then to the previous frame', () => {
    const p = twoPassProgram();
    expect(reverseStepOver(p, 9)).toBe(6);
    expect(reverseStepOver(p, 6)).toBe(0);
    expect(reverseStepOver(p, 0)).toBe(0);
  });

  it('steps out to the next pass, or the end of the last one', () => {
    const p = twoPassProgram();
    expect(stepOut(p, 3)).toBe(14);
    expect(stepOut(p, 20)).toBe(p.steps.length - 1);
  });

  it('finds a layer (and stage) in the current pass for an explorer click', () => {
    const p = twoPassProgram();
    expect(stepForLayer(p, 1, 1)).toBe(20);
    expect(stepForLayer(p, 1, 1, 'residual')).toBe(25);
    expect(stepForLayer(p, 0, 7)).toBeNull();
  });

  it('a layer breakpoint stops on block entry only, not on every node in it', () => {
    const p = twoPassProgram();
    const bps: Breakpoint[] = [{ id: 'a', kind: 'layer', layer: 1 }];
    const first = continueForward(p, 0, bps, noCtx);
    expect(first).toMatchObject({ step: 6, hit: bps[0] });
    expect(continueForward(p, 6, bps, noCtx).step).toBe(20); // next pass's block 1
    expect(continueBack(p, 20, bps, noCtx).step).toBe(6);
  });

  it('runs off the end when nothing stops it, and says so', () => {
    const p = twoPassProgram();
    expect(continueForward(p, 0, [], noCtx)).toEqual({ step: p.steps.length - 1, hit: null });
  });

  it('a stat breakpoint never fires on a statistic that was not measured', () => {
    const p = twoPassProgram();
    const bp: Breakpoint = { id: 's', kind: 'stat', stage: 'residual', stat: 'rms', above: 10 };
    const stats = new Map<number, number | null>([
      [5, 3], // l_out-0: under the threshold
      [11, null], // l_out-1: not measured — must not stop
      [19, 42], // l_out-0 of pass 1
    ]);
    expect(continueForward(p, 0, [bp], { stats: { rms: stats } }).step).toBe(19);
  });

  it('a lens breakpoint stops at the residual where the watched token becomes top-1', () => {
    const p = twoPassProgram();
    const bp: Breakpoint = { id: 'l', kind: 'lens', tokenId: 42, text: ' Paris' };
    const lensTop1 = new Map([
      [lensKey(0, 0), 7],
      [lensKey(0, 1), 42],
    ]);
    const hit = continueForward(p, 0, [bp], { stats: {}, lensTop1 });
    expect(p.steps[hit.step].name).toBe('l_out-1');
  });

  it('names the call stack outermost first', () => {
    const p = twoPassProgram();
    expect(callStack(p.steps[7]).map((f) => f.label)).toEqual([
      'prompt pass',
      'block 1',
      'attention',
      'kq_soft_max-1',
    ]);
    expect(callStack(p.steps[13])[1].label).toBe('output head');
  });
});
