/**
 * Native-webview viewport (desktop only, `browser.nativeWebview`) — one browser tab.
 *
 * Renders **nothing but a placeholder**: the actual page lives in a real child
 * webview the Tauri shell composites over this element's rectangle. That's the whole
 * point — no iframe restrictions (`X-Frame-Options`/CSP can't refuse it), no frame
 * streaming, no decode cost, and native scrolling, input, clipboard and text
 * selection at native speed.
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
 *    claim while they're up. Hiding used to leave a black hole where the page was;
 *    with the CDP bridge the tab is photographed first and the photo stands in for
 *    it, so a menu opening over the browser looks like a menu opening over a page.
 * 3. **Invisible is not the same as unmounted.** A pane on a non-visible workspace
 *    still holds its native surface, which would otherwise float over whatever
 *    replaced it — so an `IntersectionObserver` and `visibilitychange` hide it too,
 *    and so does this component unmounting (hide, never close — see below). That
 *    last one covers the pane outliving the component: switching to reader mode or
 *    another engine leaves a live surface over its own replacement.
 * 4. **Another window on top is invisible to every event above.** On a floating
 *    desktop, raising or dragging a window over the browser changes nothing about
 *    this element, and the page kept painting over the window in front of it. So
 *    the region is hit-tested on a short poll: when something else is topmost
 *    inside it, the page's photo (plain HTML, so it obeys z-order) takes its place
 *    until the pane is uncovered.
 *
 * The surface's lifetime is the **tab's**, owned by `BrowserPanel` through its pane
 * session — not this component's. A workspace switch unmounts panes (see
 * layout/pane-lifetime and the `unmount-is-not-close` rule), and tearing the webview
 * down there would drop the user's page, scroll position and logged-in state. For
 * the same reason a remount re-attaches to the live surface instead of re-creating
 * it: `create` on an existing webview navigates it, which is a reload.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { windowControl, type WebviewBounds } from '../../../window';
import { captureFrame } from '../native-engine';
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

/**
 * The longest a menu waits for the stand-in photo before the surface is hidden
 * anyway. A screenshot is ~30-80ms; past this, the menu showing *behind* the page
 * would be worse than a moment of blank.
 */
const FREEZE_BUDGET_MS = 150;

