/**
 * The Neurons section's state, outside any component — the suite's store pattern
 * (`steer/state.ts`): a comparison keeps streaming while the section is hidden
 * behind another, and the recipe survives the section unmounting.
 *
 * The model is the window's WebML engine's: whatever GGUF is loaded there. The
 * recipe, selection and prompt are kept per model file in localStorage (a viewer
 * convenience; a recipe that matters is exported).
 */
import { useSyncExternalStore } from 'react';

import { webmlEngine } from '../../webml/engine';
import { layerHeat, topNeurons, type TopNeuron } from './heat';
import {
  exportRecipe,
  influence,
  MAX_DOSE,
  MAX_EDITS,
  parseRecipe,
  RecipeHistory,
  toEngineEdits,
  validateRecipe,
  type EditMode,
  type EngineEdit,
  type NeuronEdit,
} from './recipe';

export interface NeuronModel {
  /** The engine's model id (`gguf:…`, `gguf-node:…`). */
  id: string;
  /** The file's name, what a recipe is keyed by. */
  file: string;
  layers: number;
  ffn: number;
  arch: string;
  thinking: boolean;
}

export interface Branch {
  text: string;
  /** The reply's tokens as generated, for clicking one. */
  tokens: string[];
  done: boolean;
  /** The engine edits this reply ran under; a capture replays with the same. */
  edits: EngineEdit[];
}

export interface Comparison {
  prompt: string;
  baseline: Branch;
  steered: Branch;
  /** The recipe as it was run, for the summary line. */
  recipe: NeuronEdit[];
}

export interface Capture {
  branch: 'baseline' | 'steered';
  index: number;
  input: string;
  predicted: string;
  activations: Float32Array;
  layers: number;
  ffn: number;
}

export interface NeuronState {
  model: NeuronModel | null;
  selection: { layer: number; neuron: number };
  recipe: NeuronEdit[];
  prompt: string;
  maxTokens: number;
  comparison: Comparison | null;
  /** What the engine is doing for this section, for the status line. */
  busy: string | null;
  capture: Capture | null;
  top: TopNeuron[];
  /** Restrict the top-neurons list to the selected layer. */
  topThisLayer: boolean;
  medians: number[] | null;
  error: string | null;
  canUndo: boolean;
}

const DEFAULT_MAX_TOKENS = 128;

let state: NeuronState = {
  model: null,
  selection: { layer: 0, neuron: 0 },
  recipe: [],
  prompt: '',
  maxTokens: DEFAULT_MAX_TOKENS,
  comparison: null,
  busy: null,
  capture: null,
  top: [],
  topThisLayer: false,
  medians: null,
  error: null,
  canUndo: false,
};
const listeners = new Set<() => void>();
const history = new RecipeHistory();
/** Down-column norms, per model id, `layer:neuron` → norm. */
const columnCache = new Map<string, Map<string, number>>();
const medianCache = new Map<string, number[]>();

