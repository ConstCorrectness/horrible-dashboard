/**
 * Live deep-research run state, fed by the `research` /ws channel.
 *
 * Mirrors the files/library store pattern: a module-scoped store with
 * `useSyncExternalStore` subscription; `run`/`step` events are full snapshots
 * upserted by id (ordering doesn't matter), `delta` events append streamed
 * synthesis text per step. HTTP fills the initial list; the socket keeps it hot.
 */
import { useSyncExternalStore } from 'react';

import { subscribeChannel } from '../../ws';
import { getRunSteps, listRuns, type RunModel, type StepModel } from './api';

interface ResearchState {
  runs: RunModel[];
  /** Steps per run id, ordered by seq. */
  steps: Record<string, StepModel[]>;
  /** Streaming synthesis text per step id (live view before the output lands). */
  deltas: Record<string, string>;
}

let state: ResearchState = { runs: [], steps: {}, deltas: {} };
const listeners = new Set<() => void>();
let wsUnsub: (() => void) | null = null;
let loaded = false;

function emit(next: ResearchState): void {
  state = next;
  for (const listener of listeners) listener();
}

function upsertRun(run: RunModel): void {
  const runs = [...state.runs];
  const index = runs.findIndex((r) => r.id === run.id);
  if (index >= 0) runs[index] = run;
  else runs.unshift(run);
  emit({ ...state, runs });
}

function upsertStep(step: StepModel): void {
  const forRun = [...(state.steps[step.run_id] ?? [])];
  const index = forRun.findIndex((s) => s.id === step.id);
  if (index >= 0) forRun[index] = step;
  else forRun.push(step);
  forRun.sort((a, b) => a.seq - b.seq || a.id.localeCompare(b.id));
  emit({ ...state, steps: { ...state.steps, [step.run_id]: forRun } });
}

function appendDelta(stepId: string, text: string): void {
  emit({
    ...state,
    deltas: { ...state.deltas, [stepId]: (state.deltas[stepId] ?? '') + text },
  });
}

function ensureWired(): void {
  if (!wsUnsub) {
    wsUnsub = subscribeChannel('research', (msg) => {
      if (msg.event === 'run') upsertRun(msg.data as RunModel);
      else if (msg.event === 'step') upsertStep(msg.data as StepModel);
      else if (msg.event === 'delta') {
        const { step_id, text } = msg.data as { step_id: string; text: string };
        appendDelta(step_id, text);
      }
    });
  }
  if (!loaded) {
    loaded = true;
    void listRuns()
      .then((res) => emit({ ...state, runs: res.runs }))
      .catch(() => {
        loaded = false; // backend down — retry on next mount
      });
  }
}

/** Load (or refresh) one run's steps over HTTP; the socket updates them after. */
export function loadSteps(runId: string): void {
  void getRunSteps(runId)
    .then((res) => emit({ ...state, steps: { ...state.steps, [runId]: res.steps } }))
    .catch(() => undefined);
}

function subscribe(listener: () => void): () => void {
  ensureWired();
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useResearchState(): ResearchState {
  return useSyncExternalStore(subscribe, () => state);
}
