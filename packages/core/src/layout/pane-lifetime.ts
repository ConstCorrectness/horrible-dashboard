/**
 * Resources that belong to a **pane**, not to a mounted component.
 *
 * A pane unmounts far more often than it closes: only the active tab of a group
 * renders, so looking at another tab for four seconds unmounts the one you left.
 * Components could not tell that apart from a real close, so their cleanups
 * assumed the destructive reading: `TerminalPane` killed its PTY, the browser
 * released the last reference to its Chromium engine. Switching tabs destroyed
 * running processes.
 *
 * **Unmount is not close.** This module is where that distinction lives: a
 * resource created through `paneSession` survives every unmount and is disposed
 * only when the layout says the pane genuinely went away. The pattern is not new
 * — notebook and training kernels are already process-global for exactly this
 * reason ("closing a pane leaves the kernel running; reopening reattaches"); this
 * generalizes it to the frontend.
 *
 * **Keys are workspace-scoped.** `paneSeq` lives on `FrameState`, so instance ids
 * are only unique *within* a frame. There is one workspace now, but the key still
 * says which layout a resource belongs to.
 *
 * Disposal is driven by explicit close paths (`closePaneGuarded`,
 * `changePaneType`), never by a timer. If a path
 * is ever missed the resource leaks until the page closes — at which point the
 * backend reaps it anyway, since PTYs die with their `/ws` connection. That is a
 * strictly better failure than killing a live shell on every tab switch.
 */

interface Entry {
  value: unknown;
  dispose: () => void;
}

const live = new Map<string, Entry>();

/**
 * Per-pane **view** state — scroll offsets, cursor positions, which cell was being
 * edited. See `use-pane-ui-state.ts` for what belongs here and why it is not
 * persisted.
 *
 * A second map rather than a second `Entry` with a no-op dispose, so that a module
 * which only wants to remember a scroll offset does not acquire a lifetime resource
 * whose disposal contract it never wrote. It is keyed identically, and every path
 * below that drops a session drops this too — a leak here is invisible (nothing
 * observable stays alive), which is exactly why it has to be handled by
 * construction rather than noticed later.
 */
const uiState = new Map<string, Map<string, unknown>>();

/** The pane's view-state bag, created empty on first use. */
export function paneUiBag(key: string): Map<string, unknown> {
  let bag = uiState.get(key);
  if (!bag) {
    bag = new Map();
    uiState.set(key, bag);
  }
  return bag;
}

/**
 * A region strip's view renders under a synthetic instance id,
 * `${host}:${position}:${viewId}` (ui/layout/Region.tsx), so its sessions belong to
 * the host pane: closed, moved and kept with it. The agent chat's docked browser is
 * one, and its session holds a live native webview.
 */
const REGION_CHILD = /:(left|right|bottom):[^:]+$/;

/** The pane a region view's instance id rides on, or the id itself for a pane. */
export function regionHost(instanceId: string): string {
  return instanceId.replace(REGION_CHILD, '');
}

/** Every session key belonging to `key`'s pane: its own, then its region views'. */
function familyOf(key: string): string[] {
  const keys = [...new Set([...live.keys(), ...uiState.keys()])];
  return [key, ...keys.filter((k) => k !== key && k.startsWith(`${key}:`) && REGION_CHILD.test(k))];
}

/** The stable identity of a pane across the whole app. */
export function paneSessionKey(workspaceId: string | null, instanceId: string): string {
  return `${workspaceId ?? '-'}::${instanceId}`;
}

/**
 * The pane's resource, created on first use and reused by every later mount.
 *
 * `create` runs once per pane, not once per mount — so anything it does (spawning
 * a PTY, typing an `initialCommand`, opening a socket) happens exactly once, and
 * a remount reattaches to what is already there.
 */
export function paneSession<T>(key: string, create: () => T, dispose: (value: T) => void): T {
  const existing = live.get(key);
  if (existing) return existing.value as T;
  const value = create();
  live.set(key, { value, dispose: () => dispose(value) });
  return value;
}

/** Whether this pane already has a live resource (i.e. this is a remount). */
export function hasPaneSession(key: string): boolean {
  return live.has(key);
}

/**
 * Dispose one pane's resources, its region views' included. Called from the
 * layout's close paths.
 */
export function closePaneSession(key: string): void {
  for (const k of familyOf(key)) {
    uiState.delete(k);
    const entry = live.get(k);
    if (!entry) continue;
    live.delete(k);
    entry.dispose();
  }
}

/** Test-only: drop everything without disposing (mirrors `layoutStore.resetForTests`). */
export function resetPaneSessionsForTests(): void {
  live.clear();
  uiState.clear();
}
