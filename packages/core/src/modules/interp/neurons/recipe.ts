/**
 * A steering recipe: a short list of neuron edits applied to the MLP activation right
 * before `down_proj`, on every token. `mul` scales the activation; `add` adds an
 * offset whose `dose` is in units of "the layer's typical residual norm", converted
 * to activation units by dividing by that neuron's down_proj column norm — so dose 1
 * on any neuron moves the residual stream by about one median residual norm.
 */

export type EditMode = 'add' | 'mul';

export interface NeuronEdit {
  layer: number;
  neuron: number;
  dose: number;
  mode: EditMode;
  enabled: boolean;
}

/** What the engine applies: `act = act * scale + offset` at (layer, index). */
export interface EngineEdit {
  layer: number;
  index: number;
  scale: number;
  offset: number;
}

export interface ModelShape {
  layers: number;
  ffn: number;
}

export interface RecipeModel extends ModelShape {
  /** The GGUF file the recipe was made on; recipes do not transfer between models. */
  file: string;
}

export const MAX_EDITS = 32;
export const MAX_DOSE = 3;
export const RECIPE_SCHEMA_VERSION = 1;
export const RECIPE_SITE = 'mlp.down_proj.input';
export const RECIPE_NORMALIZATION = 'layer_residual_median_over_down_column_l2';

export interface RecipeFile {
  schemaVersion: typeof RECIPE_SCHEMA_VERSION;
  model: RecipeModel;
  site: typeof RECIPE_SITE;
  normalization: typeof RECIPE_NORMALIZATION;
  edits: NeuronEdit[];
}

const keyOf = (e: Pick<NeuronEdit, 'layer' | 'neuron'>): string => `${e.layer}:${e.neuron}`;

/** Throws a descriptive error for the first problem found; returns the edits otherwise. */
export function validateRecipe(
  edits: readonly NeuronEdit[],
  shape: ModelShape,
): readonly NeuronEdit[] {
  if (!Array.isArray(edits)) throw new Error('recipe: edits must be an array');
  if (edits.length > MAX_EDITS)
    throw new Error(`recipe: ${edits.length} edits, at most ${MAX_EDITS} allowed`);
  const seen = new Set<string>();
  edits.forEach((e, i) => {
    const at = `recipe edit ${i}`;
    if (typeof e !== 'object' || e === null) throw new Error(`${at}: not an object`);
    if (!Number.isInteger(e.layer) || e.layer < 0 || e.layer >= shape.layers) {
      throw new Error(
        `${at}: layer ${String(e.layer)} is not an integer in [0, ${shape.layers - 1}]`,
      );
    }
    if (!Number.isInteger(e.neuron) || e.neuron < 0 || e.neuron >= shape.ffn) {
      throw new Error(
        `${at}: neuron ${String(e.neuron)} is not an integer in [0, ${shape.ffn - 1}]`,
      );
    }
    if (typeof e.dose !== 'number' || !Number.isFinite(e.dose) || Math.abs(e.dose) > MAX_DOSE) {
      throw new Error(
        `${at}: dose ${String(e.dose)} is not a finite number in [-${MAX_DOSE}, ${MAX_DOSE}]`,
      );
    }
    if (e.mode !== 'add' && e.mode !== 'mul')
      throw new Error(`${at}: mode ${String(e.mode)} is not 'add' or 'mul'`);
    if (typeof e.enabled !== 'boolean') throw new Error(`${at}: enabled must be a boolean`);
    const key = keyOf(e);
    if (seen.has(key))
      throw new Error(`${at}: duplicate edit for layer ${e.layer} neuron ${e.neuron}`);
    seen.add(key);
  });
  return edits;
}

/** How far an edit moves its neuron from "untouched": 0 means no effect. */
export function influence(e: Pick<NeuronEdit, 'dose' | 'mode'>): number {
  return e.mode === 'mul' ? e.dose - 1 : e.dose;
}

/**
 * Resolve enabled edits into engine terms. `residualMedians[layer]` is the layer's
 * median residual L2 norm; `colNorm(layer, neuron)` the L2 norm of that neuron's
 * down_proj column. `add` edits with dose 0 are dropped (they would be a no-op).
 */
