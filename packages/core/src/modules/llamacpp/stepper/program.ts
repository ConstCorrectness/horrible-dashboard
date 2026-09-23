/**
 * A trace read as a program: the instruction stream a debugger steps through.
 *
 * The tracer appends records in the order ggml **computed** them, each tagged with
 * its pass and its block. That is already an instruction listing, so nothing new is
 * stored to step through it:
 *
 * | debugger verb     | here                                               |
 * | ----------------- | -------------------------------------------------- |
 * | step into         | the next record (one graph node)                   |
 * | step over         | the first record of the next *frame* (block)       |
 * | step out          | the first record of the next forward pass          |
 * | step back         | the previous record — a snapshot makes reverse free |
 * | continue          | the next record a breakpoint stops on              |
 *
 * A **frame** is one decoder block of one pass. Nodes outside any block (the
 * embedding, the output head) are each their own frame, so "step over" from the
 * last block lands on the head rather than skipping it.
 *
 * The stage of a node comes from `nodeKind` — the one classifier. It is not
 * re-derived here or on the server; a second list of ggml node names is one
 * upstream rename away from disagreeing with the first.
 *
 * Pure and synchronous: every verb is `(program, cursor) -> cursor`, which is what
 * makes the stepper testable without a pane.
 */
import type { TraceRecord } from '../api';
import { nodeKind, type NodeKind } from '../node-kind';

export interface Step {
  /** Position in the program — the cursor value. */
  step: number;
  /** `TraceRecord.index`, which addresses the record on the server. */
  recordIndex: number;
  name: string;
  op: string;
  layer: number | null;
  passIndex: number;
  stage: NodeKind;
  fidelity: TraceRecord['fidelity'];
  /** `ne` with the trailing 1s dropped — the shape as a reader writes it. */
  shape: number[];
}

export interface Program {
  steps: Step[];
  passes: number[];
  layers: number[];
}

export function shapeOf(ne: readonly number[]): number[] {
  const out = [...ne];
  while (out.length > 1 && out[out.length - 1] === 1) out.pop();
  return out;
}

export function buildProgram(records: readonly TraceRecord[]): Program {
  const ordered = [...records].sort((a, b) => a.index - b.index);
  const steps = ordered.map<Step>((r, step) => ({
    step,
    recordIndex: r.index,
    name: r.name,
    op: r.op,
    layer: r.layer,
    passIndex: r.passIndex,
    stage: nodeKind(r.name),
    fidelity: r.fidelity,
    shape: shapeOf(r.ne),
  }));
  const passes = [...new Set(steps.map((s) => s.passIndex))].sort((a, b) => a - b);
  const layers = [
    ...new Set(steps.map((s) => s.layer).filter((l): l is number => l !== null)),
  ].sort((a, b) => a - b);
  return { steps, passes, layers };
}

/** The frame a step belongs to. Blockless nodes are frames of their own. */
export function frameKey(s: Step): string {
  return s.layer === null ? `${s.passIndex}:${s.name}` : `${s.passIndex}:L${s.layer}`;
}

const clamp = (p: Program, i: number) => Math.max(0, Math.min(p.steps.length - 1, i));

export function stepInto(p: Program, i: number): number {
  return clamp(p, i + 1);
}

export function stepBack(p: Program, i: number): number {
  return clamp(p, i - 1);
}

/** First step of the next frame, or the last step when there is none. */
export function stepOver(p: Program, i: number): number {
  const here = p.steps[i];
  if (!here) return 0;
  const key = frameKey(here);
  for (let j = i + 1; j < p.steps.length; j++) {
    if (frameKey(p.steps[j]) !== key) return j;
  }
  return p.steps.length - 1;
}

/** Start of the current frame; from its start, the start of the previous frame. */
export function reverseStepOver(p: Program, i: number): number {
  const here = p.steps[i];
  if (!here) return 0;
  const start = frameStart(p, i);
  if (start < i) return start;
  return start > 0 ? frameStart(p, start - 1) : 0;
}

export function frameStart(p: Program, i: number): number {
  const key = frameKey(p.steps[i]);
  let j = i;
  while (j > 0 && frameKey(p.steps[j - 1]) === key) j--;
  return j;
}

/** First step of the next pass — or the last step of this one when it is the last. */
export function stepOut(p: Program, i: number): number {
  const here = p.steps[i];
  if (!here) return 0;
  for (let j = i + 1; j < p.steps.length; j++) {
    if (p.steps[j].passIndex !== here.passIndex) return j;
  }
  return p.steps.length - 1;
}

/** Where a click on a layer (in the explorer, or the timeline) lands: that layer's
 * first step in the current pass, optionally narrowed to a stage. `null` when the
 * pass never computed that layer — a layer filter on the trace — which the caller
 * reports rather than jumping somewhere nearby. */
