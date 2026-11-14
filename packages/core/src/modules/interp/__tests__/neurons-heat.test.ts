import { describe, expect, it } from 'vitest';

import { layerHeat, topNeurons } from '../neurons/heat';

// 3 layers × 4 neurons.
const capture = Float32Array.from(
  [
    [1, -4, 2, 0],
    [0, 0, 0, 0],
    [5, -5, Number.NaN, 0.5],
  ].flat(),
);

describe('layerHeat', () => {
  it('normalizes a layer by its max |value|, keeping sign', () => {
    expect(Array.from(layerHeat(capture, 0, 4))).toEqual([0.25, -1, 0.5, 0]);
    expect(Array.from(layerHeat(capture, 2, 4))).toEqual(
      [1, -1, 0, 0.1].map((v) => Math.fround(v)),
    );
  });

  it('gives zeros for an all-zero layer and rejects a short capture', () => {
    expect(Array.from(layerHeat(capture, 1, 4))).toEqual([0, 0, 0, 0]);
    expect(() => layerHeat(capture, 3, 4)).toThrow(/expected/);
  });
});

describe('topNeurons', () => {
  it('ranks by |value| across layers, ties to the lower layer then neuron', () => {
    expect(topNeurons(capture, 3, 4, 4)).toEqual([
      { layer: 2, neuron: 0, value: 5 },
      { layer: 2, neuron: 1, value: -5 },
      { layer: 0, neuron: 1, value: -4 },
      { layer: 0, neuron: 2, value: 2 },
    ]);
  });

  it('restricts to one layer and handles k edge cases', () => {
    expect(topNeurons(capture, 3, 4, 2, { layer: 0 })).toEqual([
      { layer: 0, neuron: 1, value: -4 },
      { layer: 0, neuron: 2, value: 2 },
    ]);
    expect(topNeurons(capture, 3, 4, 0)).toEqual([]);
    expect(topNeurons(capture, 3, 4, 100)).toHaveLength(11); // the NaN is skipped
    expect(() => topNeurons(capture, 3, 4, 1, { layer: 3 })).toThrow(/layer 3/);
  });

  it('matches a full sort on a large random capture', () => {
    const layers = 6;
    const ffn = 997;
    let seed = 7;
    const rand = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648) * 2 - 1;
    const big = Float32Array.from({ length: layers * ffn }, rand);
    const expected = Array.from(big, (value, i) => ({
      layer: Math.floor(i / ffn),
      neuron: i % ffn,
      value,
    }))
      .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
      .slice(0, 25);
    expect(topNeurons(big, layers, ffn, 25)).toEqual(expected);
  });
});
