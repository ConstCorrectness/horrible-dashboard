/**
 * The buy menu against `economy-vectors.json`, which
 * `scripts/gen_hassault_economy_vectors.py` writes from `modes/defuse.py`.
 *
 * The catalogue is the server's and grows with the weapon table, so the
 * grouping is checked against the real one: each side sees exactly the rows it
 * may buy, under the groups the native client shows too, and a key sequence
 * buys the catalogue index the server will read.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { buyGroups, buyNotice, categoryOf, pressBuyKey } from '../buy-menu';
import type { BuyItem } from '../net';

interface Vectors {
  catalog: BuyItem[];
  menus: Record<'t' | 'ct', { category: string; label: string; rows: number[] }[]>;
}

const vectors = JSON.parse(
  readFileSync(join(__dirname, 'economy-vectors.json'), 'utf8'),
) as Vectors;

describe('the buy menu, against the served catalogue', () => {
  it.each([
    ['t', 0],
    ['ct', 1],
  ] as const)('groups the %s side as the server does', (side, team) => {
    const groups = buyGroups(vectors.catalog, team);
    expect(
      groups.map((g) => ({
        category: g.category,
        label: g.label,
        rows: g.rows.map((r) => r.index),
      })),
    ).toEqual(vectors.menus[side]);
  });

  it('has more rows than one row of keys, which is why it is grouped', () => {
    expect(vectors.catalog.length).toBeGreaterThan(9);
    for (const side of ['t', 'ct'] as const) {
      for (const group of vectors.menus[side]) expect(group.rows.length).toBeLessThanOrEqual(9);
    }
  });

  it('buys with a group key then a row key, and goes back with 0', () => {
    const groups = buyGroups(vectors.catalog, 1);
    const primary = groups.findIndex((g) => g.category === 'primary');
    let state = pressBuyKey(groups, null, primary + 1);
    expect(state).toEqual({ open: primary, buy: -1 });
    state = pressBuyKey(groups, state.open, 2);
    expect(state).toEqual({ open: null, buy: groups[primary].rows[1].index });
    expect(pressBuyKey(groups, primary, 0)).toEqual({ open: null, buy: -1 });
    // A key past the end does nothing, rather than buying something else.
    expect(pressBuyKey(groups, primary, 9).buy).toBe(-1);
    expect(pressBuyKey(groups, null, 9).open).toBeNull();
  });

  it('groups an older server’s rows by their kind', () => {
    expect(categoryOf({ id: 'x', name: 'x', kind: 'weapon', slot: 2, price: 1 })).toBe('primary');
    expect(categoryOf({ id: 'x', name: 'x', kind: 'nade', slot: 0, price: 1 })).toBe('grenade');
    expect(categoryOf({ id: 'x', name: 'x', kind: 'kit', slot: -1, price: 1 })).toBe('gear');
  });

  it('says why a buy would be refused', () => {
    expect(buyNotice({ canBuy: true, buyIn: 10, inBuyZone: true })).toBe('');
    expect(buyNotice({ canBuy: false, buyIn: 0 })).toBe('BUY TIME IS OVER');
    expect(buyNotice({ canBuy: false, buyIn: 12, inBuyZone: false })).toBe('OUTSIDE THE BUY ZONE');
  });
});
