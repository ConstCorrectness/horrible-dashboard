/**
 * The **buffer host** seam: a surface that renders buffers itself instead of as
 * panes, and therefore wants to claim `openBuffer` before the layout does.
 *
 * The IDE workbench is the one implementation — it draws an open-file tab strip
 * over a single nested `BufferView`. Without this seam, a file clicked in the
 * workbench's own Explorer strip splits off a *second* `editor.buffer` pane
 * holding the same URI: two live CodeMirror views registered in `buffers.ts`
 * under one source, where saving is last-writer-wins.
 *
 * It lives in its own file rather than in `index.tsx` for a boring but real
 * reason: `index.tsx` calls `subscribeLocus()` at module scope, which opens a
 * WebSocket, so importing it from a unit test fails outright. The seam is the
 * part with rules worth pinning, so it is importable on its own.
 *
 * Both methods return false to mean "not mine", so a closed workbench is
 * invisible and every caller falls through to the normal pane routing.
 */
import type { OpenBufferOptions } from './openOptions';

export interface BufferHost {
  /** Open `source` here. False ⇒ fall through to pane routing. */
  open(source: string, opts?: OpenBufferOptions): boolean;
  /**
   * A buffer changed identity — an untitled buffer that was just saved to a real
   * file. `instanceId` is the id the host handed the nested view. False ⇒ not
   * mine, in which case the caller retargets a real pane instead.
   */
  adopt(instanceId: string, uri: string): boolean;
}

let host: BufferHost | null = null;

/** Install the host, or clear it with null when the surface unmounts. */
export function setBufferHost(next: BufferHost | null): void {
  host = next;
}

/** Whether a host is currently claiming opens. */
export function hasBufferHost(): boolean {
  return host !== null;
}

/**
 * Offer an open to the host. True means it took it and the caller must stop —
 * `openBuffer`'s first line.
 */
export function routeOpenToHost(source: string, opts?: OpenBufferOptions): boolean {
  return host?.open(source, opts) ?? false;
}

/**
 * Offer a rename to the host. `BufferView`'s Save As asks this before falling
 * back to `retargetPane`, which cannot find a host's synthetic instance id and
 * would leave a saved file sitting in a tab still reading `untitled`.
 */
export function adoptByHost(instanceId: string | null, uri: string): boolean {
  if (!instanceId) return false;
  return host?.adopt(instanceId, uri) ?? false;
}
