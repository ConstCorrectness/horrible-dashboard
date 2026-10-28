/**
 * The Heads section's state, outside any component: which trace is scored, by
 * which metric, which head is selected — and the scores themselves.
 *
 * Module-level for the reason the suite's other state is: the section is kept
 * mounted behind others, and the agent's digest and `interp.heads` tool read it
 * whether the section is on screen, hidden or never opened.
 */
import { useSyncExternalStore } from 'react';

import {
  getTraceHeads,
  listTraces,
  runHeadsProbe,
  type HeadScore,
  type HeadTable,
  type ModelEntry,
  type TraceSummary,
} from '../../llamacpp/api';
import { sameFile } from '../attention/model';

export type HeadMetric =
  | 'induction'
  | 'prevToken'
  | 'duplicate'
  | 'sink'
  | 'selfWeight'
  | 'entropy';

export const METRICS: { id: HeadMetric; label: string; gloss: string }[] = [
  {
    id: 'induction',
    label: 'Induction',
    gloss:
      'Weight on the token that followed an earlier copy of the current one — copying a continuation forward.',
  },
  {
    id: 'prevToken',
    label: 'Previous token',
    gloss: 'Weight on the position just before. The first half of an induction circuit.',
  },
  {
    id: 'duplicate',
    label: 'Duplicate',
    gloss: 'Weight on earlier copies of the current token itself.',
  },
  { id: 'sink', label: 'Sink', gloss: 'Weight parked on position 0 — the attention sink.' },
  { id: 'selfWeight', label: 'Self', gloss: 'Weight on the query’s own position.' },
  {
    id: 'entropy',
    label: 'Entropy',
    gloss: 'How spread the head’s attention is, in nats — low is sharp, high is diffuse.',
  },
];

/** A head's score on `metric`, or null where the metric is not measurable. */
export function scoreOf(head: HeadScore, metric: HeadMetric): number | null {
  return head[metric];
}

/** Heads ranked by `metric`, highest first; unmeasurable ones are left out. */
export function rankHeads(heads: readonly HeadScore[], metric: HeadMetric, top = 12): HeadScore[] {
  return heads
    .filter((h) => scoreOf(h, metric) !== null)
    .sort((a, b) => (scoreOf(b, metric) as number) - (scoreOf(a, metric) as number))
    .slice(0, top);
}

/** Whether a metric can be read at all from this table. */
export function measurable(table: HeadTable, metric: HeadMetric): boolean {
  if (metric === 'induction' || metric === 'duplicate') return (table.repeatedPositions ?? 0) > 0;
  return Boolean(table.heads?.length);
}

export interface HeadsState {
  traces: TraceSummary[];
  traceId: string | null;
  table: HeadTable | null;
  loading: boolean;
  metric: HeadMetric;
  selected: { layer: number; head: number } | null;
  probe: { running: boolean; status: string; error: string | null };
}

let state: HeadsState = {
  traces: [],
  traceId: null,
  table: null,
  loading: false,
  metric: 'induction',
  selected: null,
  probe: { running: false, status: '', error: null },
};
const listeners = new Set<() => void>();