export function toEngineEdits(
  edits: readonly NeuronEdit[],
  residualMedians: readonly number[],
  colNorm: (layer: number, neuron: number) => number,
): EngineEdit[] {
  const out: EngineEdit[] = [];
  for (const e of edits) {
    if (!e.enabled) continue;
    if (e.mode === 'mul') {
      out.push({ layer: e.layer, index: e.neuron, scale: e.dose, offset: 0 });
      continue;
    }
    if (e.dose === 0) continue;
    const median = residualMedians[e.layer];
    if (median === undefined || !Number.isFinite(median)) {
      throw new Error(`recipe: no residual median for layer ${e.layer}`);
    }
    const norm = colNorm(e.layer, e.neuron);
    if (!(norm > 0))
      throw new Error(
        `recipe: down_proj column norm for L${e.layer} N${e.neuron} is ${norm}, cannot normalize`,
      );
    out.push({ layer: e.layer, index: e.neuron, scale: 1, offset: (e.dose * median) / norm });
  }
  return out;
}

export function exportRecipe(edits: readonly NeuronEdit[], model: RecipeModel): RecipeFile {
  validateRecipe(edits, model);
  return {
    schemaVersion: RECIPE_SCHEMA_VERSION,
    model: { file: model.file, layers: model.layers, ffn: model.ffn },
    site: RECIPE_SITE,
    normalization: RECIPE_NORMALIZATION,
    edits: edits.map((e) => ({
      layer: e.layer,
      neuron: e.neuron,
      dose: e.dose,
      mode: e.mode,
      enabled: e.enabled,
    })),
  };
}

/** Parse an exported recipe (a JSON string or the already-parsed object) for `model`. */
export function parseRecipe(json: string | unknown, model: RecipeModel): NeuronEdit[] {
  let raw: unknown = json;
  if (typeof json === 'string') {
    try {
      raw = JSON.parse(json);
    } catch (err) {
      throw new Error(`recipe: not valid JSON (${(err as Error).message})`);
    }
  }
  if (typeof raw !== 'object' || raw === null) throw new Error('recipe: expected an object');
  const r = raw as Partial<RecipeFile>;
  if (r.schemaVersion !== RECIPE_SCHEMA_VERSION) {
    throw new Error(
      `recipe: schemaVersion ${String(r.schemaVersion)} is not supported (expected ${RECIPE_SCHEMA_VERSION})`,
    );
  }
  if (r.site !== RECIPE_SITE)
    throw new Error(`recipe: site ${String(r.site)} is not ${RECIPE_SITE}`);
  if (r.normalization !== RECIPE_NORMALIZATION) {
    throw new Error(
      `recipe: normalization ${String(r.normalization)} is not ${RECIPE_NORMALIZATION}`,
    );
  }
  const m = r.model;
  if (typeof m !== 'object' || m === null) throw new Error('recipe: missing model');
  if (m.file !== model.file)
    throw new Error(`recipe: made for ${String(m.file)}, but the loaded model is ${model.file}`);
  if (m.layers !== model.layers || m.ffn !== model.ffn) {
    throw new Error(
      `recipe: model shape ${String(m.layers)}×${String(m.ffn)} does not match the loaded ${model.layers}×${model.ffn}`,
    );
  }
  if (!Array.isArray(r.edits)) throw new Error('recipe: edits must be an array');
  const edits = r.edits.map((e: NeuronEdit) => ({
    layer: e?.layer,
    neuron: e?.neuron,
    dose: e?.dose,
    mode: e?.mode,
    enabled: e?.enabled,
  }));
  validateRecipe(edits, model);
  return edits;
}

export const HISTORY_LIMIT = 50;

/**
 * Undo/redo over recipe snapshots. Call `record(current)` before each change; `undo`
 * and `redo` take the live recipe so it can move to the other stack. Snapshots are
 * deep copies, so later mutation of the live recipe cannot rewrite history.
 */
export class RecipeHistory {
  private past: NeuronEdit[][] = [];
  private future: NeuronEdit[][] = [];

  constructor(private readonly limit = HISTORY_LIMIT) {}

  get canUndo(): boolean {
    return this.past.length > 0;
  }

  get canRedo(): boolean {
    return this.future.length > 0;
  }

  get size(): number {
    return this.past.length;
  }

  record(current: readonly NeuronEdit[]): void {
    this.past.push(structuredClone(current) as NeuronEdit[]);
    if (this.past.length > this.limit) this.past.splice(0, this.past.length - this.limit);
    this.future = [];
  }

  undo(current: readonly NeuronEdit[]): NeuronEdit[] | null {
    const prev = this.past.pop();
    if (!prev) return null;
    this.future.push(structuredClone(current) as NeuronEdit[]);
    return prev;
  }

  redo(current: readonly NeuronEdit[]): NeuronEdit[] | null {
    const next = this.future.pop();
    if (!next) return null;
    this.past.push(structuredClone(current) as NeuronEdit[]);
    return next;
  }

  clear(): void {
    this.past = [];
    this.future = [];
  }
}
