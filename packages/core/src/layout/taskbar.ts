/**
 * Taskbar derivation: what the window buttons say, and what clicking one does.
 *
 * Pure over `FrameState` + the registry — the taskbar should have no opinion of
 * its own about what is open. The rendering half lives in
 * packages/ui/src/desktop/taskbar/.
 *
 * See docs/architecture/desktop-shell.mdx.
 */
import { registry } from '../registry';
import { listPanes } from './model';
import type { FrameState, PaneLocation } from './types';

/**
 * A taskbar button's current meaning. Note that a taskbar button is per **pane
 * instance**, not per window: a window merged into three tabs is three things
 * the user can switch to, and one button for it would make two of them
 * unreachable from here.
 */
export type TaskbarState =
  /** The active tab of the focused window. Click minimizes it. */
  | 'focused'
  /** Open and on screen, but not focused. Click focuses. */
  | 'open'
  /** In a minimized window. Click restores. */
  | 'minimized'
  /** Open but not showing: a background tab of its window. */
  | 'hidden';

export interface TaskbarEntry {
  instanceId: string;
  viewId: string;
  title: string;
  icon: string;
  state: TaskbarState;
  /** The pane's background work finished while it was out of sight. */
  attention: boolean;
  /** The pane's window — the taskbar's minimize target. */
  windowId: string;
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
const DEFAULT_ICONS: Record<string, string> = {
  observability: '📡',
  localtrack: '∿',
  agent: '🤖',
  browser: '🌐',
  editor: '✎',
  terminal: '',
  files: '📁',
  explorer: '🗂',
  database: '🛢',
  notebook: '📓',
  training: '⚡',
  git: '⎇',
  github: '🐙',
  settings: '⚙',
  repl: 'λ',
  flow: '⬡',
  research: '🔬',
  karaoke: '🎤',
  music: '🎵',
  hassault: '🎯',
  games: '🕹',
  mcp: '🔌',
  skills: '🧩',
  scratch: '📝',
  records: '▦',
  code: '≡',
  dashboard: '👋',
};

export function resolveViewIcon(viewId: string, declaredIcon?: string, title?: string): string {
  if (declaredIcon && declaredIcon.trim()) return declaredIcon;
  const prefix = viewId.split('.')[0];
  if (DEFAULT_ICONS[prefix]) return DEFAULT_ICONS[prefix];
  return title && title.length > 0 ? title[0] : '❐';
}

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
          icon: resolveViewIcon(pane.viewId, d.icon, d.title),
          state: stateOf(frame, located),
          attention: pane.attention ?? false,
          windowId: location.windowId,
          location,
        },
      ];
    })
    .sort((a, b) => compareInstanceIds(a.instanceId, b.instanceId));
}

function stateOf(frame: FrameState, located: ReturnType<typeof listPanes>[number]): TaskbarState {
  const { pane, location } = located;
  const win = frame.windows.find((w) => w.id === location.windowId);
  if (!win) return 'hidden';
  if (win.mode === 'minimized') return 'minimized';
  // A merged window's background tabs are open but not showing.
  if (win.area.tabs[win.area.activeTab]?.instanceId !== pane.instanceId) return 'hidden';
  return frame.focusedWindowId === win.id ? 'focused' : 'open';
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
// Kept as a local because the caller already holds the decl; `paneDisplayTitle`
// in the controller is the same rule for callers that hold only a pane.

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
