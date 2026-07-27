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
import { getRunSteps, getToolCalls, listRuns, type RunModel, type StepModel } from './api';

/** One subagent tool call, as it happens. */
export interface ToolCallEvent {
  run_id: string;
  step_id: string;
  seq: number;
  name: string;
  args: Record<string, unknown>;
  ok: boolean;
  ms?: number | null;
  summary: string;
}

interface ResearchState {
  runs: RunModel[];
  /** Steps per run id, ordered by seq. */
  steps: Record<string, StepModel[]>;
  /** Streaming synthesis text per step id (live view before the output lands). */
  deltas: Record<string, string>;
  /**
   * Tool calls per step id. A step's transcript is only persisted when the step
   * *finishes*, so without this a subagent shows nothing for minutes — exactly the
   * window in which you want to know whether it's searching sensibly or looping.
   */
  toolCalls: Record<string, ToolCallEvent[]>;
}

let state: ResearchState = { runs: [], steps: {}, deltas: {}, toolCalls: {} };
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

function appendToolCall(call: ToolCallEvent): void {
  const forStep = state.toolCalls[call.step_id] ?? [];
  // Deduped by seq: a reconnect can replay, and a retried step restarts its
  // numbering, so last-write-wins on a seq is the behaviour that matches the DB.
  const next = [...forStep.filter((c) => c.seq !== call.seq), call].sort((a, b) => a.seq - b.seq);
  emit({ ...state, toolCalls: { ...state.toolCalls, [call.step_id]: next } });
}

function ensureWired(): void {
  if (!wsUnsub) {
    wsUnsub = subscribeChannel('research', (msg) => {
      if (msg.event === 'run') upsertRun(msg.data as RunModel);
      else if (msg.event === 'step') upsertStep(msg.data as StepModel);
      else if (msg.event === 'tool') appendToolCall(msg.data as unknown as ToolCallEvent);
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
  // How a browser that wasn't open during the run catches up on its tool trace.
  void getToolCalls(runId)
    .then((res) => {
      const byStep: Record<string, ToolCallEvent[]> = {};
      for (const call of res.calls) {
        (byStep[call.step_id] ??= []).push(call as ToolCallEvent);
      }
      emit({ ...state, toolCalls: { ...state.toolCalls, ...byStep } });
    })
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
