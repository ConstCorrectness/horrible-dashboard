/** Code cells: finding them in a page, their shadow ids, and syncing the shadow. */
import { describe, expect, it } from 'vitest';

import { cellId, codeCellsOf, syncOps } from '../cells';
import type { CellOp } from '../../../notebook/types';

/** Apply ops to an id list the way `notebook_core.apply_op` does. */
function apply(ids: string[], ops: CellOp[]): string[] {
  const out = [...ids];
  for (const op of ops) {
    if (op.op === 'delete') out.splice(out.indexOf(op.cellId!), 1);
    else if (op.op === 'move') {
      out.splice(out.indexOf(op.cellId!), 1);
      out.splice(Math.min(op.index!, out.length), 0, op.cellId!);
    } else if (op.op === 'insert') {
      const at = op.afterCellId ? out.indexOf(op.afterCellId) + 1 : (op.index ?? out.length);
      out.splice(at, 0, op.cellId!);
    }
  }
  return out;
}

describe('codeCellsOf', () => {
  it('lists code cells in order, counting repeated sources', () => {
    const page = [
      '---',
      'title: T',
      '---',
      '',
      '```{code-cell} python',
      'x = 1',
      '```',
      '',
      ':::{note}',
      'Not code.',
      ':::',
      '',
      '+++',
      '',
      '```{code-cell}',
      'x = 1',
      '```',
      '',
      '```python',
      'not a cell',
      '```',
    ].join('\n');
    expect(codeCellsOf(page)).toEqual([
      { source: 'x = 1', occurrence: 0 },
      { source: 'x = 1', occurrence: 1 },
    ]);
  });
});

describe('cellId', () => {
  it('matches the backend: sha256 of the source, 12 hex, then the occurrence', async () => {
    // hashlib.sha256(b"1 + 1").hexdigest()[:12] in backend/modules/scrive/kernel.py
    const id = await cellId('1 + 1', 2);
    expect(id).toBe('c-72fce59447a0-2');
    expect(await cellId('1 + 1', 2)).toBe(id);
    expect(await cellId('1 + 2', 2)).not.toBe(id);
  });
});

describe('syncOps', () => {
  const d = (...ids: string[]) => ids.map((id) => ({ id, source: id }));
  const s = (...ids: string[]) => ids.map((id) => ({ id }));

  it.each([
    [[], ['a', 'b']],
    [
      ['a', 'b'],
      ['a', 'b'],
    ],
    [
      ['a', 'x', 'b'],
      ['a', 'b'],
    ],
    [
      ['b', 'a'],
      ['a', 'b', 'c'],
    ],
    [
      ['c', 'a', 'old'],
      ['a', 'new', 'c'],
    ],
  ])('%j → %j', (shadow, desired) => {
    const ops = syncOps(s(...shadow), d(...desired));
    expect(apply(shadow, ops)).toEqual(desired);
  });

  it('sends nothing when the shadow already matches', () => {
    expect(syncOps(s('a', 'b'), d('a', 'b'))).toEqual([]);
  });
});
