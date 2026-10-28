/**
 * The Axis section's state, outside any component: the built axes, the one in
 * view, a build in flight, the last monitor reading and the last cap comparison.
 *
 * Module-level like the suite's other stores: the digest and `interp.axis` read
 * it, and a build — minutes of generations — keeps streaming while the section is
 * hidden behind another.
 */
import { useSyncExternalStore } from 'react';

import {
  buildAxis,
  capAxis,
  getAxes,
  getTraceSeries,
  monitorAxis,
  type AxisDefaults,
  type AxisSummary,
  type CapResult,
  type Message,
  type MonitorResult,
  type TraceSeries,
} from './api';

export type MonitorSource = 'trace' | 'conversation' | 'turn';

export interface AxisState {
  axes: AxisSummary[];
  defaults: AxisDefaults | null;
  /** The axis in view. */
  id: string;
  build: {
    modelPath: string;
    roleCount: number;
    rolesText: string;
    maxTokens: number;
    running: { done: number; total: number; condition: string; elapsed: number } | null;
  };
  monitor: {
    source: MonitorSource;
    traceId: string;
    conversation: string;
    turnId: string;
    running: string | null;
    /** A trace's reading, or a templated conversation's. */
    result: TraceSeries | MonitorResult | null;
  };
  cap: {
    system: string;
    prompt: string;
    maxTokens: number;
    running: string | null;
    result: CapResult | null;
  };
  error: string | null;
}

let state: AxisState = {
  axes: [],
  defaults: null,
  id: '',
  build: { modelPath: '', roleCount: 0, rolesText: '', maxTokens: 32, running: null },
  monitor: {
    source: 'conversation',
    traceId: '',
    conversation: '',
    turnId: '',
    running: null,
    result: null,
  },
  cap: {
    system:
      'You are a ghost who has haunted the same house for two hundred years. Stay fully in character.',
    prompt: 'Tell me about yourself.',
    maxTokens: 48,
    running: null,
    result: null,
  },
  error: null,
};
const listeners = new Set<() => void>();

