/**
 * Rail customization preferences — the user's overrides on top of what modules
 * *declare*: which dock a view's glyph calls home (`side`), glyphs hidden from
 * the rails entirely (`hidden`), and per-dock glyph order (`order`).
 *
 * Global rather than per-workspace (like VS Code's activity-bar customization):
 * where your tools live is a habit, not a layout. The whole bag rides the
 * settings store as one JSON-string override under `frame.railPrefs`, which
 * buys boot hydration, live reactivity (settingsStore emits on every set), and
 * server-side persistence for free. Derivations (`dockSidesOf`, `railEntries`)
 * consult this module; mutations here persist fire-and-forget so callers and
 * tests never await the network.
 */
import { getSetting, resetSetting, setSetting, settingsStore } from '../settings';
import type { DockSide } from './types';

export const RAIL_PREFS_KEY = 'frame.railPrefs';

export interface RailPrefs {
  /** viewId → the dock its glyph should call home, overriding the declaration. */
  side: Record<string, DockSide>;
  /** View ids whose glyphs are hidden from the rails. */
  hidden: string[];
  /** Per-dock glyph order; entries not listed keep their natural order after. */
  order: Partial<Record<DockSide, string[]>>;
}

const EMPTY: RailPrefs = { side: {}, hidden: [], order: {} };

const DOCK_SIDES: readonly DockSide[] = ['left', 'right', 'bottom'];

function isDockSide(value: unknown): value is DockSide {
  return DOCK_SIDES.includes(value as DockSide);
}

/** Tolerant parse — a malformed blob degrades to no customization. */
function parse(raw: string | undefined): RailPrefs {
  if (!raw) return EMPTY;
  try {
    const data = JSON.parse(raw) as Partial<RailPrefs> | null;
    if (!data || typeof data !== 'object') return EMPTY;
    const side: Record<string, DockSide> = {};
    for (const [viewId, s] of Object.entries(data.side ?? {})) {
      if (isDockSide(s)) side[viewId] = s;
    }
    const order: RailPrefs['order'] = {};
    for (const dock of DOCK_SIDES) {
      const list = data.order?.[dock];
      if (Array.isArray(list)) order[dock] = list.map(String);
    }
    return {
      side,
      hidden: Array.isArray(data.hidden) ? data.hidden.map(String) : [],
      order,
    };
  } catch {
    return EMPTY;
  }
}

// Parse cache keyed on the raw string, so getRailPrefs is cheap to call from
// render paths and returns a stable reference between settings changes.
let cache: { raw: string | undefined; prefs: RailPrefs } | null = null;

/** The current preferences (empty defaults when never customized). */
export function getRailPrefs(): RailPrefs {
  const raw = getSetting<string>(RAIL_PREFS_KEY);
  if (!cache || cache.raw !== raw) cache = { raw, prefs: parse(raw) };
  return cache.prefs;
}

/** Subscribe/getSnapshot pair for useSyncExternalStore in the rails. */
export const railPrefsStore = {
  subscribe: (listener: () => void) => settingsStore.subscribe(listener),
  getSnapshot: (): RailPrefs => getRailPrefs(),
};

function save(prefs: RailPrefs): void {
  // setSetting updates the local override synchronously (and emits) before the
  // network write; a failed persist costs durability, not correctness.
  setSetting(RAIL_PREFS_KEY, JSON.stringify(prefs)).catch(() => {});
}

/** Override (or clear, with null) the dock a view's glyph calls home. */
export function setViewDockSide(viewId: string, side: DockSide | null): void {
  const prefs = getRailPrefs();
  const next = { ...prefs.side };
  if (side === null) delete next[viewId];
  else next[viewId] = side;
  save({ ...prefs, side: next });
}

/** Hide a glyph from the rails, or bring it back. */
export function setViewHidden(viewId: string, hidden: boolean): void {
  const prefs = getRailPrefs();
  const has = prefs.hidden.includes(viewId);
  if (hidden === has) return;
  save({
    ...prefs,
    hidden: hidden ? [...prefs.hidden, viewId] : prefs.hidden.filter((v) => v !== viewId),
  });
}

export function isViewHidden(viewId: string): boolean {
  return getRailPrefs().hidden.includes(viewId);
}

/**
 * Place a view's glyph on `side` at `index` (of the dock's current rail order,
 * as rendered). One write covers the whole drop gesture: the side override, the
 * removal from every other dock's order list, and the splice into this one's.
 * `currentOrder` is the rendered glyph order of the target dock, so an index
 * from the drop gesture means what the user saw.
 */
export function placeRailGlyph(
  viewId: string,
  side: DockSide,
  index: number,
  currentOrder: string[],
): void {
  const prefs = getRailPrefs();
  const order: RailPrefs['order'] = {};
  for (const dock of DOCK_SIDES) {
    const list = prefs.order[dock];
    if (list) order[dock] = list.filter((v) => v !== viewId);
  }
  const target = (order[side] ?? currentOrder).filter((v) => v !== viewId);
  target.splice(Math.max(0, Math.min(index, target.length)), 0, viewId);
  order[side] = target;
  save({ ...prefs, side: { ...prefs.side, [viewId]: side }, order });
}

/** Drop every customization (the `rail.reset` command). */
export function resetRailPrefs(): void {
  resetSetting(RAIL_PREFS_KEY).catch(() => {});
}