export function stepForLayer(
  p: Program,
  passIndex: number,
  layer: number | null,
  stage?: NodeKind,
): number | null {
  let first: number | null = null;
  for (const s of p.steps) {
    if (s.passIndex !== passIndex || s.layer !== layer) continue;
    if (first === null) first = s.step;
    if (!stage || s.stage === stage) return s.step;
  }
  return first;
}

// ── breakpoints ─────────────────────────────────────────────────────────────

export type StatName = 'rms' | 'absMax';

export type Breakpoint =
  | { id: string; kind: 'layer'; layer: number }
  | { id: string; kind: 'stage'; stage: NodeKind }
  | { id: string; kind: 'node'; name: string }
  /** Stops where a stage's statistic crosses a threshold — "where does it blow up". */
  | { id: string; kind: 'stat'; stage: NodeKind | 'any'; stat: StatName; above: number }
  /** Stops at the residual step of the first layer whose lens top-1 at the
   * cursor's position is `tokenId` — "where does the answer appear". */
  | { id: string; kind: 'lens'; tokenId: number; text: string };

/**
 * Everything a condition may consult, supplied by the section. Missing data is
 * `undefined`, and a condition that needs it does **not** fire: stopping on a
 * statistic that was never measured would be a breakpoint on a guess.
 */
export interface HitContext {
  /** `recordIndex -> value` per statistic, for the passes loaded so far. */
  stats: Partial<Record<StatName, ReadonlyMap<number, number | null>>>;
  /** `passIndex:layer -> top-1 token id` of the lens at the watched position. */
  lensTop1?: ReadonlyMap<string, number>;
}

export function lensKey(passIndex: number, layer: number): string {
  return `${passIndex}:${layer}`;
}

export function hits(bp: Breakpoint, s: Step, ctx: HitContext): boolean {
  switch (bp.kind) {
    case 'layer':
      // A layer breakpoint stops on *entry* to the block — the first step of the
      // frame — so continuing does not stop six times inside the same block.
      return s.layer === bp.layer;
    case 'stage':
      return s.stage === bp.stage;
    case 'node':
      return s.name === bp.name;
    case 'stat': {
      if (bp.stage !== 'any' && s.stage !== bp.stage) return false;
      const value = ctx.stats[bp.stat]?.get(s.recordIndex);
      return typeof value === 'number' && value > bp.above;
    }
    case 'lens': {
      if (s.stage !== 'residual' || s.layer === null) return false;
      return ctx.lensTop1?.get(lensKey(s.passIndex, s.layer)) === bp.tokenId;
    }
  }
}

function entryOnly(bp: Breakpoint, p: Program, j: number): boolean {
  if (bp.kind !== 'layer') return true;
  return j === frameStart(p, j);
}

/** Does any enabled breakpoint stop at step `j`? */
export function stopsAt(
  p: Program,
  j: number,
  breakpoints: readonly Breakpoint[],
  ctx: HitContext,
): Breakpoint | null {
  const s = p.steps[j];
  for (const bp of breakpoints) {
    if (hits(bp, s, ctx) && entryOnly(bp, p, j)) return bp;
  }
  return null;
}

export interface ContinueResult {
  step: number;
  /** The breakpoint that stopped it, or null when it ran off the end. */
  hit: Breakpoint | null;
}

export function continueForward(
  p: Program,
  i: number,
  breakpoints: readonly Breakpoint[],
  ctx: HitContext,
): ContinueResult {
  for (let j = i + 1; j < p.steps.length; j++) {
    const hit = stopsAt(p, j, breakpoints, ctx);
    if (hit) return { step: j, hit };
  }
  return { step: Math.max(0, p.steps.length - 1), hit: null };
}

export function continueBack(
  p: Program,
  i: number,
  breakpoints: readonly Breakpoint[],
  ctx: HitContext,
): ContinueResult {
  for (let j = i - 1; j >= 0; j--) {
    const hit = stopsAt(p, j, breakpoints, ctx);
    if (hit) return { step: j, hit };
  }
  return { step: 0, hit: null };
}

export function describeBreakpoint(bp: Breakpoint): string {
  switch (bp.kind) {
    case 'layer':
      return `layer ${bp.layer}`;
    case 'stage':
      return `every ${bp.stage} node`;
    case 'node':
      return bp.name;
    case 'stat':
      return `${bp.stage === 'any' ? 'any node' : bp.stage} ${bp.stat} > ${bp.above}`;
    case 'lens':
      return `lens top-1 = ${JSON.stringify(bp.text)}`;
  }
}

/** The call stack at a step, outermost first — what the left column lists. */
export function callStack(s: Step): { label: string; detail: string }[] {
  return [
    { label: s.passIndex === 0 ? 'prompt pass' : `pass ${s.passIndex}`, detail: 'forward' },
    {
      label:
        s.layer === null
          ? s.stage === 'output'
            ? 'output head'
            : 'embedding'
          : `block ${s.layer}`,
      detail: s.layer === null ? '' : 'decoder',
    },
    { label: s.stage, detail: 'stage' },
    { label: s.name, detail: `${s.op} · ${s.shape.join('×')}` },
  ];
}
