/**
 * Activity-rail state derivation — which glyphs a rail shows for a dock, and
 * what each one currently means. Pure over `FrameState` + the registry so it can
 * be unit-tested; the rendering half lives in packages/ui/src/layout/ActivityRail.
 * See docs/architecture/windowing.mdx.
 */
import { registry } from '../registry';
import { dockSidesOf } from './controller';
import { listPanes } from './model';
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

  // Views declared for this side that aren't docked anywhere: either sitting out
  // in the center, or not open at all (dimmed, so a module stays discoverable
  // before it has ever been opened).
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

  return [...docked, ...rest];
}
