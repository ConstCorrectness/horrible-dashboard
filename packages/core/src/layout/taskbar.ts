/**
 * Taskbar derivation: what the window buttons say, and what clicking one does.
 *
 * Pure over `FrameState` + the registry, in the same shape and for the same
 * reason as `railEntries` — the taskbar should have no opinion of its own about
 * what is open. The rendering half lives in packages/ui/src/desktop/taskbar/.
 *
 * See docs/architecture/desktop-shell.mdx.
 */
import { registry } from '../registry';
import { findArea, listPanes } from './model';
import type { FrameState, PaneLocation } from './types';

/**
 * A taskbar button's current meaning. Note that a taskbar button is per **pane
 * instance**, not per window: a window merged into three tabs is three things
 * the user can switch to, and one button for it would make two of them
 * unreachable from here.
 */
export type TaskbarState =
  /** The focused window, or the active tab of the focused area. Click hides it. */
  | 'focused'
  /** Open and on screen, but not focused. Click focuses. */
  | 'open'
  /** Minimized — a minimized window, or a minimized centre pane. Click restores. */
  | 'minimized'
  /** Open but not showing: a background tab, or a tool in a hidden dock. */
  | 'hidden';

export interface TaskbarEntry {
  instanceId: string;
  viewId: string;
  title: string;
  icon: string;
  state: TaskbarState;
  /** Set when the pane lives in a window — the taskbar's minimize target. */
  windowId?: string;
  /** Where the pane actually is, for callers that need to route the click. */
  location: PaneLocation;
}

/**
 * One entry per open pane instance, in a **stable** order.
 *
 * Stable is the whole requirement: a taskbar that reorders on focus moves the
 * button out from under a pointer that is on its way to click it. So entries are
 * ordered by pane instance id, which is allocated monotonically and never
 * changes — i.e. by the order the panes were opened — and nothing about focus,
 * z-order or minimizing can reorder them.
 */
export function taskbarEntries(frame: FrameState): TaskbarEntry[] {
  const views = [...registry.panels, ...registry.widgets];
  const decl = (viewId: string) => views.find((v) => v.id === viewId);

  return listPanes(frame)
    .flatMap((located): TaskbarEntry[] => {
      const { pane, location } = located;
      const d = decl(pane.viewId);
      if (!d) return [];
      return [
        {
          instanceId: pane.instanceId,
          viewId: pane.viewId,
          title: paneTitle(d.title, pane.params),
          icon: d.icon ?? d.title[0],
          state: stateOf(frame, located),
          ...(location.kind === 'window' ? { windowId: location.windowId } : {}),
          location,
        },
      ];
    })
    .sort((a, b) => compareInstanceIds(a.instanceId, b.instanceId));
}

function stateOf(frame: FrameState, located: ReturnType<typeof listPanes>[number]): TaskbarState {
  const { pane, location } = located;
  if (location.kind === 'window') {
    const win = frame.windows.find((w) => w.id === location.windowId);
    if (!win) return 'hidden';
    if (win.mode === 'minimized') return 'minimized';
    // A merged window's background tabs are open but not showing, exactly like a
    // background tab in a centre area.
    if (win.area.tabs[win.area.activeTab]?.instanceId !== pane.instanceId) return 'hidden';
    return frame.focusedWindowId === win.id ? 'focused' : 'open';
  }
  if (location.kind === 'dock') {
    const dock = frame.docks[location.dock];
    if (!dock.visible || dock.activeTool !== pane.instanceId) return 'hidden';
    return frame.focusedInstanceId === pane.instanceId ? 'focused' : 'open';
  }
  // Minimized beats every other reading: the pane may still be its area's
  // `activeTab` (nothing else in the area was live to hand it to), and reporting
  // that as "focused" would offer to minimize something already minimized.
  if (pane.minimized) return 'minimized';
  // Centre area: showing only if it is its area's active tab.
  //
  // "Focused" here is the active tab of the **focused area**, not
  // `focusedInstanceId`. That field tracks DOM focus and only moves when
  // something is actually clicked or focused, so a pane opened by a command or
  // by the agent would never show as focused and its taskbar button would offer
  // to focus what you are already looking at.
  const area = findArea(frame.center, location.areaId);
  if (!area || area.tabs[area.activeTab]?.instanceId !== pane.instanceId) return 'hidden';
  return frame.focusedAreaId === location.areaId ? 'focused' : 'open';
}

/**
 * A pane's label. Falls back to the view's title, but prefers a `title` param —
 * that is how an editor buffer or a terminal says which file or shell it is, and
 * six buttons all reading "Editor" is a taskbar that tells you nothing.
 */
function paneTitle(declared: string, params?: Record<string, unknown>): string {
  const t = params?.title ?? params?.name ?? params?.path;
  return typeof t === 'string' && t.trim() ? t : declared;
}

/**
 * Order by the numeric suffix of `viewId#n` when both have one, falling back to
 * a plain string compare. Comparing the whole id as a string would put `#10`
 * before `#2`, so panes would visibly reshuffle the tenth time you opened one.
 */
function compareInstanceIds(a: string, b: string): number {
  const na = Number(a.split('#').pop());
  const nb = Number(b.split('#').pop());
  if (Number.isFinite(na) && Number.isFinite(nb) && na !== nb) return na - nb;
  return a.localeCompare(b);
}
