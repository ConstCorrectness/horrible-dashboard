/**
 * Activity-rail state derivation — which glyphs a rail shows for a dock, and
 * what each one currently means — plus the rail-customization verbs (move a
 * glyph to another dock, hide one). Derivation is pure over `FrameState` + the
 * registry + the user's rail prefs (rail-prefs.ts) so it can be unit-tested;
 * the rendering half lives in packages/ui/src/layout/ActivityRail.
 * See docs/architecture/windowing.mdx.
 */
import { registry } from '../registry';
import { closePaneGuarded, dockSidesOf, isDockable } from './controller';
import { listPanes } from './model';
import { getRailPrefs, placeRailGlyph, setViewHidden } from './rail-prefs';
import { layoutStore } from './store';
import type { DockSide, FrameState } from './types';

/** Which docks each rail drives, in render order.
 *
 * The left rail also carries the **bottom** dock: the bottom edge is reserved
 * for the minibuffer rather than a third rail, and a rail is the only tool
 * switcher a dock has — without this the bottom dock's tools (terminal, REPL)
 * could be revealed by keybinding but never switched between. */
export const RAIL_SECTIONS: Record<RailSide, DockSide[]> = {
  left: ['left', 'bottom'],
  right: ['right'],
};

export type RailSide = 'left' | 'right';

export type RailState =
  /** The dock's visible tool — picking it hides the dock. */
  | 'active'
  /** In the dock's list but not showing — picking it reveals it. */
  | 'docked'
  /** Out in a center area or the floating layer — picking it focuses it. */
  | 'center'
  /** Not open anywhere — picking it opens it in this dock. */
  | 'closed';

export interface RailEntry {
  viewId: string;
  title: string;
  icon: string;
  state: RailState;
  /** Set for everything except `closed`. */
  instanceId?: string;
}

/** The glyphs one dock contributes to its rail, in display order. */
export function railEntries(frame: FrameState, side: DockSide): RailEntry[] {
  const prefs = getRailPrefs();
  const dock = frame.docks[side];
  const open = listPanes(frame);
  const dockedAnywhere = new Set(
    open.filter((p) => p.location.kind === 'dock').map((p) => p.pane.viewId),
  );
  const views = [...registry.panels, ...registry.widgets];
  const decl = (viewId: string) => views.find((v) => v.id === viewId);

  // What is actually in this dock, in dock order — listed by where it *is*, not
  // where it declared it wanted to be, so a tool that ended up on an unusual
  // side (an old preset, a hand-edited layout) still has a switcher.
  const docked = dock.tools.flatMap((tool): RailEntry[] => {
    const d = decl(tool.viewId);
    if (!d) return [];
    return [
      {
        viewId: tool.viewId,
        title: d.title,
        icon: d.icon ?? d.title[0],
        instanceId: tool.instanceId,
        state: dock.visible && dock.activeTool === tool.instanceId ? 'active' : 'docked',
      },
    ];
  });

  // Views declared (or user-assigned) for this side that aren't docked anywhere:
  // either sitting out in the center, or not open at all (dimmed, so a module
  // stays discoverable before it has ever been opened).
  const rest = views
    .filter((v) => !dockedAnywhere.has(v.id) && dockSidesOf(v.id)[0] === side)
    .map((v): RailEntry => {
      const elsewhere = open.find((p) => p.pane.viewId === v.id);
      return {
        viewId: v.id,
        title: v.title,
        icon: v.icon ?? v.title[0],
        instanceId: elsewhere?.pane.instanceId,
        state: elsewhere ? 'center' : 'closed',
      };
    });

  const entries = [...docked, ...rest].filter((e) => !prefs.hidden.includes(e.viewId));

  // User order: listed glyphs first in list order, the rest keep natural order.
  const order = prefs.order[side];
  if (!order?.length) return entries;
  const rank = new Map(order.map((viewId, i) => [viewId, i]));
  return entries
    .map((entry, natural) => ({ entry, natural }))
    .sort((a, b) => {
      const ra = rank.get(a.entry.viewId) ?? order.length + a.natural;
      const rb = rank.get(b.entry.viewId) ?? order.length + b.natural;
      return ra - rb;
    })
    .map(({ entry }) => entry);
}

/**
 * Place a view's glyph on `side` at `index` of that dock's rendered order — the
 * rail drop verb, also behind the glyph context menu's "Move to …". Persists
 * the preference, and if the view is open somewhere, moves the pane into the
 * dock so the layout matches what the user just expressed. Returns false for a
 * view that isn't dockable at all (customization never overrides declarations).
 */
export function moveViewToDock(viewId: string, side: DockSide, index?: number): boolean {
  if (!isDockable(viewId)) return false;
  const frame = layoutStore.getSnapshot().frame;
  const rendered = railEntries(frame, side).map((e) => e.viewId);
  placeRailGlyph(viewId, side, index ?? rendered.length, rendered);
  const located = listPanes(frame).find((p) => p.pane.viewId === viewId);
  if (located && !(located.location.kind === 'dock' && located.location.dock === side)) {
    layoutStore.dispatch({ type: 'MOVE_TOOL', instanceId: located.pane.instanceId, side });
  }
  return true;
}

/**
 * Hide a view's glyph from the rails. A currently docked instance is closed
 * first (guarded) — a tool with no glyph would be reachable but unswitchable.
 * Panes out in the center are untouched: hiding a glyph edits the rail, not
 * the layout.
 */
export function hideRailView(viewId: string): void {
  const docked = listPanes(layoutStore.getSnapshot().frame).find(
    (p) => p.pane.viewId === viewId && p.location.kind === 'dock',
  );
  if (docked) void closePaneGuarded(docked.pane.instanceId);
  setViewHidden(viewId, true);
}
