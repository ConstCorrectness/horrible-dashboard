import { describe, expect, it } from 'vitest';

import {
  exportRecipe,
  influence,
  type NeuronEdit,
  parseRecipe,
  RecipeHistory,
  toEngineEdits,
  validateRecipe,
} from '../neurons/recipe';

const shape = { layers: 36, ffn: 9728 };
const model = { file: 'Qwen3-4B-Q4_K_M.gguf', ...shape };
const edit = (over: Partial<NeuronEdit> = {}): NeuronEdit => ({
  layer: 14,
  neuron: 8949,
  dose: 1.5,
  mode: 'add',
  enabled: true,
  ...over,
});

describe('validateRecipe', () => {
  it('accepts a valid recipe', () => {
    expect(() =>
      validateRecipe([edit(), edit({ neuron: 1, mode: 'mul', dose: -3 })], shape),
    ).not.toThrow();
    expect(() => validateRecipe([], shape)).not.toThrow();
  });

  it.each([
    [[edit({ layer: 36 })], /layer 36/],
    [[edit({ layer: 1.5 })], /layer 1.5/],
    [[edit({ neuron: -1 })], /neuron -1/],
    [[edit({ neuron: 9728 })], /neuron 9728/],
    [[edit({ dose: 3.01 })], /dose 3.01/],
    [[edit({ dose: Number.NaN })], /dose NaN/],
    [[edit({ dose: Number.POSITIVE_INFINITY })], /dose Infinity/],
    [[edit({ mode: 'set' as never })], /mode set/],
    [[edit(), edit({ dose: 2 })], /duplicate edit for layer 14 neuron 8949/],
  ])('rejects %j', (edits, message) => {
    expect(() => validateRecipe(edits, shape)).toThrow(message);
  });

  it('caps a recipe at 32 edits', () => {
    const many = Array.from({ length: 33 }, (_, i) => edit({ neuron: i }));
    expect(() => validateRecipe(many.slice(0, 32), shape)).not.toThrow();
    expect(() => validateRecipe(many, shape)).toThrow(/33 edits, at most 32/);
  });
});

describe('influence & toEngineEdits', () => {
  it('measures influence from the no-op point', () => {
    expect(influence({ mode: 'mul', dose: 1 })).toBe(0);
    expect(influence({ mode: 'mul', dose: 0 })).toBe(-1);
    expect(influence({ mode: 'add', dose: -2 })).toBe(-2);
  });

  it('normalizes add doses by layer median over column norm', () => {
    const medians = Array.from({ length: 36 }, (_, l) => 10 + l);
    const out = toEngineEdits(
      [
        edit({ layer: 2, neuron: 5, dose: 2 }),
        edit({ layer: 3, neuron: 6, mode: 'mul', dose: 0.5 }),
        edit({ layer: 4, neuron: 7, dose: 1, enabled: false }),
        edit({ layer: 5, neuron: 8, dose: 0 }),
      ],
      medians,
      (layer, neuron) => layer + neuron,
    );
    expect(out).toEqual([
      { layer: 2, index: 5, scale: 1, offset: (2 * 12) / 7 },
      { layer: 3, index: 6, scale: 0.5, offset: 0 },
    ]);
  });

  it('throws when a column norm cannot normalize', () => {
    expect(() => toEngineEdits([edit()], new Array(36).fill(1), () => 0)).toThrow(/column norm/);
    expect(() => toEngineEdits([edit()], [], () => 1)).toThrow(/residual median/);
  });
});

describe('recipe files', () => {
  const edits = [edit(), edit({ layer: 0, neuron: 0, mode: 'mul', dose: 0, enabled: false })];

  it('round-trips through JSON', () => {
    const file = exportRecipe(edits, model);
    expect(file).toMatchObject({
      schemaVersion: 1,
      site: 'mlp.down_proj.input',
      normalization: 'layer_residual_median_over_down_column_l2',
      model,
    });
    expect(parseRecipe(JSON.stringify(file), model)).toEqual(edits);
    expect(parseRecipe(file, model)).toEqual(edits);
  });

  it('rejects another model, another shape, bad JSON and bad edits', () => {
    const json = JSON.stringify(exportRecipe(edits, model));
    expect(() => parseRecipe(json, { ...model, file: 'other.gguf' })).toThrow(
      /made for Qwen3-4B-Q4_K_M.gguf/,
    );
    expect(() => parseRecipe(json, { ...model, ffn: 9729 })).toThrow(/does not match/);
    expect(() => parseRecipe('{', model)).toThrow(/not valid JSON/);
    expect(() => parseRecipe({ ...JSON.parse(json), schemaVersion: 2 }, model)).toThrow(
      /schemaVersion 2/,
    );
    expect(() => parseRecipe({ ...JSON.parse(json), site: 'attn' }, model)).toThrow(/site attn/);
    const bad = JSON.parse(json);
    bad.edits[0].dose = 9;
    expect(() => parseRecipe(bad, model)).toThrow(/dose 9/);
  });
});

describe('RecipeHistory', () => {
  it('undoes and redoes snapshots that later mutation cannot touch', () => {
    const h = new RecipeHistory();
    const live: NeuronEdit[] = [];
    h.record(live);
    live.push(edit());
    h.record(live);
    live[0]!.dose = 2.5;

    expect(h.canUndo).toBe(true);
    const back = h.undo(live)!;
    expect(back).toEqual([edit()]);
    expect(h.redo(back)).toEqual([edit({ dose: 2.5 })]);
    expect(h.undo(live)).toEqual([edit()]);
    expect(h.undo([edit()])).toEqual([]);
    expect(h.undo([])).toBeNull();
  });

  it('caps at 50 entries and clears redo on a new record', () => {
    const h = new RecipeHistory();
    for (let i = 0; i < 60; i++) h.record([edit({ neuron: i })]);
    expect(h.size).toBe(50);
    let last: NeuronEdit[] | null = [];
    let oldest: NeuronEdit[] = [];
    while ((last = h.undo([])) !== null) oldest = last;
    expect(oldest[0]!.neuron).toBe(10);
    expect(h.canRedo).toBe(true);
    h.record([]);
    expect(h.canRedo).toBe(false);
  });
});
