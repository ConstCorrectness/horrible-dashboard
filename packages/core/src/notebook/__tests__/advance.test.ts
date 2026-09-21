import { describe, expect, it } from 'vitest';

import { advanceFrom } from '../advance';
import type { NotebookCell } from '../types';

const cell = (id: string, type: 'code' | 'markdown' = 'code'): NotebookCell => ({
  id,
  cell_type: type,
  source: '',
  outputs: [],
  execution_count: null,
});

describe('advanceFrom', () => {
  it('moves to the next cell when there is one', () => {
    const cells = [cell('a'), cell('b'), cell('c')];
    expect(advanceFrom(cells, 0)).toEqual({ kind: 'focus', index: 1 });
    expect(advanceFrom(cells, 1)).toEqual({ kind: 'focus', index: 2 });
  });

  it('does not care what type the next cell is', () => {
    // Markdown is a destination like any other: Shift+Enter renders it and keeps
    // going, which is the whole point of the walk.
    const cells = [cell('a'), cell('b', 'markdown')];
    expect(advanceFrom(cells, 0)).toEqual({ kind: 'focus', index: 1 });
  });

  it('appends a cell at the end rather than stopping', () => {
    const cells = [cell('a'), cell('b')];
    expect(advanceFrom(cells, 1)).toEqual({ kind: 'insert', afterCellId: 'b', index: 2 });
  });

  it('appends from a lone cell', () => {
    expect(advanceFrom([cell('only')], 0)).toEqual({
      kind: 'insert',
      afterCellId: 'only',
      index: 1,
    });
  });

  it('has nothing to insert after in an empty notebook', () => {
    // An empty `afterCellId` means "append", which is how the cell ops already
    // spell it — an `insert` op with no `afterCellId` goes to the end.
    expect(advanceFrom([], 0)).toEqual({ kind: 'insert', afterCellId: '', index: 0 });
  });

  it('treats an index past the end as the end', () => {
    // Reachable after a delete races a keypress. Appending is the safe answer;
    // returning `focus` at a nonexistent index would silently do nothing.
    const cells = [cell('a')];
    expect(advanceFrom(cells, 7)).toEqual({ kind: 'insert', afterCellId: 'a', index: 1 });
  });
});
