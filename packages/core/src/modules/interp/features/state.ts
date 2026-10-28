/**
 * The Features section's state, outside any component: which trace and SAE, the
 * encoding and its self-check, the selected token and feature, and the
 * Neuronpedia cards and label checks fetched so far.
 *
 * Module-level like the suite's other stores, so the digest and `interp.features`
 * answer whether the section is open, hidden or never visited.
 */
import { useSyncExternalStore } from 'react';

import { setModelLocus } from '../../../model-locus';
import { getFeature, type FeatureCard } from '../library/api';
import {
  getFeatures,
  getFeatureTraces,
  getFeatureValues,
  getSaes,
  labelCheck,
  type FeatureEncoding,
  type FeatureTrace,
  type FeatureValues,
  type LabelCheck,
  type SaeRow,
} from './api';

/** Labels fetched up front for the trace's top features — a card each, cached a
 *  week by the backend, so this is a handful of GETs once per feature. */
const LABEL_PREFETCH = 12;

export interface FeaturesState {
  traces: FeatureTrace[];
  traceId: string | null;
  saes: SaeRow[];
  artifactId: string | null;
  result: FeatureEncoding | null;
  loading: boolean;
  position: number | null;
  feature: number | null;
  values: FeatureValues | null;
  /** Neuronpedia cards by feature index, for the current SAE. */
  cards: Record<number, FeatureCard>;
  /** Label checks by `artifactId:index`. */
  checks: Record<string, LabelCheck | 'running'>;
  error: string | null;
}

const initial: FeaturesState = {
  traces: [],
  traceId: null,
  saes: [],
  artifactId: null,
  result: null,
  loading: false,
  position: null,
  feature: null,
  values: null,
  cards: {},
  checks: {},
  error: null,
};
let state: FeaturesState = initial;
const listeners = new Set<() => void>();

