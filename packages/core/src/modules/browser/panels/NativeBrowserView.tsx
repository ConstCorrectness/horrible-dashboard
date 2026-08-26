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
 * 1. **Geometry has to be pushed, not inherited**, and *position* is the half that
 *    has no event. `ResizeObserver` fires on size; `resize`/`scroll` fire on the
 *    viewport. Dragging a desktop window moves this element without any of them —
 *    the rect changes every frame and nothing announces it, so the overlay stayed
 *    parked where the window used to be while the pane slid out from under it.
 *    The watcher below therefore *samples* the rect: an animation frame loop while
 *    anything is moving, backing off to a slow poll once it settles.
 * 2. **Anything drawing over this region must hide it.** The overlay subscribes to
 *    `suppressNativeOverlays()` (see ../overlay.ts), which the palette and modals
 *    claim while they're up.
 * 3. **Invisible is not the same as unmounted.** A pane on a non-visible workspace
 *    still holds its native surface, which would otherwise float over whatever
 *    replaced it — so an `IntersectionObserver` and `visibilitychange` hide it too,
 *    and so does this component unmounting (hide, never close — see below). That
 *    last one covers the pane outliving the component: switching to reader mode or
 *    another engine leaves a live surface over its own replacement.
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

/**
 * Frames of stillness before the geometry watcher stops sampling every frame. A
 * window drag pauses mid-gesture, so this has to outlast a hesitation; ~0.3s does.
 */
const STILL_FRAMES = 20;

/**
 * How often the parked watcher re-measures. The backstop for movement that fires
 * none of the events below (a layout written straight to the store, a pane moved by
 * an agent tool), so it trades a quarter second of lag for an idle cost of nothing.
 */
const IDLE_MS = 250;

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
  // Sampled, not event-driven — see note 1. `push` is cheap (one
  // `getBoundingClientRect` plus a compare) and only crosses to the shell when the
  // rect actually moved, so the parked poll costs effectively nothing.
  useEffect(() => {
    const el = hostRef.current;
    if (!control || !el) return;
    let raf = 0;
    let timer = 0;
    let still = 0;
    let stopped = false;

    const push = (): boolean => {
      if (!createdRef.current) return false;
      const bounds = measure();
      if (!bounds || sameBounds(lastBounds.current, bounds)) return false;
      lastBounds.current = bounds;
      control.updateBounds(id, bounds).catch(() => {
        // A bounds push racing a close is expected; the next one re-syncs.
      });
      return true;
    };

    // Parked: the layout is at rest, so poll slowly. Anything that moves the pane
    // without firing an event we listen for is picked up within IDLE_MS.
    const park = () => {
      if (stopped) return;
      timer = window.setTimeout(() => {
        timer = 0;
        if (push()) wake();
        else park();
      }, IDLE_MS);
    };

    const frame = () => {
      raf = 0;
      still = push() ? 0 : still + 1;
      if (still < STILL_FRAMES) raf = requestAnimationFrame(frame);
      else park();
    };

    // Anything that might be the start of movement drops us into frame-rate
    // tracking; the loop decides for itself when the motion is over.
    const wake = () => {
      if (stopped || raf) return;
      if (timer) {
        clearTimeout(timer);
        timer = 0;
      }
      still = 0;
      raf = requestAnimationFrame(frame);
    };

    const observer = new ResizeObserver(wake);
    observer.observe(el);
    window.addEventListener('resize', wake);
    window.addEventListener('scroll', wake, true);
    // A window/sash drag is a pointer gesture: waking on the pointer means the
    // overlay is already tracking by the first frame of the move.
    window.addEventListener('pointerdown', wake, true);
    window.addEventListener('pointermove', wake, true);
    window.addEventListener('transitionend', wake, true);
    window.addEventListener('animationend', wake, true);
    wake();

    return () => {
      stopped = true;
      if (raf) cancelAnimationFrame(raf);
      if (timer) clearTimeout(timer);
      observer.disconnect();
      window.removeEventListener('resize', wake);
      window.removeEventListener('scroll', wake, true);
      window.removeEventListener('pointerdown', wake, true);
      window.removeEventListener('pointermove', wake, true);
      window.removeEventListener('transitionend', wake, true);
      window.removeEventListener('animationend', wake, true);
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

  // Unmount hides — it must not close (the page, its scroll and its login survive a
  // workspace switch; that is what `usePaneSession` is for). But it must not leave
  // the surface up either: this component unmounts while its *pane* lives on, when
  // the pane switches to reader mode or another engine, or when the whole workspace
  // is replaced. A visible overlay then floats over whatever took its place, which
  // reads exactly like a frozen, unclosable page.
  useEffect(() => {
    return () => {
      if (!createdRef.current) return;
      control?.setVisible(id, false).catch(() => {
        // Already gone — nothing to hide.
      });
    };
  }, [control, id]);

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
