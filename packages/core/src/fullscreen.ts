/**
 * App-window fullscreen, across both layouts.
 *
 * Two mechanisms, one verb. Under a native shell the OS window itself goes
 * borderless-fullscreen through the {@link windowControl} seam; in the browser
 * there is no OS window to drive, so the page element goes fullscreen through
 * the DOM Fullscreen API instead. Callers ask for "fullscreen" and never learn
 * which one they got.
 *
 * This is deliberately **not** the same thing as a pane filling the app
 * (`presentPane` / the frame's fullscreen area). That one covers the shell's own
 * chrome; this one covers the OS's. A presented pane on a native shell escalates
 * to this, which is why both live behind small functions rather than inline
 * `windowControl()` calls scattered through the shell.
 *
 * See docs/architecture/desktop-shell.mdx.
 */
import { windowControl } from './window';

type Listener = () => void;
const listeners = new Set<Listener>();

/** Whether the DOM Fullscreen API is usable in this document. */
function domFullscreenAvailable(): boolean {
  return (
    typeof document !== 'undefined' &&
    typeof document.documentElement?.requestFullscreen === 'function' &&
    document.fullscreenEnabled !== false
  );
}

/**
 * Is the app currently fullscreen?
 *
 * Async because the native answer is an IPC round trip. The DOM branch is
 * synchronous underneath, so `subscribeFullscreen` exists for rendering — a
 * button that only ever polls this shows a stale icon when the user leaves
 * fullscreen with the OS's own gesture.
 */
export async function isAppFullscreen(): Promise<boolean> {
  const wc = windowControl();
  if (wc) return wc.isFullscreen();
  return typeof document !== 'undefined' && document.fullscreenElement !== null;
}

/**
 * Put the *document element* into fullscreen, or take it out.
 *
 * Separate from the OS window on purpose. Keyboard Lock activates only in
 * **JavaScript-initiated document fullscreen** — the spec excludes F11 and, by
 * extension, a native shell's borderless window, which never sets
 * `document.fullscreenElement`. So under Tauri this runs *in addition to* the
 * window going fullscreen: visually a no-op (the webview already fills the
 * screen), but it is the precondition `canHoldEscape` and system-key capture are
 * gated on. Without it the desktop layout — the one that most wants to hold the
 * keyboard — silently had the weaker behaviour of the two.
 *
 * Failure is expected and ignored: the API refuses outside a user gesture, and a
 * webview may not implement it at all. Callers read the real state afterwards.
 */
async function setDocumentFullscreen(value: boolean): Promise<void> {
  if (!domFullscreenAvailable()) return;
  try {
    if (value) {
      if (!document.fullscreenElement) await document.documentElement.requestFullscreen();
    } else if (document.fullscreenElement) {
      await document.exitFullscreen();
    }
  } catch {
    /* not permitted here, or unsupported — the caller reports the true state */
  }
}

/** Set app fullscreen; resolves to the state actually applied. */
export async function setAppFullscreen(value: boolean): Promise<boolean> {
  const wc = windowControl();
  if (wc) {
    const applied = await wc.setFullscreen(value);
    // Match the document to the window, so Keyboard Lock is available on desktop.
    // Driven off what actually happened, not what was asked for.
    await setDocumentFullscreen(applied);
    return applied;
  }
  if (!domFullscreenAvailable()) return document?.fullscreenElement !== null;
  // A rejection here is normal rather than exceptional: the Fullscreen API
  // refuses outside a user gesture, and the browser's own permission UI has
  // already told the user. Report the state we actually ended in.
  try {
    if (value) await document.documentElement.requestFullscreen();
    else if (document.fullscreenElement) await document.exitFullscreen();
  } catch {
    /* fall through to the real state below */
  }
  return document.fullscreenElement !== null;
}

/** Flip app fullscreen; resolves to the new state. */
export async function toggleAppFullscreen(): Promise<boolean> {
  const wc = windowControl();
  if (wc) {
    const applied = await wc.toggleFullscreen();
    await setDocumentFullscreen(applied);
    return applied;
  }
  return setAppFullscreen(document?.fullscreenElement === null);
}

/**
 * Notify on fullscreen changes.
 *
 * The DOM branch has a real event, so a control renders the truth even when the
 * user left fullscreen by pressing Escape or F11 rather than clicking. The
 * native branch has no such event — tao does not push one through — so a caller
 * there re-reads {@link isAppFullscreen} after its own toggle and is otherwise
 * only wrong if the user used the OS's window menu. Returns an unsubscribe.
 */
export function subscribeFullscreen(listener: Listener): () => void {
  listeners.add(listener);
  if (listeners.size === 1 && typeof document !== 'undefined') {
    document.addEventListener('fullscreenchange', emit);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && typeof document !== 'undefined') {
      document.removeEventListener('fullscreenchange', emit);
    }
  };
}

function emit(): void {
  for (const l of listeners) l();
}
