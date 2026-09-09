/**
 * The workbench's **open files** — the model behind its tab strip.
 *
 * It lives in the host pane's `params`, which is a deliberate choice among three
 * places it could have gone:
 *
 * - **Not component state.** Only the active tab of an area renders, so the
 *   workbench unmounts whenever you look at another document, and its tabs would
 *   come back empty.
 * - **Not `paneUiBag`.** That store is explicitly *not* persisted — right for a
 *   scroll offset, wrong for which files you have open, which is arrangement and
 *   should survive a reload the way VS Code's does.
 * - **Params**, which `serialize.ts` writes into the workspace blob, are keyed by
 *   pane instance (so two workspaces each keep their own set) and are dropped when
 *   the pane genuinely closes.
 *
 * Note `SET_PANE_PARAMS` **replaces** the params object rather than merging, so
 * every mutator here reads the current params first and spreads them. Reading a
 * stale copy is how the `title` a pane was opened with silently disappears.
 */
import { layoutStore } from '../../layout/store';
import { findPaneAnywhere } from '../../layout/model';

/** The tab strip's whole state: the open sources, in order, and the active one. */
export interface WorkbenchTabs {
  /** Source URIs (`workspace-file:<path>`, `note:<id>`), in tab order. */
  tabs: string[];
  /** The source being rendered, or null when nothing is open. */
  active: string | null;
}

const EMPTY: WorkbenchTabs = { tabs: [], active: null };

function paramsOf(hostInstanceId: string): Record<string, unknown> | null {
  const located = findPaneAnywhere(layoutStore.getSnapshot().frame, hostInstanceId);
  return located ? (located.pane.params ?? {}) : null;
}

/** The open tabs of a workbench instance (empty when it isn't open). */
export function readTabs(hostInstanceId: string): WorkbenchTabs {
  const params = paramsOf(hostInstanceId);
  if (!params) return EMPTY;
  const tabs = Array.isArray(params.tabs) ? (params.tabs as unknown[]).filter(isUri) : [];
  const active =
    typeof params.active === 'string' && tabs.includes(params.active)
      ? params.active
      : (tabs[0] ?? null);
  return { tabs, active };
}

function isUri(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0;
}

/** Write tabs back, preserving every other param the pane was opened with. */
function writeTabs(hostInstanceId: string, next: WorkbenchTabs): void {
  const params = paramsOf(hostInstanceId);
  if (!params) return;
  layoutStore.dispatch({
    type: 'SET_PANE_PARAMS',
    instanceId: hostInstanceId,
    params: { ...params, tabs: next.tabs, active: next.active },
  });
}

/**
 * Open a source as a tab and activate it. Already-open sources are **activated,
 * not duplicated** — the same focus-or-create rule `openBuffer` applies to panes,
 * and the reason a file clicked twice in the tree does not accumulate tabs.
 */
export function openTab(hostInstanceId: string, uri: string): void {
  const { tabs, active } = readTabs(hostInstanceId);
  if (active === uri) return;
  writeTabs(hostInstanceId, {
    tabs: tabs.includes(uri) ? tabs : [...tabs, uri],
    active: uri,
  });
}

/** Make an already-open tab the active one. */
export function activateTab(hostInstanceId: string, uri: string): void {
  const state = readTabs(hostInstanceId);
  if (!state.tabs.includes(uri) || state.active === uri) return;
  writeTabs(hostInstanceId, { ...state, active: uri });
}

/**
 * Remove a tab. When it was the active one, activate its **neighbour** — the tab
 * to its right, or the new last tab when it was rightmost. Falling back to index
 * 0 would throw you to the far end of the strip every time you closed something.
 *
 * Purely a model operation: whether the content may be discarded is the caller's
 * question, and `IdeWorkbench` asks it before calling this.
 */
export function closeTab(hostInstanceId: string, uri: string): void {
  const { tabs, active } = readTabs(hostInstanceId);
  const index = tabs.indexOf(uri);
  if (index < 0) return;
  const remaining = tabs.filter((t) => t !== uri);
  const nextActive =
    active === uri ? (remaining[index] ?? remaining[remaining.length - 1] ?? null) : active;
  writeTabs(hostInstanceId, { tabs: remaining, active: nextActive });
}

/** Reorder a tab (drag within the strip). */
export function moveTab(hostInstanceId: string, uri: string, index: number): void {
  const state = readTabs(hostInstanceId);
  const from = state.tabs.indexOf(uri);
  if (from < 0) return;
  const tabs = state.tabs.filter((t) => t !== uri);
  tabs.splice(Math.max(0, Math.min(tabs.length, index)), 0, uri);
  writeTabs(hostInstanceId, { ...state, tabs });
}

/**
 * Rename a tab in place — an untitled buffer that was just saved to a real file.
 * The position and active state are preserved, because Save As must not feel like
 * closing a tab and opening another.
 */
export function renameTab(hostInstanceId: string, from: string, to: string): boolean {
  const state = readTabs(hostInstanceId);
  const index = state.tabs.indexOf(from);
  if (index < 0) return false;
  const tabs = [...state.tabs];
  // The target may already be open elsewhere in the strip; collapse onto it
  // rather than listing the same file twice.
  const existing = tabs.indexOf(to);
  tabs[index] = to;
  if (existing >= 0 && existing !== index) tabs.splice(existing, 1);
  writeTabs(hostInstanceId, {
    tabs,
    active: state.active === from ? to : state.active,
  });
  return true;
}
