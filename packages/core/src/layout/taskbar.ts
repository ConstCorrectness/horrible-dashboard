/**
 * Pane-switcher derivation: every open pane, what it is called, and whether the
 * user can see it. Read by the status bar's agent button and Spotlight's open
 * pane results.
 *
 * Pure over `FrameState` + the registry — the switcher should have no opinion of
 * its own about what is open.
 *
 * Named for the taskbar of the floating desktop, whose window buttons it fed.
 */
import { registry } from '../registry';
import { listPanes, visiblePanes } from './model';
import type { FrameState, PaneLocation } from './types';

/** Whether a pane is on screen. Per **pane instance**, not per group. */
export type TaskbarState =
  /** The focused pane. */
  | 'focused'
  /** On screen, but not focused. */
  | 'open'
  /** In a hidden dock. */
  | 'minimized'
  /** Open but not showing: a background tab, or behind a maximized group. */
  | 'hidden';

export interface TaskbarEntry {
  instanceId: string;
  viewId: string;
  title: string;
  icon: string;
  state: TaskbarState;
  /** The pane's background work finished while it was out of sight. */
  attention: boolean;
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
          location,
        },
      ];
    })
    .sort((a, b) => compareInstanceIds(a.instanceId, b.instanceId));
}

function stateOf(frame: FrameState, located: ReturnType<typeof listPanes>[number]): TaskbarState {
  const { pane, location } = located;
  if (location.kind === 'dock' && !frame.docks[location.side].visible) return 'minimized';
  if (!visiblePanes(frame).some((p) => p.pane.instanceId === pane.instanceId)) return 'hidden';
  return frame.focusedInstanceId === pane.instanceId ? 'focused' : 'open';
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
