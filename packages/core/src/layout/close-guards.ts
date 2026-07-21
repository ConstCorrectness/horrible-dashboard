/**
 * Pane close guards — the seam that lets a pane veto its own close so unsaved
 * work isn't lost. An editor buffer with unsaved changes registers a guard that
 * prompts (Save / Save As… / Don't Save / Cancel, VS Code-style) before the pane
 * is removed; every close path (`closePaneGuarded`, the UI tab/area/float close
 * buttons, `LayoutController.closePane`) runs the guard first.
 *
 * Guards are keyed by pane **instanceId**. A separate lightweight **dirty-flag**
 * set powers the `beforeunload` warning for closing the whole app/window — the
 * async guard dialog can't run during unload, so a native browser prompt stands in.
 */

/** Runs before a pane closes. Return true to proceed, false to keep it open. May
 * be async (e.g. to await a save dialog). */
export type CloseGuard = () => Promise<boolean> | boolean;

const guards = new Map<string, CloseGuard>();

/** Register a close guard for a pane instance. Returns an unregister function. */
export function registerCloseGuard(instanceId: string, guard: CloseGuard): () => void {
  guards.set(instanceId, guard);
  return () => {
    if (guards.get(instanceId) === guard) guards.delete(instanceId);
  };
}

/** Run a pane's guard, if any. Resolves true when the close should proceed. A
 * guard that throws is treated as "allow" so a bug can't wedge closing. */
export async function runCloseGuard(instanceId: string): Promise<boolean> {
  const guard = guards.get(instanceId);
  if (!guard) return true;
  try {
    return await guard();
  } catch {
    return true;
  }
}

// --- App-exit (beforeunload) protection ------------------------------------
//
// Panes flag themselves dirty here; if any is dirty when the tab/window is closed
// or reloaded, the browser shows its native "Leave site? Changes may not be
// saved" prompt. (In the Tauri desktop shell the same flag can gate a native
// window-close confirmation.)

const dirtyPanes = new Set<string>();

/** Mark a pane instance dirty (has unsaved changes) or clean. */
export function setPaneDirty(instanceId: string, dirty: boolean): void {
  if (dirty) dirtyPanes.add(instanceId);
  else dirtyPanes.delete(instanceId);
}

/** Whether any pane currently has unsaved changes. */
export function anyPaneDirty(): boolean {
  return dirtyPanes.size > 0;
}

/** Whether one pane instance has unsaved changes — the "is this pane safe to
 * take over?" test behind `openDocument`'s reuse rule. */
export function isPaneDirty(instanceId: string): boolean {
  return dirtyPanes.has(instanceId);
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', (e: BeforeUnloadEvent) => {
    if (!anyPaneDirty()) return;
    // Setting returnValue triggers the browser's native unsaved-changes prompt;
    // the string is ignored by modern browsers but required to be truthy.
    e.preventDefault();
    e.returnValue = '';
  });
}
