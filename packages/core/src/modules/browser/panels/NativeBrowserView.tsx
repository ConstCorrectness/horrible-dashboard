/**
 * Native-webview viewport (desktop only, `browser.nativeWebview`).
 *
 * Renders **nothing but a placeholder**: the actual page lives in a real child
 * webview the Tauri shell composites over this element's rectangle. That's the whole
 * point — no iframe restrictions (`X-Frame-Options`/CSP can't refuse it), no frame
 * streaming, no decode cost, and native scrolling and input at native speed.
 *
 * The price is that the overlay sits **above** the HTML layer and cannot be
 * z-indexed under anything. Three things follow, and all three are handled here:
 *
 * 1. **Geometry has to be pushed, not inherited.** A `ResizeObserver` on the
 *    placeholder plus a scroll/resize listener keep the native surface glued to
 *    where this div actually is.
 * 2. **Anything drawing over this region must hide it.** The overlay subscribes to
 *    `suppressNativeOverlays()` (see ../overlay.ts), which the palette and modals
 *    claim while they're up.
 * 3. **Invisible is not the same as unmounted.** A pane on a non-visible workspace
 *    still holds its native surface, which would otherwise float over whatever
 *    replaced it — so an `IntersectionObserver` and `visibilitychange` hide it too.
 *
 * The overlay's lifetime is the **pane's**, not the component's: a workspace switch
 * unmounts panes (see layout/pane-lifetime and the `unmount-is-not-close` rule), and
 * tearing down the webview there would drop the user's page, scroll position and any
 * logged-in state. `usePaneSession` ties destruction to the pane closing instead.
 */
import { useCallback, useContext, useEffect, useRef, useState } from 'react';

import { PaneInstanceContext } from '../../../agent-context';
import { usePaneSession } from '../../../layout/use-pane-session';
import { windowControl, type WebviewBounds } from '../../../window';
import { subscribeNativeOverlaySuppression, nativeOverlaysSuppressed } from '../overlay';

/** Bounds are only pushed when they actually move — sub-pixel jitter is noise. */
function sameBounds(a: WebviewBounds | null, b: WebviewBounds): boolean {
  return (
    a != null &&
    Math.abs(a.x - b.x) < 1 &&
    Math.abs(a.y - b.y) < 1 &&
    Math.abs(a.width - b.width) < 1 &&
    Math.abs(a.height - b.height) < 1
  );
}

export function NativeBrowserView({
  url,
  navSeq,
  onError,
}: {
  url: string;
  /** Bumped by the parent to (re)issue navigation to `url` (also covers reload). */
  navSeq: number;
  onError: (message: string) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const paneInstanceId = useContext(PaneInstanceContext);
  // Falls back to a per-component id if the pane has no instance id (shouldn't
  // happen in the frame, but an unkeyed overlay would collide with another pane's).
  const idRef = useRef<string>('');
  if (!idRef.current) {
    idRef.current = paneInstanceId || `browser-${Math.random().toString(36).slice(2)}`;
  }
  const id = idRef.current;

  const control = windowControl()?.browserWebview ?? null;
  const lastBounds = useRef<WebviewBounds | null>(null);
  // Whether the shell has actually created the surface. Bounds/navigate calls
  // before that would reject with "no native browser webview".
  const createdRef = useRef(false);
  const [visible, setVisible] = useState(true);

  // Own the native surface for the life of the *pane*, not this component.
  const session = usePaneSession(
    () => ({ id, control }),
    (held) => {
      held.control?.close(held.id).catch(() => {
        // Shutdown races (window already gone) are not worth surfacing — the
        // shell drops the registry entry either way.
      });
    },
  );

  const measure = useCallback((): WebviewBounds | null => {
    const el = hostRef.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    return { x: rect.left, y: rect.top, width: rect.width, height: rect.height };
  }, []);

  // --- create + navigate ---------------------------------------------------
  useEffect(() => {
    if (!control || !session || !url) return;
    const bounds = measure();
    if (!bounds) return;
    lastBounds.current = bounds;
    if (!createdRef.current) {
      createdRef.current = true;
      control.create(id, url, bounds).catch((e: Error) => {
        createdRef.current = false;
        onError(e.message || 'could not create the native browser view');
      });
      return;
    }
    control.navigate(id, url).catch((e: Error) => onError(e.message));
    // navSeq re-issues navigation for reload/home even when `url` is unchanged.
  }, [control, session, url, navSeq, id, measure, onError]);

  // --- geometry ------------------------------------------------------------
  useEffect(() => {
    const el = hostRef.current;
    if (!control || !el) return;
    const push = () => {
      if (!createdRef.current) return;
      const bounds = measure();
      if (!bounds || sameBounds(lastBounds.current, bounds)) return;
      lastBounds.current = bounds;
      control.updateBounds(id, bounds).catch(() => {
        // A bounds push racing a close is expected; the next one re-syncs.
      });
    };
    const observer = new ResizeObserver(push);
    observer.observe(el);
    // ResizeObserver fires on size, not position: a sibling pane collapsing or an
    // ancestor scrolling moves this element without resizing it.
    window.addEventListener('resize', push);
    window.addEventListener('scroll', push, true);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', push);
      window.removeEventListener('scroll', push, true);
    };
  }, [control, id, measure]);

  // --- visibility ----------------------------------------------------------
  // Three independent reasons to yield the region, combined into one flag.
  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    let onScreen = true;
    let suppressed = nativeOverlaysSuppressed();
    const apply = () => setVisible(onScreen && !suppressed && !document.hidden);

    const io = new IntersectionObserver((entries) => {
      onScreen = entries.some((e) => e.isIntersecting);
      apply();
    });
    io.observe(el);
    const unsub = subscribeNativeOverlaySuppression((s) => {
      suppressed = s;
      apply();
    });
    document.addEventListener('visibilitychange', apply);
    apply();
    return () => {
      io.disconnect();
      unsub();
      document.removeEventListener('visibilitychange', apply);
    };
  }, []);

  useEffect(() => {
    if (!control || !createdRef.current) return;
    control.setVisible(id, visible).catch(() => {
      // Racing a close; the next create re-establishes the correct state.
    });
  }, [control, id, visible]);

  if (!control) {
    // Capability was granted but the seam is missing — a wiring bug, not a user
    // error, so say so plainly rather than rendering an empty pane.
    return (
      <div style={{ padding: '2rem', color: 'var(--text-dim)' }}>
        The native browser view is unavailable on this host.
      </div>
    );
  }

  return (
    <div
      ref={hostRef}
      // The native surface covers this element; the background only shows in the
      // moment before it is created, or while it is hidden for an overlay.
      style={{ width: '100%', height: '100%', background: '#111' }}
    />
  );
}
