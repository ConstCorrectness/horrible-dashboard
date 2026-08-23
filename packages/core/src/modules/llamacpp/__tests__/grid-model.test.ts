import { describe, expect, it } from 'vitest';

import type { LensCell, LensGrid, LensTrack, TraceToken } from '../api';
import {
  candidateRank,
  cellStrength,
  diffGrids,
  displayToken,
  editableTokens,
  EMBEDDING_LAYER,
  layerLabel,
  rankStrength,
  trackRank,
} from '../lens/grid-model';

function cell(texts: string[], ids: number[], relProbs: number[]): LensCell {
  return { ids, texts, logits: ids.map((_, i) => 10 - i), relProbs };
}

function grid(layers: number[], positions: number[], cells: LensCell[][]): LensGrid {
  return {
    layers,
    positions,
    cells,
    lens: { id: 'identity', kind: 'identity', label: '', provenance: '', layers: [], dModel: 0 },
    unembedding: {
      tensor: 'output.weight',
      tied: false,
      quant: 'F32',
      nEmbd: 8,
      nVocab: 1000,
      architecture: 'llama',
      tokenizerModel: 'gpt2',
      logitSoftcap: null,
    },
    tokens: [],
    verified: 'true',
    verifyNote: '',
    verifyDetail: {},
  };
}

describe('displayToken', () => {
  it('makes leading whitespace visible', () => {
    // ` Paris` and `Paris` are different tokens with different ranks. Rendering
    // both as "Paris" would make two rows of the grid look like one finding.
    expect(displayToken(' Paris')).toBe('·Paris');
    expect(displayToken('Paris')).toBe('Paris');
  });

  it('shows an empty piece rather than an empty cell', () => {
    expect(displayToken('')).toBe('∅');
  });

  it('marks newlines and tabs instead of breaking the row', () => {
    expect(displayToken('\n')).toBe('⏎');
    expect(displayToken('\t')).toBe('⇥');
  });

  it('truncates with an ellipsis', () => {
    expect(displayToken('abcdefghijklmnop', 6)).toBe('abcde…');
  });
});

describe('layerLabel', () => {
  it('names the embedding rather than calling it layer -1', () => {
    expect(layerLabel(EMBEDDING_LAYER)).toBe('emb');
    expect(layerLabel(0)).toBe('L0');
  });
});

describe('cellStrength', () => {
  it('is zero for an even split across the shown candidates', () => {
    const third = 1 / 3;
    expect(cellStrength(cell(['a', 'b', 'c'], [1, 2, 3], [third, third, third]))).toBeCloseTo(
      0,
      6,
    );
  });

  it('is one when the top candidate takes everything', () => {
    expect(cellStrength(cell(['a', 'b'], [1, 2], [1, 0]))).toBe(1);
  });

  it('is zero for a missing cell rather than NaN', () => {
    expect(cellStrength(undefined)).toBe(0);
  });
});

describe('rankStrength', () => {
  it('is log-scaled, so rank 1 and rank 5 are far apart and 4000 and 8000 are not', () => {
    const near = rankStrength(1, 262144) - rankStrength(5, 262144);
    const far = rankStrength(4000, 262144) - rankStrength(8000, 262144);
    expect(near).toBeGreaterThan(far);
  });

  it('is 1 at rank 1 and near 0 at the bottom of the vocabulary', () => {
    expect(rankStrength(1, 1000)).toBe(1);
    expect(rankStrength(1000, 1000)).toBeCloseTo(0, 5);
  });
});

describe('candidateRank', () => {
  it('finds a token among the shown candidates, one-based', () => {
    expect(candidateRank(cell(['a', 'b'], [7, 9], [0.6, 0.4]), 9)).toBe(2);
  });

  it('is null when the token is not shown, never 0', () => {
    expect(candidateRank(cell(['a'], [7], [1]), 9)).toBeNull();
  });
});

describe('editableTokens', () => {
  it('excludes generated tokens', () => {
    // A generated token is an output. Replacing one would be editing the answer
    // rather than the question, which is a different (and meaningless) run.
    const tokens: TraceToken[] = [
      { index: 0, id: 1, text: 'a', generated: false },
      { index: 1, id: 2, text: 'b', generated: true },
    ];
    expect(editableTokens(tokens).map((t) => t.index)).toEqual([0]);
  });
});

describe('diffGrids', () => {
  it('reports a changed top word', () => {
    const before = grid([0], [0], [[cell(['Paris'], [1], [1])]]);
    const after = grid([0], [0], [[cell(['Rome'], [2], [1])]]);
    expect(diffGrids(before, after)[0]?.[0]).toMatchObject({
      changed: true,
      was: 'Paris',
      now: 'Rome',
    });
  });

  it('aligns by layer and position, not by array index', () => {
    // The parent covered layers 0 and 1; the fork's grid starts at layer 1.
    // Comparing cells[0] to cells[0] would silently compare layer 1 against
    // layer 0 and call an unchanged cell changed.
    const before = grid([0, 1], [0], [[cell(['a'], [1], [1])], [cell(['b'], [2], [1])]]);
    const after = grid([1], [0], [[cell(['b'], [2], [1])]]);
    expect(diffGrids(before, after)[0]?.[0]?.changed).toBe(false);
  });

  it('reports how far the old winner fell when it is still shown', () => {
    const before = grid([0], [0], [[cell(['Paris', 'Rome'], [1, 2], [0.6, 0.4])]]);
    const after = grid([0], [0], [[cell(['Rome', 'Paris'], [2, 1], [0.6, 0.4])]]);
    expect(diffGrids(before, after)[0]?.[0]?.rankDelta).toBe(1);
  });

  it('reports nothing rather than a false change for a cell the parent lacks', () => {
    const before = grid([0], [0], [[cell(['a'], [1], [1])]]);
    const after = grid([0, 1], [0], [[cell(['a'], [1], [1])], [cell(['c'], [3], [1])]]);
    expect(diffGrids(before, after)[1]?.[0]).toMatchObject({ changed: false, was: '' });
  });
});

describe('trackRank', () => {
  const track: LensTrack = {
    tokenId: 5,
    text: ' Paris',
    layers: [0, 1],
    positions: [0, 1],
    logits: [
      [1, 2],
      [3, 4],
    ],
    ranks: [
      [400, 12],
      [3, 1],
    ],
    lens: { id: 'identity', kind: 'identity', label: '', provenance: '', layers: [], dModel: 0 },
  };

  it('looks a rank up by layer and position', () => {
    expect(trackRank(track, 1, 1)).toBe(1);
    expect(trackRank(track, 0, 0)).toBe(400);
  });

  it('is null off the grid rather than reading the wrong cell', () => {
    expect(trackRank(track, 9, 0)).toBeNull();
    expect(trackRank(track, 0, 9)).toBeNull();
  });
});
