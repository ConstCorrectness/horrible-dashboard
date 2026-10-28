/**
 * The NLA section's state, outside any component: the pair and its files, the
 * readout in view, an explanation run in flight, and edited explanations scored.
 *
 * Module-level like the suite's other stores: the digest and `interp.nla` read it,
 * and a run — three 4–8 GB models loaded in turn, tens of seconds per position on
 * the CPU — keeps streaming while the section is hidden behind another.
 */
import { useSyncExternalStore } from 'react';

import { parseConversation } from '../axis/state';
import {
  explain,
  getNla,
  getReadout,
  scoreExplanation,
  type NlaDefaults,
  type NlaPair,
  type Readout,
  type ReadoutRow,
  type ScoreResult,
} from './api';

export type NlaSource = 'text' | 'conversation' | 'turn' | 'trace';

export interface NlaState {
  pairs: NlaPair[];
  defaults: NlaDefaults | null;
  pairId: string;
  readouts: ReadoutRow[];
  readout: Readout | null;
  /** The explained position in view. */
  position: number | null;
  form: {
    source: NlaSource;
    text: string;
    conversation: string;
    turnId: string;
    traceId: string;
    /** Comma-separated positions ("-1, 12"); empty explains the last `limit`. */
    positions: string;
    limit: number;
    samples: number;
    maxTokens: number;
    temperature: number;
  };
  running: { phase: string; done: number; total: number; latest: string } | null;
  /** Edited explanations scored against the original vector, by position. */
  edits: Record<number, { text: string; running: boolean; result: ScoreResult | null }>;
  error: string | null;
}

let state: NlaState = {
  pairs: [],
  defaults: null,
  pairId: 'qwen2.5-7b-L20',
  readouts: [],
  readout: null,
  position: null,
  form: {
    source: 'text',
    text: 'The Eiffel Tower is in the city of Paris, and the Colosseum is in the city of',
    conversation: '',
    turnId: '',
    traceId: '',
    positions: '',
    limit: 3,
    samples: 1,
    maxTokens: 160,
    temperature: 1,
  },
  running: null,
  edits: {},
  error: null,
};
const listeners = new Set<() => void>();

function set(patch: Partial<NlaState>): void {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export const nlaStore = {
  get: (): NlaState => state,
  subscribe(l: () => void): () => void {
    listeners.add(l);
    return () => listeners.delete(l);
  },
};

export function useNla(): NlaState {
  return useSyncExternalStore(nlaStore.subscribe, nlaStore.get);
}

export function currentPair(s: NlaState = state): NlaPair | null {
  return s.pairs.find((p) => p.id === s.pairId) ?? null;
}

export function setForm(patch: Partial<NlaState['form']>): void {
  set({ form: { ...state.form, ...patch } });
}

export function selectPosition(position: number | null): void {
  set({ position });
}

function fail(err: unknown): void {
  set({ error: err instanceof Error ? err.message : String(err) });
}

export async function refreshNla(): Promise<void> {
  try {
    const { pairs, readouts, defaults } = await getNla();
    const keep = pairs.some((p) => p.id === state.pairId);
    set({ pairs, readouts, defaults, pairId: keep ? state.pairId : (pairs[0]?.id ?? '') });
    if (!state.readout && readouts[0]) await openReadout(readouts[0].id);
  } catch (err) {
    fail(err);
  }
}

export async function openReadout(id: string): Promise<void> {
  try {
    const readout = await getReadout(id);
    set({ readout, position: readout.positions[0]?.position ?? null, edits: {} });
  } catch (err) {
    fail(err);
  }
}

/** "-1, 12, 3" → [-1, 12, 3]; anything not an integer is dropped. */
export function parsePositions(text: string): number[] {
  return text
    .split(/[\s,]+/)
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isInteger(n));
}

const PHASES: Record<string, string> = {
  base: 'reading the activations',
  av: 'explaining',
  ar: 'reconstructing',
};