function set(patch: Partial<FeaturesState>): void {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export const featuresStore = {
  get: (): FeaturesState => state,
  subscribe(l: () => void): () => void {
    listeners.add(l);
    return () => listeners.delete(l);
  },
};

export function useFeatures(): FeaturesState {
  return useSyncExternalStore(featuresStore.subscribe, featuresStore.get);
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function checkKey(artifactId: string, index: number): string {
  return `${artifactId}:${index}`;
}

/** The trace's strongest feature that is not a first-token one, else the strongest. */
export function leadFeature(result: FeatureEncoding): FeatureEncoding['top'][number] | null {
  return result.top.find((f) => !f.firstToken) ?? result.top[0] ?? null;
}

// ── loading ──────────────────────────────────────────────────────────────────

export async function refreshFeatureTraces(): Promise<void> {
  try {
    const { traces } = await getFeatureTraces();
    set({ traces, error: null });
    if (!state.traceId) {
      const first = traces.find((t) => t.fits.length > 0);
      if (first) await openTrace(first.traceId);
    }
  } catch (err) {
    set({ error: message(err) });
  }
}

export async function openTrace(traceId: string): Promise<void> {
  set({ traceId, result: null, position: null, feature: null, values: null });
  // A trace made since the list loaded (a steered run): without it in the list the
  // picker would show another trace's name over this one's features.
  if (!state.traces.some((t) => t.traceId === traceId)) {
    void getFeatureTraces()
      .then(({ traces }) => set({ traces }))
      .catch(() => undefined);
  }
  try {
    const { saes } = await getSaes(traceId);
    if (state.traceId !== traceId) return;
    const usable = saes.filter((s) => s.compat.state !== 'incompatible');
    const keep = usable.find((s) => s.id === state.artifactId);
    const pick = keep ?? usable.find((s) => s.compat.state === 'compatible') ?? usable[0];
    set({ saes, artifactId: pick?.id ?? null });
    if (pick) await encode();
  } catch (err) {
    set({ error: message(err) });
  }
}

export async function chooseSae(artifactId: string): Promise<void> {
  if (artifactId === state.artifactId) return;
  set({ artifactId, cards: {}, result: null, feature: null, values: null });
  await encode();
}

export async function encode(): Promise<FeatureEncoding | null> {
  const { traceId, artifactId } = state;
  if (!traceId || !artifactId) return null;
  set({ loading: true, error: null });
  try {
    const result = await getFeatures(traceId, artifactId);
    if (state.traceId !== traceId || state.artifactId !== artifactId) return null;
    if (result.error) {
      set({ result: null, loading: false, error: result.error });
      return null;
    }
    set({ result, loading: false });
    const lead = leadFeature(result);
    if (lead) {
      selectPosition(lead.position, false);
      void selectFeature(lead.index);
    }
    void prefetchLabels(result);
    return result;
  } catch (err) {
    set({ loading: false, error: message(err) });
    return null;
  }
}

async function prefetchLabels(result: FeatureEncoding): Promise<void> {
  const np = result.neuronpedia;
  if (!np) return;
  const wanted = result.top.slice(0, LABEL_PREFETCH).map((f) => f.index);
  // Four at a time: a polite rate for a public API, and the first labels land fast.
  for (let i = 0; i < wanted.length; i += 4) {
    await Promise.all(wanted.slice(i, i + 4).map((index) => loadCard(index)));
  }
}

/** Cards being fetched, by `source:index` — the prefetch, the token row and the
 *  selected feature often ask for the same one at once. */
const inflight = new Map<string, Promise<FeatureCard | null>>();

export function loadCard(index: number): Promise<FeatureCard | null> {
  const np = state.result?.neuronpedia;
  if (!np) return Promise.resolve(null);
  if (state.cards[index]) return Promise.resolve(state.cards[index]);
  const key = `${np.model}/${np.source}:${index}`;
  const pending = inflight.get(key);
  if (pending) return pending;
  const request = getFeature(np.model, np.source, String(index))
    .then((card) => {
      set({ cards: { ...state.cards, [index]: card } });
      return card;
    })
    .catch((err: unknown) => {
      set({ cards: { ...state.cards, [index]: { error: message(err) } } });
      return null;
    })
    .finally(() => inflight.delete(key));
  inflight.set(key, request);
  return request;
}

// ── selection ────────────────────────────────────────────────────────────────

/** Select a token. `publish` moves the shared locus so the other tools follow. */
export function selectPosition(position: number, publish = true): void {
  set({ position });
  // The features at this token get their labels too, not just the trace's top.
  const row = state.result?.perToken.find((p) => p.position === position);
  if (row) void Promise.all(row.top.map((f) => loadCard(f.index)));
  if (publish && state.result) {
    setModelLocus(
      { traceId: state.result.traceId, position, layer: state.result.layer },
      'features',
    );
  }
}

export async function selectFeature(index: number): Promise<void> {
  const { traceId, artifactId } = state;
  set({ feature: index, values: null });
  if (!traceId || !artifactId) return;
  void loadCard(index);
  try {
    const values = await getFeatureValues(traceId, artifactId, index);
    if (state.feature === index) set({ values });
  } catch (err) {
    if (state.feature === index) set({ error: message(err) });
  }
}

export async function runLabelCheck(index: number): Promise<LabelCheck | null> {
  const { traceId, artifactId } = state;
  if (!traceId || !artifactId) return null;
  const key = checkKey(artifactId, index);
  if (state.checks[key] === 'running') return null;
  set({ checks: { ...state.checks, [key]: 'running' } });
  let outcome: LabelCheck;
  try {
    outcome = await labelCheck(traceId, artifactId, index);
  } catch (err) {
    outcome = { state: 'failed', reason: message(err) };
  }
  set({ checks: { ...state.checks, [key]: outcome } });
  return outcome;
}

// ── the digest ───────────────────────────────────────────────────────────────

/** The label Neuronpedia gives a feature, if its card is loaded. */
export function labelOf(cards: Record<number, FeatureCard>, index: number): string | null {
  return cards[index]?.explanations?.[0]?.text ?? null;
}

/** A line for the suite digest: the SAE, its verdict on this trace, the lead feature. */
export function featuresDigest(current: FeaturesState = state): string | null {
  const r = current.result;
  if (!r) return null;
  const c = r.check;
  const verdict =
    c.state === 'not_checked'
      ? 'reconstruction not checked'
      : `${c.state} (FVU ${c.fvu?.toFixed(2)}, L0 ${c.l0 ?? '?'}${
          c.expectedL0 ? `/${c.expectedL0}` : ''
        })`;
  const lead = leadFeature(r);
  const tok = (p: number) => r.tokens.find((t) => t.position === p)?.text ?? `#${p}`;
  const label = lead ? labelOf(current.cards, lead.index) : null;
  return (
    `SAE L${r.layer} ${Math.round(r.width / 1024)}k on ${r.model ?? 'trace'} ${r.traceId}: ${verdict}` +
    (lead
      ? `; lead F${lead.index}${label ? ` "${label}"` : ''} ${lead.peak.toFixed(0)} at "${tok(lead.position)}"`
      : '')
  );
}