/** How often to check whether another window has been raised over the pane. */
const OCCLUSION_POLL_MS = 250;

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
  webviewId,
  url,
  navSeq,
  active,
  created,
  onCreated,
  onError,
}: {
  /** The shell's id for this tab's webview (unique across panes and tabs). */
  webviewId: string;
  /** Where the tab should be — loaded on creation and on every `navSeq` bump. */
  url: string;
  /** Bumped by the parent to (re)issue navigation to `url` (also covers reload). */
  navSeq: number;
  /** Whether this is the pane's visible tab. Background tabs keep their page. */
  active: boolean;
  /** The webview already exists (a remount) — attach to it rather than create. */
  created: boolean;
  onCreated: (id: string) => void;
  onError: (message: string) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const id = webviewId;
  const control = windowControl()?.browserWebview ?? null;
  const lastBounds = useRef<WebviewBounds | null>(null);
  // Whether the shell has the surface. Bounds/navigate calls before that would
  // reject with "no native browser webview".
  const createdRef = useRef(created);
  // The navigation already honoured. A remount must not re-issue the last one —
  // the page has moved on since (links clicked inside it), and re-navigating would
  // throw the user back to where the tab started.
  const seqRef = useRef(created ? navSeq : -1);
  const [visible, setVisible] = useState(true);
  const [frozen, setFrozen] = useState<string | null>(null);

  const measure = useCallback((): WebviewBounds | null => {
    const el = hostRef.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    return { x: rect.left, y: rect.top, width: rect.width, height: rect.height };
  }, []);

  // --- create + navigate ---------------------------------------------------
  useEffect(() => {
    if (!control || !url || seqRef.current === navSeq) return;
    seqRef.current = navSeq;
    if (!createdRef.current) {
      const bounds = measure();
      if (!bounds) return;
      lastBounds.current = bounds;
      createdRef.current = true;
      control
        .create(id, url, bounds)
        .then(() => onCreated(id))
        .catch((e: Error) => {
          createdRef.current = false;
          seqRef.current = -1;
          onError(e.message || 'could not create the native browser view');
        });
      return;
    }
    control.navigate(id, url).catch((e: Error) => onError(e.message));
  }, [control, url, navSeq, id, measure, onCreated, onError]);

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
  // Five independent reasons to yield the region, combined into one flag.
  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    let onScreen = true;
    let suppressed = nativeOverlaysSuppressed();
    let occluded = false;
    let freezing = false;
    let alive = true;
    const wanted = () => active && onScreen && !suppressed && !occluded && !document.hidden;
    const apply = () => setVisible(wanted());

    /**
     * Yield the region behind a photo of the page (note 2): photograph, *then*
     * commit the reason and hide, so the photo is in place the moment the surface
     * goes. `commit` re-reads its reason at that point — it may have cleared while
     * the shot was being taken. Without the bridge there is no photo; just hide.
     */
    const hideBehindPhoto = (commit: () => void) => {
      const cdp = windowControl()?.browserWebview?.cdp;
      if (!wanted() || !createdRef.current || !cdp) {
        commit();
        apply();
        return;
      }
      if (freezing) return; // the shot in flight will commit whatever is current
      freezing = true;
      const budget = new Promise<null>((r) => setTimeout(() => r(null), FREEZE_BUDGET_MS));
      void Promise.race([captureFrame(id, 60).catch(() => null), budget]).then((shot) => {
        freezing = false;
        if (!alive) return;
        commit();
        if (!wanted()) setFrozen(shot);
        apply();
      });
    };

    /**
     * Note 4 — another HTML window on top of this pane. The native surface paints
     * above *everything*, so a floating window dragged over the browser, or brought
     * in front of it, would otherwise sit underneath the page. Sample a grid of
     * points inside the rect: if the topmost element at any of them is not ours,
     * something is covering us, and the photo (which *is* HTML, so it obeys
     * z-order) stands in until it moves away. Inset from the edges so a sibling's
     * resize grip or a 1px border never reads as cover.
     */
    const coveredNow = (): boolean => {
      const r = el.getBoundingClientRect();
      if (r.width < 40 || r.height < 40) return false;
      const inset = 12;
      const xs = [r.left + inset, r.left + r.width / 2, r.right - inset];
      const ys = [r.top + inset, r.top + r.height / 2, r.bottom - inset];
      for (const x of xs) {
        for (const y of ys) {
          const hit = document.elementFromPoint(x, y);
          if (hit && !el.contains(hit)) return true;
        }
      }
      return false;
    };
    const checkCover = () => {
      if (!active || !onScreen || document.hidden) return;
      const now = coveredNow();
      if (now === occluded) return;
      if (!now) {
        occluded = false;
        apply();
        return;
      }
      hideBehindPhoto(() => {
        occluded = coveredNow();
      });
    };
    // A window raised or dragged over the pane fires nothing on *this* element, so
    // poll — cheaply, nine hit tests — and also check on every pointer release,
    // which is when a click raises a window or a drag drops one.
    const coverTimer = window.setInterval(checkCover, OCCLUSION_POLL_MS);
    const onPointerUp = () => requestAnimationFrame(checkCover);
    window.addEventListener('pointerup', onPointerUp, true);

    const io = new IntersectionObserver((entries) => {
      onScreen = entries.some((e) => e.isIntersecting);
      apply();
    });
    io.observe(el);
    const unsub = subscribeNativeOverlaySuppression((s) => {
      if (!s) {
        suppressed = false;
        apply();
        return;
      }
      hideBehindPhoto(() => {
        suppressed = nativeOverlaysSuppressed();
      });
    });
    document.addEventListener('visibilitychange', apply);
    apply();
    return () => {
      alive = false;
      clearInterval(coverTimer);
      window.removeEventListener('pointerup', onPointerUp, true);
      io.disconnect();
      unsub();
      document.removeEventListener('visibilitychange', apply);
    };
  }, [active, id]);

  useEffect(() => {
    if (!control || !createdRef.current) return;
    control
      .setVisible(id, visible)
      .then(() => {
        // Drop the stand-in only once the real surface is back over it.
        if (visible) setFrozen(null);
      })
      .catch(() => {
        // Racing a close; the next create re-establishes the correct state.
      });
  }, [control, id, visible]);

  // Unmount hides — it must not close (the page, its scroll and its login survive a
  // workspace switch). But it must not leave the surface up either: this component
  // unmounts while its *tab* lives on, when the pane switches to reader mode or
  // another engine, or when the whole workspace is replaced. A visible overlay then
  // floats over whatever took its place, which reads exactly like a frozen,
  // unclosable page.
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
      // moment before it is created, or while it is hidden for an overlay — and
      // then it shows the page's last photo, if there is one.
      style={{ width: '100%', height: '100%', background: '#111', position: 'relative' }}
    >
      {frozen && (
        <img
          src={frozen}
          alt=""
          draggable={false}
          style={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            objectFit: 'fill',
            pointerEvents: 'none',
          }}
        />
      )}
    </div>
  );
}
