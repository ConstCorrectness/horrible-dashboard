/**
 * **Unsaved buffer content, keyed by source URI** — the editor's memory of work
 * that exists nowhere else.
 *
 * This started life as a private `Map` inside `BufferView`, and the reason it is
 * module-global rather than component state has not changed: a buffer unmounts far
 * more often than it closes. Only the active tab of an area renders, so switching
 * tabs to check something and coming back must not lose what you typed. Keyed by
 * *source*, not by pane instance, so the edits follow the file rather than the
 * window that happened to be showing it.
 *
 * It is a module of its own now because a **host** needs to read it. A workbench
 * pane draws a tab strip over files whose `BufferView` is unmounted — so
 * `getBuffer(uri)?.snapshot().dirty` (the mount-scoped registry in `buffers.ts`)
 * answers only for the one tab you are looking at, and every background tab would
 * silently lose its dirty dot. `isSourceDirty` is that question answered from the
 * one place that survives an unmount.
 *
 * The subscription exists for the same reason: nothing else would tell a tab strip
 * that the file *behind* it just became dirty.
 *
 * **`forgetUnsaved` is not optional cleanup.** Because this map outlives the
 * buffer, discarding an edit ("Don't Save") without clearing it here means the
 * discarded content comes back the next time that source is opened — the user
 * declined to save and got the change anyway.
 */

/** Content the user has typed that is not on disk, plus its proposal state. */
export interface UnsavedState {
  content: string;
  dirty: boolean;
  proposing: boolean;
  /** Pre-proposal text, so Decline can restore it. */
  original?: string;
  /** Whether the buffer was already dirty when the proposal arrived. */
  dirtyBeforeProposal?: boolean;
}

const unsaved = new Map<string, UnsavedState>();
const listeners = new Set<() => void>();

function notify(): void {
  for (const fn of listeners) fn();
}

/** The unsaved state for a source, or undefined when it matches disk. */
export function readUnsaved(uri: string): UnsavedState | undefined {
  return unsaved.get(uri);
}

/** Record unsaved content for a source. */
export function writeUnsaved(uri: string, state: UnsavedState): void {
  unsaved.set(uri, state);
  notify();
}

/**
 * Drop a source's unsaved content. Called on a successful save (it is on disk
 * now) and on a discard (it must not resurrect).
 */
export function forgetUnsaved(uri: string): void {
  if (unsaved.delete(uri)) notify();
}

/**
 * Whether a source has edits that are not on disk — answerable for a buffer that
 * is not currently mounted, which is the whole point.
 */
export function isSourceDirty(uri: string): boolean {
  return unsaved.get(uri)?.dirty ?? false;
}

/** Every source with unsaved edits, for a "save all before closing" walk. */
export function listDirtySources(): string[] {
  return [...unsaved.entries()].filter(([, s]) => s.dirty).map(([uri]) => uri);
}

/** Subscribe to unsaved-state changes (a tab strip's dirty dots). */
export function subscribeUnsaved(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
