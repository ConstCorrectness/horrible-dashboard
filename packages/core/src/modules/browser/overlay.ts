/**
 * Suppression registry for **native overlays** — surfaces the OS composites above
 * the HTML layer, which today means the embedded browser's native child webview
 * (`browser.nativeWebview`, apps/desktop/src-tauri/src/webview.rs).
 *
 * ## Why this has to exist
 *
 * A native child webview is not a DOM node. It is a sibling surface painted over
 * the window by the OS compositor, so **no z-index reaches it**: the command
 * palette, a modal, a dropdown, a drag preview — all of them render *underneath*
 * a native overlay no matter what CSS says. The only way to draw over that region
 * is to hide the overlay while you need it.
 *
 * So anything that renders full-window UI must claim suppression for as long as
 * it's up:
 *
 * ```ts
 * useEffect(() => (open ? suppressNativeOverlays() : undefined), [open]);
 * ```
 *
 * Reference-counted, because two overlays can be up at once (a dropdown inside a
 * modal) and the first one closing must not un-hide the webview under the second.
 *
 * Deliberately in `core` rather than the browser module: the callers are shell
 * components in `packages/ui` that must not import a module's internals, and a
 * future native surface (a video overlay, a native map) would register here too.
 */

type Listener = (suppressed: boolean) => void;

let count = 0;
const listeners = new Set<Listener>();

function notify(): void {
  const suppressed = count > 0;
  listeners.forEach((fn) => fn(suppressed));
}

/**
 * Hide every native overlay until the returned release is called.
 *
 * The release is idempotent — a component that releases in both an effect cleanup
 * and an explicit close handler must not drive the count negative, which would
 * leave a later suppression unable to take effect.
 */
export function suppressNativeOverlays(): () => void {
  count += 1;
  if (count === 1) notify();
  let released = false;
  return () => {
    if (released) return;
    released = true;
    count -= 1;
    if (count === 0) notify();
  };
}

/** Whether any caller currently holds suppression. */
export function nativeOverlaysSuppressed(): boolean {
  return count > 0;
}

/** Subscribe to suppression changes; returns an unsubscribe. */
export function subscribeNativeOverlaySuppression(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}