function set(patch: Partial<AxisState>): void {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export const axisStore = {
  get: (): AxisState => state,
  subscribe(l: () => void): () => void {
    listeners.add(l);
    return () => listeners.delete(l);
  },
};

export function useAxis(): AxisState {
  return useSyncExternalStore(axisStore.subscribe, axisStore.get);
}

export function current(s: AxisState = state): AxisSummary | null {
  return s.axes.find((a) => a.id === s.id) ?? null;
}

export function selectAxis(id: string): void {
  set({ id, monitor: { ...state.monitor, result: null }, cap: { ...state.cap, result: null } });
}

export function setBuild(patch: Partial<AxisState['build']>): void {
  set({ build: { ...state.build, ...patch } });
}

export function setMonitor(patch: Partial<AxisState['monitor']>): void {
  set({ monitor: { ...state.monitor, ...patch } });
}

export function setCap(patch: Partial<AxisState['cap']>): void {
  set({ cap: { ...state.cap, ...patch } });
}

export async function refreshAxes(): Promise<void> {
  try {
    const { axes, defaults } = await getAxes();
    const keep = axes.some((a) => a.id === state.id);
    set({ axes, defaults, id: keep ? state.id : (axes[0]?.id ?? '') });
  } catch (err) {
    set({ error: err instanceof Error ? err.message : String(err) });
  }
}

function fail(err: unknown): void {
  set({ error: err instanceof Error ? err.message : String(err) });
}

export async function build(): Promise<AxisSummary | null> {
  const b = state.build;
  if (!b.modelPath || b.running) return null;
  setBuild({ running: { done: 0, total: 0, condition: 'loading', elapsed: 0 } });
  set({ error: null });
  let built: AxisSummary | null = null;
  try {
    await buildAxis(
      {
        modelPath: b.modelPath,
        rolesText: b.rolesText,
        roleCount: b.roleCount,
        maxTokens: b.maxTokens,
      },
      (e) => {
        if (e.error) set({ error: e.error });
        else if (e.done && e.result) built = e.result;
        else if (e.status === 'sample')
          setBuild({
            running: {
              done: Number(e.done) || 0,
              total: e.total ?? 0,
              condition: e.condition ?? '',
              elapsed: e.elapsed ?? 0,
            },
          });
      },
    );
  } catch (err) {
    fail(err);
  }
  setBuild({ running: null });
  if (built) {
    await refreshAxes();
    selectAxis((built as AxisSummary).id);
  }
  return built;
}

/**
 * A pasted conversation: a line starting `system:`, `user:` or `assistant:` opens
 * a message, and the lines after it continue it. Text before the first marker is
 * the user's.
 */
export function parseConversation(text: string): Message[] {
  const out: Message[] = [];
  for (const line of text.split(/\r?\n/)) {
    const m = /^(system|user|assistant)\s*:\s?(.*)$/i.exec(line);
    if (m) out.push({ role: m[1].toLowerCase() as Message['role'], content: m[2] });
    else if (out.length) out[out.length - 1].content += `\n${line}`;
    else if (line.trim()) out.push({ role: 'user', content: line });
  }
  return out.map((msg) => ({ ...msg, content: msg.content.trim() })).filter((msg) => msg.content);
}

export async function runMonitor(): Promise<void> {
  const ax = current();
  const m = state.monitor;
  if (!ax || m.running) return;
  set({ error: null });
  setMonitor({ running: 'starting', result: null });
  try {
    if (m.source === 'trace') {
      if (!m.traceId) throw new Error('Pick a trace of this model.');
      setMonitor({ result: await getTraceSeries(ax.id, m.traceId) });
    } else {
      const messages = m.source === 'turn' ? [] : parseConversation(m.conversation);
      if (m.source === 'turn' && !m.turnId) throw new Error('Pick a captured agent turn.');
      if (m.source !== 'turn' && !messages.length)
        throw new Error('Paste a conversation: lines starting user:, assistant:, system:.');
      const req =
        m.source === 'turn' ? { axisId: ax.id, turnId: m.turnId } : { axisId: ax.id, messages };
      await monitorAxis(req, (e) => {
        if (e.error) set({ error: e.error });
        else if (e.done && e.result) setMonitor({ result: e.result });
        else if (e.status)
          setMonitor({ running: e.status === 'decode' ? `reading ${e.tokens} tokens` : e.status });
      });
    }
  } catch (err) {
    fail(err);
  }
  setMonitor({ running: null });
}

export async function runCap(): Promise<void> {
  const ax = current();
  const c = state.cap;
  if (!ax || c.running || !c.prompt.trim()) return;
  set({ error: null });
  setCap({ running: 'starting', result: null });
  try {
    await capAxis(
      { axisId: ax.id, system: c.system, prompt: c.prompt, maxTokens: c.maxTokens },
      (e) => {
        if (e.error) set({ error: e.error });
        else if (e.done && e.result) setCap({ result: e.result });
        else if (e.status === 'run' || e.status === 'token')
          setCap({ running: `${e.run} run${e.generated ? ` · ${e.generated} tokens` : ''}` });
      },
    );
  } catch (err) {
    fail(err);
  }
  setCap({ running: null });
}

const pct = (v: number | undefined) => (v === undefined ? '?' : `${Math.round(v * 100)}%`);

/** A line for the suite digest: the axis, its check, the last reading and cap. */
export function axisDigest(s: AxisState = state): string | null {
  const ax = current(s);
  if (!ax) return null;
  const parts = [
    `axis on ${ax.modelName} at L${ax.defaultLayer}: ${ax.check.state.replace('_', ' ')}` +
      (ax.check.tokenAuc != null ? ` (held-out token AUC ${ax.check.tokenAuc})` : '') +
      `, ${ax.roles.length} roles`,
  ];
  const r = s.monitor.result;
  if (r) {
    const sum = 'assistantSummary' in r && r.assistantSummary ? r.assistantSummary : r.summary;
    const drift = 'drift' in r ? r.drift : null;
    parts.push(
      `last reading (${'traceId' in r ? `trace ${r.traceId}` : (r as MonitorResult).source.kind}): ` +
        (drift?.changeInGaps !== undefined
          ? `replies moved ${drift.changeInGaps} of the assistant-to-role gap over ${drift.replies} replies, `
          : '') +
        `mean ${sum.mean}, ${pct(sum.belowFloor)} below the assistant floor, trend ${sum.slopePer100}/100 tokens`,
    );
  }
  const c = s.cap.result;
  if (c) {
    parts.push(
      `cap at L${c.layers.join(',')}: ${c.capped.cappedTokens} of ${c.capped.tokens.filter((t) => t.generated).length} answer tokens floored`,
    );
  }
  return parts.join('; ');
}