function set(patch: Partial<HeadsState>): void {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export const headsStore = {
  get: (): HeadsState => state,
  subscribe(l: () => void): () => void {
    listeners.add(l);
    return () => listeners.delete(l);
  },
};

export function useHeads(): HeadsState {
  return useSyncExternalStore(headsStore.subscribe, headsStore.get);
}

export function setMetric(metric: HeadMetric): void {
  set({ metric });
}

export function selectHead(layer: number, head: number): void {
  set({ selected: { layer, head } });
}

/** Traces that captured attention — the only ones a head can be scored on. */
export async function refreshTraces(): Promise<void> {
  try {
    const list = await listTraces();
    const withAttention = list.traces.filter((t) => t.attention);
    set({ traces: withAttention });
    if (!state.traceId && withAttention.length) await openTrace(withAttention[0].traceId);
  } catch {
    // Backend down: keep the list we had.
  }
}

export async function openTrace(traceId: string): Promise<HeadTable | null> {
  set({ traceId, loading: true, selected: null });
  try {
    const table = await getTraceHeads(traceId);
    // A newer open may have started while this one was in flight.
    if (state.traceId === traceId) set({ table, loading: false });
    return table;
  } catch (err) {
    if (state.traceId === traceId) {
      set({ table: { error: err instanceof Error ? err.message : String(err) }, loading: false });
    }
    return null;
  }
}

/** Record the induction probe on `modelPath`, then open its scores. */
export async function runProbe(modelPath: string): Promise<void> {
  if (state.probe.running) return;
  set({ probe: { running: true, status: 'starting the tracer…', error: null } });
  let traceId: string | null = null;
  try {
    await runHeadsProbe(modelPath, (p) => {
      if (typeof p.traceId === 'string') traceId = p.traceId;
      if (p.error) set({ probe: { ...state.probe, error: String(p.error) } });
      else if (p.status) set({ probe: { ...state.probe, status: String(p.status) } });
    });
  } catch (err) {
    set({
      probe: {
        running: false,
        status: '',
        error: err instanceof Error ? err.message : String(err),
      },
    });
    return;
  }
  set({ probe: { running: false, status: '', error: state.probe.error } });
  if (traceId) {
    await refreshTraces();
    await openTrace(traceId);
    set({ metric: 'induction' });
  }
}

/** A compact line for the suite digest: the trace and its strongest head. */
export function headsDigest(current: HeadsState = state): string | null {
  const table = current.table;
  if (!table?.heads?.length) return null;
  const metric: HeadMetric = measurable(table, 'induction') ? 'induction' : 'prevToken';
  const [top] = rankHeads(table.heads, metric, 1);
  const label = METRICS.find((m) => m.id === metric)?.label.toLowerCase() ?? metric;
  return (
    `${table.model ?? 'trace'} ${table.traceId ?? ''} (${table.tokens ?? '?'} tokens` +
    `${(table.repeatedPositions ?? 0) > 0 ? '' : ', no repeats: induction not measurable'}): ` +
    (top
      ? `top ${label} L${top.layer}H${top.head} ${(scoreOf(top, metric) ?? 0).toFixed(2)}`
      : 'no heads')
  );
}

/**
 * Text-decoder families the probe means something on. The catalog lists every
 * GGUF in the model directories — Whisper, an embedding model, TTS, an MTP draft
 * head — and "smallest" over all of them picked a speech model.
 */
const DECODER_FAMILIES = new Set([
  'llama',
  'mistral3',
  'qwen2',
  'qwen3',
  'qwen3moe',
  'qwen35',
  'gemma2',
  'gemma3',
  'gemma4',
  'phi3',
  'lfm2',
  'granite',
  'granitehybrid',
  'smollm3',
]);

/**
 * The model the probe offers first: the one the shown trace was recorded on, so
 * "run the probe" answers the question about *this* model; then the file the
 * attention layout describes (the agent's model); failing both, the smallest real
 * decoder, because the tracer runs on the CPU and a 27B probe is minutes, not
 * seconds. The catalog's own order is by directory, which put a fine-tune first.
 */
export function defaultProbeModel(
  models: ModelEntry[],
  tracePath?: string,
  layoutPath?: string,
): string {
  // Two passes, not one `find` with `||`: that returns whichever matching entry the
  // catalog lists first, so the layout's model could beat the trace's own.
  const same =
    models.find((m) => sameFile(m.path, tracePath)) ??
    models.find((m) => sameFile(m.path, layoutPath));
  if (same) return same.path;
  const decoders = models.filter(
    (m) => DECODER_FAMILIES.has(m.architecture) && (m.parameters ?? 0) >= 100e6,
  );
  const smallest = [...decoders].sort((a, b) => a.sizeBytes - b.sizeBytes)[0];
  return smallest?.path ?? '';
}