function set(patch: Partial<NeuronState>): void {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export const neuronStore = {
  get: (): NeuronState => state,
  subscribe(l: () => void): () => void {
    listeners.add(l);
    return () => listeners.delete(l);
  },
};

export function useNeurons(): NeuronState {
  return useSyncExternalStore(neuronStore.subscribe, neuronStore.get);
}

// ── persistence (per viewer, per model file) ────────────────────────────────

const storeKey = (file: string) => `interp.neurons.v1:${file}`;

function persist(): void {
  const m = state.model;
  if (!m) return;
  try {
    localStorage.setItem(
      storeKey(m.file),
      JSON.stringify({
        recipe: state.recipe,
        selection: state.selection,
        prompt: state.prompt,
        maxTokens: state.maxTokens,
      }),
    );
  } catch {
    // a convenience; export is the durable path
  }
}

function restore(
  m: NeuronModel,
): Pick<NeuronState, 'recipe' | 'selection' | 'prompt' | 'maxTokens'> {
  const fresh = {
    recipe: [],
    selection: { layer: Math.floor(m.layers / 2), neuron: 0 },
    prompt: '',
    maxTokens: DEFAULT_MAX_TOKENS,
  };
  try {
    const raw = JSON.parse(localStorage.getItem(storeKey(m.file)) ?? 'null');
    if (!raw) return fresh;
    const recipe = [...validateRecipe(raw.recipe ?? [], m)];
    const sel = raw.selection;
    const selection =
      sel &&
      Number.isInteger(sel.layer) &&
      sel.layer >= 0 &&
      sel.layer < m.layers &&
      Number.isInteger(sel.neuron) &&
      sel.neuron >= 0 &&
      sel.neuron < m.ffn
        ? { layer: sel.layer, neuron: sel.neuron }
        : fresh.selection;
    return {
      recipe,
      selection,
      prompt: typeof raw.prompt === 'string' ? raw.prompt.slice(0, 20000) : '',
      maxTokens:
        Number.isInteger(raw.maxTokens) && raw.maxTokens >= 1 && raw.maxTokens <= 4096
          ? raw.maxTokens
          : DEFAULT_MAX_TOKENS,
    };
  } catch {
    return fresh;
  }
}

/** The file part of an engine model id. */
export function modelFile(id: string): string {
  return id.split(/[\\/:]/).pop() || id;
}

/** Follow the engine: a new model gets its own recipe, and clears the old results. */
export function syncModel(next: NeuronModel | null): void {
  if (next?.id === state.model?.id && next?.layers === state.model?.layers) return;
  history.clear();
  if (!next) {
    set({ model: null, comparison: null, capture: null, top: [], medians: null, canUndo: false });
    return;
  }
  set({
    model: next,
    ...restore(next),
    comparison: null,
    capture: null,
    top: [],
    medians: medianCache.get(next.id) ?? null,
    error: null,
    canUndo: false,
  });
}

// ── selection and recipe ────────────────────────────────────────────────────

export function select(layer: number, neuron: number): void {
  const m = state.model;
  if (!m) return;
  const l = Math.max(0, Math.min(m.layers - 1, layer));
  const n = Math.max(0, Math.min(m.ffn - 1, neuron));
  set({ selection: { layer: l, neuron: n } });
  if (state.capture) refreshTop();
  persist();
}

const keyOf = (e: { layer: number; neuron: number }) => `${e.layer}:${e.neuron}`;

export function editAt(layer: number, neuron: number): NeuronEdit | undefined {
  return state.recipe.find((e) => e.layer === layer && e.neuron === neuron);
}

function commit(recipe: NeuronEdit[], record = true): void {
  if (record) history.record(state.recipe);
  set({ recipe, canUndo: history.canUndo, error: null });
  persist();
}

/** Begin a run of dose changes (a slider drag) as one undo step. */
export function beginDose(): void {
  history.record(state.recipe);
  set({ canUndo: true });
}

/** Add the selected neuron (dose 0, additive) if it is not in the recipe yet. */
export function addSelected(dose = 0): void {
  const { layer, neuron } = state.selection;
  if (editAt(layer, neuron)) return;
  if (state.recipe.length >= MAX_EDITS) {
    set({ error: `A recipe holds at most ${MAX_EDITS} neurons.` });
    return;
  }
  commit([...state.recipe, { layer, neuron, dose, mode: 'add', enabled: true }]);
}

/**
 * Set a neuron's dose, adding it when absent. `record: false` while a slider is
 * dragged (the drag began with `beginDose`).
 */
export function setDose(layer: number, neuron: number, dose: number, record = true): void {
  const clamped = Math.max(-MAX_DOSE, Math.min(MAX_DOSE, Math.round(dose * 100) / 100));
  const existing = editAt(layer, neuron);
  if (!existing) {
    if (state.recipe.length >= MAX_EDITS) {
      set({ error: `A recipe holds at most ${MAX_EDITS} neurons.` });
      return;
    }
    commit([...state.recipe, { layer, neuron, dose: clamped, mode: 'add', enabled: true }], record);
    return;
  }
  commit(
    state.recipe.map((e) => (e === existing ? { ...e, dose: clamped } : e)),
    record,
  );
}

export function setMode(layer: number, neuron: number, mode: EditMode): void {
  commit(
    state.recipe.map((e) =>
      e.layer === layer && e.neuron === neuron
        ? // Switching keeps "no change" meaning no change: add 0 ↔ mul 1.
          {
            ...e,
            mode,
            dose: e.dose === (e.mode === 'add' ? 0 : 1) ? (mode === 'add' ? 0 : 1) : e.dose,
          }
        : e,
    ),
  );
}

export function toggleEnabled(layer: number, neuron: number): void {
  commit(
    state.recipe.map((e) =>
      e.layer === layer && e.neuron === neuron ? { ...e, enabled: !e.enabled } : e,
    ),
  );
}

export function removeEdit(layer: number, neuron: number): void {
  commit(state.recipe.filter((e) => !(e.layer === layer && e.neuron === neuron)));
}

export function clearRecipe(): void {
  if (!state.recipe.length) return;
  commit([]);
}

export function undo(): void {
  const prev = history.undo(state.recipe);
  if (!prev) return;
  set({ recipe: prev, canUndo: history.canUndo });
  persist();
}

export function setPrompt(prompt: string): void {
  set({ prompt });
  persist();
}

export function setMaxTokens(maxTokens: number): void {
  if (!Number.isInteger(maxTokens) || maxTokens < 1 || maxTokens > 4096) return;
  set({ maxTokens });
  persist();
}

export function setTopThisLayer(on: boolean): void {
  set({ topThisLayer: on });
  refreshTop();
}

export function clearError(): void {
  set({ error: null });
}

/** The recipe's active edits, as the steered panel's summary line spells them. */
export function summary(recipe: readonly NeuronEdit[]): string {
  const live = recipe.filter((e) => e.enabled && influence(e) !== 0);
  if (!live.length) return 'No intervention';
  return live
    .map(
      (e) =>
        `L${e.layer}·N${e.neuron} ${e.mode === 'mul' ? `×${e.dose}` : `${e.dose > 0 ? '+' : ''}${e.dose}`}`,
    )
    .join('  ');
}

// ── import / export ─────────────────────────────────────────────────────────

export function exportJson(): string | null {
  const m = state.model;
  if (!m) return null;
  return JSON.stringify(
    exportRecipe(state.recipe, { file: m.file, layers: m.layers, ffn: m.ffn }),
    null,
    2,
  );
}

export function importJson(text: string): void {
  const m = state.model;
  if (!m) return;
  try {
    const recipe = parseRecipe(text, { file: m.file, layers: m.layers, ffn: m.ffn });
    commit(recipe);
  } catch (err) {
    set({ error: err instanceof Error ? err.message : String(err) });
  }
}

// ── the engine ──────────────────────────────────────────────────────────────

function columns(modelId: string): Map<string, number> {
  let m = columnCache.get(modelId);
  if (!m) columnCache.set(modelId, (m = new Map()));
  return m;
}

/** The recipe in engine terms: fetches medians and down-column norms it lacks. */
async function engineEdits(recipe: readonly NeuronEdit[]): Promise<EngineEdit[]> {
  const m = state.model!;
  const engine = webmlEngine();
  const additive = recipe.filter((e) => e.enabled && e.mode === 'add' && e.dose !== 0);
  if (additive.length) {
    if (!state.medians) {
      set({ busy: 'Measuring each layer’s residual stream…' });
      const r = await engine.neurons({ op: 'medians' });
      medianCache.set(m.id, r.medians);
      set({ medians: r.medians });
    }
    const cache = columns(m.id);
    const missing = additive.filter((e) => !cache.has(keyOf(e)));
    if (missing.length) {
      set({ busy: 'Reading down-projection columns…' });
      const r = await engine.neurons({
        op: 'columns',
        neurons: missing.map((e) => ({ layer: e.layer, index: e.neuron })),
      });
      missing.forEach((e, i) => cache.set(keyOf(e), r.norms[i]));
    }
  }
  const cache = columns(m.id);
  return toEngineEdits(
    recipe,
    state.medians ?? [],
    (layer, neuron) => cache.get(`${layer}:${neuron}`) ?? 0,
  );
}

const emptyBranch = (edits: EngineEdit[] = []): Branch => ({
  text: '',
  tokens: [],
  done: false,
  edits,
});

let abort: AbortController | null = null;

function messages(prompt: string) {
  return [{ role: 'user' as const, content: prompt }];
}

function templateKwargs(): Record<string, unknown> | undefined {
  return state.model?.thinking ? { enable_thinking: false } : undefined;
}

/**
 * Baseline then steered, both greedy from the same prompt, so the recipe is the
 * only difference. With nothing active the steered reply is the baseline itself.
 */
export async function runComparison(): Promise<void> {
  const m = state.model;
  const prompt = state.prompt.trim();
  if (!m || !prompt || state.busy) return;
  const engine = webmlEngine();
  const recipe = state.recipe.map((e) => ({ ...e }));
  abort = new AbortController();
  const signal = abort.signal;
  set({ error: null, capture: null, top: [] });
  try {
    const edits = await engineEdits(recipe);
    const comparison: Comparison = {
      prompt,
      baseline: emptyBranch([]),
      steered: emptyBranch(edits),
      recipe,
    };
    set({ comparison, busy: 'Writing the original reply…' });
    const branch = async (which: 'baseline' | 'steered', neuronEdits: EngineEdit[]) => {
      const update = (patch: Partial<Branch>) => {
        const c = state.comparison;
        if (!c) return;
        set({ comparison: { ...c, [which]: { ...c[which], ...patch } } });
      };
      const tokens: string[] = [];
      const result = await engine.generate(
        {
          messages: messages(prompt),
          temperature: 0,
          maxNewTokens: state.maxTokens,
          topK: 1,
          templateKwargs: templateKwargs(),
          neuronEdits,
        },
        {
          signal,
          onStep: (s) => {
            tokens.push(s.token);
            update({ tokens: [...tokens], text: tokens.join('') });
          },
        },
      );
      update({ text: result.text, tokens: [...tokens], done: true });
    };
    await branch('baseline', []);
    if (signal.aborted) return;
    if (!edits.length) {
      const c = state.comparison!;
      set({ comparison: { ...c, steered: { ...c.baseline, edits: [] } } });
      return;
    }
    set({ busy: 'Writing the steered reply…' });
    await branch('steered', edits);
  } catch (err) {
    set({ error: err instanceof Error ? err.message : String(err) });
  } finally {
    abort = null;
    set({ busy: null });
  }
}

export function stopComparison(): void {
  abort?.abort();
}

/**
 * Every layer's activation where `branch`'s reply token `index` was predicted, by
 * replaying the prompt and reply under the edits that reply ran with.
 */
export async function captureAt(branch: 'baseline' | 'steered', index: number): Promise<void> {
  const c = state.comparison;
  const m = state.model;
  if (!c || !m || state.busy) return;
  const b = c[branch];
  set({ busy: `Reading activations at token ${index}…`, error: null });
  try {
    const r = await webmlEngine().neurons({
      op: 'capture',
      messages: messages(c.prompt),
      templateKwargs: templateKwargs(),
      reply: b.text,
      index,
      neuronEdits: b.edits,
    });
    set({
      capture: {
        branch,
        index,
        input: r.input,
        predicted: r.predicted,
        activations: r.activations,
        layers: r.layers,
        ffn: r.ffn,
      },
    });
    refreshTop();
  } catch (err) {
    set({ error: err instanceof Error ? err.message : String(err) });
  } finally {
    set({ busy: null });
  }
}

export function clearCapture(): void {
  set({ capture: null, top: [] });
}

function refreshTop(): void {
  const cap = state.capture;
  if (!cap) return;
  const top = topNeurons(cap.activations, cap.layers, cap.ffn, 24, {
    layer: state.topThisLayer ? state.selection.layer : undefined,
  });
  set({ top });
}

/** Each layer's heat for the scene: signed, normalized per layer. */
export function heatOf(cap: Capture, layer: number): Float32Array {
  return layerHeat(cap.activations, layer, cap.ffn);
}

/** The raw activation of one neuron in the capture, for the selection readout. */
export function activationOf(cap: Capture | null, layer: number, neuron: number): number | null {
  if (!cap || layer >= cap.layers || neuron >= cap.ffn) return null;
  return cap.activations[layer * cap.ffn + neuron];
}
