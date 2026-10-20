/**
 * The buy menu's shape: groups of rows, and what a number key does.
 *
 * The catalogue outgrew one row of keys (`modes/defuse.py` builds it from the
 * weapon table: every priced gun, then the gear), so the menu is CS's: a key
 * opens a group, a second key buys a row in it, and `0` goes back. Only the
 * rows the player's side may buy are listed, but each keeps its catalogue
 * index, because that index is `Command.buy` and the server's to number.
 *
 * Free of React and three.
 */
import { sideOf } from './cosmetics';
import type { BuyItem } from './net';

export type BuyCategory = 'secondary' | 'primary' | 'gear' | 'grenade';

export interface BuyRow {
  /** The catalogue index: what goes on the wire. */
  index: number;
  item: BuyItem;
}

export interface BuyGroup {
  category: BuyCategory;
  label: string;
  rows: BuyRow[];
}

const ORDER: { category: BuyCategory; label: string }[] = [
  { category: 'secondary', label: 'PISTOLS' },
  { category: 'primary', label: 'PRIMARY' },
  { category: 'gear', label: 'GEAR' },
  { category: 'grenade', label: 'GRENADES' },
];

/** A row's group: the served `category`, or (from an older server, which sent
 * none) what its `kind` implies. */
export function categoryOf(item: BuyItem): BuyCategory {
  const served = item.category;
  if (served === 'secondary' || served === 'primary' || served === 'gear' || served === 'grenade') {
    return served;
  }
  if (item.kind === 'weapon') return 'primary';
  if (item.kind === 'nade') return 'grenade';
  return 'gear';
}

/** The groups `team` sees, in menu order, empty ones left out. */
export function buyGroups(catalog: BuyItem[], team: number | undefined): BuyGroup[] {
  const side = sideOf(team);
  const groups = ORDER.map(({ category, label }) => ({ category, label, rows: [] as BuyRow[] }));
  catalog.forEach((item, index) => {
    if (item.side && item.side !== 'any' && item.side !== side) return;
    groups.find((g) => g.category === categoryOf(item))?.rows.push({ index, item });
  });
  return groups.filter((g) => g.rows.length > 0);
}

/**
 * One number key in the held menu. `digit` is 1..9, or 0 for back. Returns the
 * group now open (`null` is the top level) and the catalogue index to buy, or
 * `-1`. A buy returns to the top, as CS does, so the next purchase starts from
 * the same place every time.
 */
export function pressBuyKey(
  groups: BuyGroup[],
  open: number | null,
  digit: number,
): { open: number | null; buy: number } {
  if (digit === 0) return { open: null, buy: -1 };
  if (open === null) {
    return { open: digit - 1 < groups.length ? digit - 1 : null, buy: -1 };
  }
  const row = groups[open]?.rows[digit - 1];
  return row ? { open: null, buy: row.index } : { open, buy: -1 };
}

/** What the window says, in a word: why a buy now would be refused, if it would. */
export function buyNotice(
  mine:
    | {
        canBuy?: boolean;
        buyIn?: number;
        inBuyZone?: boolean;
      }
    | null
    | undefined,
): string {
  if (!mine) return '';
  if (mine.canBuy) return '';
  if ((mine.buyIn ?? 0) <= 0) return 'BUY TIME IS OVER';
  if (mine.inBuyZone === false) return 'OUTSIDE THE BUY ZONE';
  return 'THE WINDOW IS CLOSED';
}