export async function run(positions?: number[]): Promise<Readout | null> {
  const f = state.form;
  if (state.running) return null;
  const req: Parameters<typeof explain>[0] = {
    pairId: state.pairId,
    positions: positions ?? parsePositions(f.positions),
    limit: f.limit,
    samples: f.samples,
    maxTokens: f.maxTokens,
    temperature: f.temperature,
  };
  if (f.source === 'text') {
    if (!f.text.trim()) return (fail('Type some text to read.'), null);
    req.text = f.text;
  } else if (f.source === 'conversation') {
    const messages = parseConversation(f.conversation);
    if (!messages.length)
      return (fail('Paste a conversation: lines starting user:, assistant:, system:.'), null);
    req.messages = messages;
  } else if (f.source === 'turn') {
    if (!f.turnId) return (fail('Pick a captured agent turn.'), null);
    req.turnId = f.turnId;
  } else {
    if (!f.traceId) return (fail('Pick a trace of the base model.'), null);
    req.traceId = f.traceId;
  }
  set({ error: null, running: { phase: 'starting', done: 0, total: 0, latest: '' } });
  let done: Readout | null = null;
  try {
    await explain(req, (e) => {
      const r = state.running ?? { phase: '', done: 0, total: 0, latest: '' };
      if (e.error) fail(e.error);
      else if (e.done === true && e.result) done = e.result;
      else if (e.status === 'loading')
        set({ running: { ...r, phase: `loading ${e.model?.toUpperCase()}` } });
      else if (e.status === 'decode')
        set({ running: { ...r, phase: `${PHASES.base} · ${e.tokens} tokens` } });
      else if (e.status === 'explain')
        set({
          running: {
            phase: PHASES.av,
            done: Number(e.done) || 0,
            total: e.total ?? 0,
            latest: e.explanation ?? '',
          },
        });
      else if (e.status === 'score')
        set({
          running: { ...r, phase: PHASES.ar, done: Number(e.done) || 0, total: e.total ?? 0 },
        });
      else if (e.status === 'strip')
        set({ running: { ...r, phase: 'preparing the AR for llama.cpp (once)' } });
    });
  } catch (err) {
    fail(err);
  }
  set({ running: null });
  if (done) {
    const readout = done as Readout;
    set({ readout, position: readout.positions[0]?.position ?? null, edits: {} });
    void getNla()
      .then(({ readouts }) => set({ readouts }))
      .catch(() => undefined);
  }
  return done;
}

export function setEdit(position: number, text: string): void {
  const prev = state.edits[position];
  set({
    edits: { ...state.edits, [position]: { text, running: false, result: prev?.result ?? null } },
  });
}

/** Score an edited explanation against the original vector at `position`. */
export async function scoreEdit(position: number): Promise<void> {
  const r = state.readout;
  const edit = state.edits[position];
  if (!r || !edit?.text.trim() || edit.running) return;
  const put = (patch: Partial<NlaState['edits'][number]>) =>
    set({ edits: { ...state.edits, [position]: { ...state.edits[position], ...patch } } });
  put({ running: true, result: null });
  set({ error: null });
  try {
    await scoreExplanation({ readoutId: r.id, position, explanation: edit.text }, (e) => {
      if (e.error) fail(e.error);
      else if (e.done === true && e.result) put({ result: e.result });
    });
  } catch (err) {
    fail(err);
  }
  put({ running: false });
}

const fmt = (v: number | null | undefined) => (v == null ? '?' : v.toFixed(2));

/** A line for the suite digest: the pair, and the readout in view. */
export function nlaDigest(s: NlaState = state): string | null {
  const pair = currentPair(s);
  const r = s.readout;
  if (!pair || (!pair.ready && !r)) return null;
  const parts = [`NLA ${pair.label}: ${pair.ready ? 'ready' : 'files missing'}`];
  if (r) {
    const sum = r.summary;
    parts.push(
      `readout of ${r.source.kind} (${sum.positions} positions): mean cos ${fmt(sum.meanCos)} vs ` +
        `neutral ${fmt(sum.meanNullCos)}, ${sum.carried} carried / ${sum.partial} partial / ` +
        `${sum.drifted} drifted`,
    );
    const p = r.positions.find((x) => x.position === s.position) ?? r.positions[0];
    if (p?.best)
      parts.push(
        `at ${p.position} "${p.token.trim()}": ${p.verdict} (cos ${fmt(p.best.cos)}) — ` +
          `${p.best.explanation.replace(/\s+/g, ' ').slice(0, 140)}`,
      );
  }
  return parts.join('; ');
}
