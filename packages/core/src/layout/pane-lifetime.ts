/**
 * Resources that belong to a **pane**, not to a mounted component.
 *
 * A pane unmounts far more often than it closes. Only the active tab of an area
 * renders, and switching workspace replaces the whole frame — so looking at
 * another workspace for four seconds unmounts every pane in the one you left.
 * Components could not tell those apart from a real close, so their cleanups
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
 * **Keys are workspace-scoped**, and that is load-bearing. `paneSeq` lives on
 * `FrameState`, so instance ids are only unique *within* a frame — two workspaces
 * can each hold a `terminal.instance#1`. A globally keyed store would hand the
 * second one the first one's shell.
 *
 * Disposal is driven by explicit close paths (`closePaneGuarded`,
 * `changePaneType`, workspace delete, layout reset), never by a timer. If a path
 * is ever missed the resource leaks until the page closes — at which point the
 * backend reaps it anyway, since PTYs die with their `/ws` connection. That is a
 * strictly better failure than killing a live shell on every tab switch.
 */

interface Entry {
  value: unknown;
  dispose: () => void;
}

const live = new Map<string, Entry>();

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

/** Dispose one pane's resource. Called from the layout's close paths. */
export function closePaneSession(key: string): void {
  const entry = live.get(key);
  if (!entry) return;
  live.delete(key);
  entry.dispose();
}

/**
 * Dispose every resource of a workspace except the panes in `keep`.
 *
 * For workspace delete (`keep` empty) and layout reset, where panes disappear
 * without passing through the single-pane close path.
 */
export function closeWorkspaceSessions(
  workspaceId: string | null,
  keep: ReadonlySet<string> = new Set(),
): void {
  const prefix = `${workspaceId ?? '-'}::`;
  for (const key of [...live.keys()]) {
    if (!key.startsWith(prefix)) continue;
    if (keep.has(key.slice(prefix.length))) continue;
    closePaneSession(key);
  }
}

/** Test-only: drop everything without disposing (mirrors `layoutStore.resetForTests`). */
export function resetPaneSessionsForTests(): void {
  live.clear();
}
