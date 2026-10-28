/**
 * The Steer section's state, outside any component: what to steer with, the run
 * in flight, and the runs so far — kept so strengths can be compared, newest first.
 *
 * Module-level like the suite's other stores: the digest and `interp.steer` read
 * it, and a run keeps streaming when the section is hidden behind another.
 */
import { useSyncExternalStore } from 'react';

import { runSteer, type SteerRequest, type SteerResult } from './api';

/** Runs kept for comparison. Each is two traces on disk; the list is the index. */
const HISTORY = 8;

export interface SteerState {
  /** What the next run steers with; seeded from the Features section. */
  draft: {
    artifactId: string;
    feature: number | null;
    /** The feature's label, for display only. */
    label: string;
    traceId: string;
    prompt: string;
    strength: number;
    maxTokens: number;
  };
  running: { phase: string; status: string } | null;
  result: SteerResult | null;
  history: SteerResult[];
  error: string | null;
}

let state: SteerState = {
  draft: {
    artifactId: '',
    feature: null,
    label: '',
    traceId: '',
    prompt: '',
    strength: 1,
    maxTokens: 32,
  },
  running: null,
  result: null,
  history: [],
  error: null,
};
const listeners = new Set<() => void>();

function set(patch: Partial<SteerState>): void {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export const steerStore = {
  get: (): SteerState => state,
  subscribe(l: () => void): () => void {
    listeners.add(l);
    return () => listeners.delete(l);
  },
};

export function useSteer(): SteerState {
  return useSyncExternalStore(steerStore.subscribe, steerStore.get);
}

export function setDraft(patch: Partial<SteerState['draft']>): void {
  set({ draft: { ...state.draft, ...patch } });
}

/** Point the next run at a feature — "Steer with this" from the Features section. */
export function steerWith(target: {
  artifactId: string;
  feature: number;
  label?: string | null;
  traceId: string;
  prompt: string;
}): void {
  const samePrompt = state.draft.traceId === target.traceId && state.draft.prompt;
  setDraft({
    artifactId: target.artifactId,
    feature: target.feature,
    label: target.label ?? '',
    traceId: target.traceId,
    // Keep a prompt the user already wrote for this trace; otherwise the trace's own.
    prompt: samePrompt ? state.draft.prompt : target.prompt,
  });
}

/** The request a draft makes. The backend reuses `traceId`'s own run as the
 *  default when it is the same experiment, so another strength costs one run. */
export function requestOf(draft: SteerState['draft']): SteerRequest | null {
  if (!draft.artifactId || draft.feature === null || !draft.traceId) return null;
  return {
    artifactId: draft.artifactId,
    feature: draft.feature,
    strength: draft.strength,
    traceId: draft.traceId,
    prompt: draft.prompt,
    maxTokens: draft.maxTokens,
  };
}

export async function run(): Promise<SteerResult | null> {
  const req = requestOf(state.draft);
  if (!req || state.running) return null;
  set({ running: { phase: 'default', status: 'starting' }, error: null });
  let result: SteerResult | null = null;
  try {
    await runSteer(req, (p) => {
      if (p.error) set({ error: p.error });
      else if (p.done && p.result) result = p.result;
      else if (p.phase) set({ running: { phase: p.phase, status: p.status ?? '' } });
    });
  } catch (err) {
    set({ error: err instanceof Error ? err.message : String(err) });
  }
  if (result) {
    set({ result, history: [result, ...state.history].slice(0, HISTORY) });
    // The next strength on this prompt reuses this default run.
    setDraft({ traceId: (result as SteerResult).defaultTraceId });
  }
  set({ running: null });
  return result;
}

/** A line for the suite digest: the last run and whether it took. */
export function steerDigest(current: SteerState = state): string | null {
  const r = current.result;
  if (!r) return null;
  const nt = r.nextToken;
  return (
    `steered F${r.feature} at L${r.layer} ×${r.strength} (scale ${r.scale.toFixed(0)}): ` +
    `${r.arrived.state.replace(/_/g, ' ')} (mean ${r.arrived.defaultMean}→${r.arrived.steeredMean})` +
    (nt ? `; next token "${nt.defaultTop.token}"→"${nt.steeredTop.token}", KL ${nt.kl}` : '') +
    (r.diverges === null ? '; text unchanged' : `; text diverges at token ${r.diverges}`)
  );
}
