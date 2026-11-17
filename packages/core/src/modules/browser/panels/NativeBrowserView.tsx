/**
 * Native-webview viewport (desktop only, `browser.nativeWebview`) — one browser tab.
 *
 * Renders **nothing but a placeholder**: the actual page lives in a real child
 * view the desktop shell composites over this element's rectangle. That's the whole
 * point — no iframe restrictions (`X-Frame-Options`/CSP can't refuse it), no frame
 * streaming, no decode cost, and native scrolling, input, clipboard and text
 * selection at native speed.
 *
 * The price is that the overlay sits **above** the HTML layer and cannot be
 * z-indexed under anything. Four things follow, and all four are handled here:
 *
 * 1. **Geometry has to be pushed, not inherited.** The workbench moves panes in
 *    two ways: a size change (a divider drag, a dock resize, a window resize),
 *    which `ResizeObserver` reports, and a committed layout change (a tab moved to
 *    another group, a split, a maximize), which the layout store announces. The
 *    watcher below measures on both, on the next frame. The floating desktop also
 *    moved panes with nothing announcing it (a window drag), which needed a
 *    per-frame sampler; the workbench has no such gesture, so a slow backstop poll
 *    is all that is left of it.
 * 2. **Anything drawing over this region must hide it.** The overlay subscribes to
 *    `suppressNativeOverlays()` (see ../overlay.ts), which the palette, modals and
 *    a workbench tab drag claim while they're up. Hiding used to leave a black
 *    hole where the page was; with the CDP bridge the tab is photographed first
 *    and the photo stands in for it, so a menu opening over the browser looks like
 *    a menu opening over a page.
 * 3. **Invisible is not the same as unmounted.** A pane on a non-visible workspace
 *    still holds its native surface, which would otherwise float over whatever
 *    replaced it — so an `IntersectionObserver` and `visibilitychange` hide it too,
 *    and so does this component unmounting (hide, never close — see below). That
 *    last one covers the pane outliving the component: switching to reader mode or
 *    another engine leaves a live surface over its own replacement.
 * 4. **A covered pane is still laid out.** The workbench hides a tab behind its
 *    group's showing tab, a group behind a maximized one, and a hidden dock with
 *    `visibility`, never by unmounting — and no DOM observer reports visibility.
 *    So the layout is asked directly (`isPaneShowing`). This replaced hit-testing
 *    the region on a poll, which the floating desktop needed because any window
 *    could be raised over any other; in the workbench nothing overlaps a pane
 *    except the overlays note 2 covers.
 * 5. **The surface lives in one OS window at a time.** A popped-out pane is
 *    drawn in its pop-out's document, so the surface must be parented to that
 *    window's content and measured against that window — `attach` moves it, and
 *    the listeners here are on the pane's window (`usePaneWindow`), not `window`.
 *
 * The surface's lifetime is the **tab's**, owned by `BrowserPanel` through its pane
 * session — not this component's. A workspace switch unmounts panes (see
 * layout/pane-lifetime and the `unmount-is-not-close` rule), and tearing the webview
 * down there would drop the user's page, scroll position and logged-in state. For
 * the same reason a remount re-attaches to the live surface instead of re-creating
 * it: `create` on an existing webview navigates it, which is a reload.
 */
import { useCallback, useContext, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import { PaneInstanceContext } from '../../../agent-context';
import { usePanePopoutId, usePaneWindow } from '../../../layout/pane-window';
import { isPaneShowing } from '../../../layout/dock/frame';
import { layoutStore } from '../../../layout/store';

import { windowControl, type WebviewBounds } from '../../../window';
import { captureFrame } from '../native-engine';
import { subscribeNativeOverlaySuppression, nativeOverlaysSuppressed } from '../overlay';

/**
 * How often the geometry is re-measured with no event to prompt it. The
 * backstop for a move nothing announces; one `getBoundingClientRect` per tick.
 */
const BACKSTOP_MS = 1000;

/**
 * The longest a menu waits for the stand-in photo before the surface is hidden
 * anyway. A screenshot is ~30-80ms; past this, the menu showing *behind* the page
 * would be worse than a moment of blank.
 */
const FREEZE_BUDGET_MS = 150;

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
  // Note 4. Outside a workbench pane (no instance) there is no layout to ask.
  const paneInstanceId = useContext(PaneInstanceContext);
  // Note 5: the OS window this pane is in, and the shell's name for it.
  const win = usePaneWindow();
  const popoutId = usePanePopoutId();
  const popoutRef = useRef(popoutId);
  popoutRef.current = popoutId;
  const showing = useSyncExternalStore(layoutStore.subscribe, () =>
    paneInstanceId ? isPaneShowing(layoutStore.getSnapshot().frame, paneInstanceId) : true,
  );

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
        .create(id, url, bounds, popoutRef.current)
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

  // --- window --------------------------------------------------------------
  // Popped out or docked back: the surface follows into that window's content
  // (note 5). Declared before the geometry effect, so the move reaches the shell
  // before the first bounds measured in the new window.
  const attachedTo = useRef(popoutId);
  useEffect(() => {
    if (!control?.attach || !createdRef.current || attachedTo.current === popoutId) return;
    attachedTo.current = popoutId;
    lastBounds.current = null;
    control.attach(id, popoutId).catch((e: Error) => onError(e.message));
  }, [control, id, popoutId, onError]);

  // --- geometry ------------------------------------------------------------
  // Event-driven (note 1). `push` is cheap (one `getBoundingClientRect` plus a
  // compare) and only crosses to the shell when the rect actually moved.
  useEffect(() => {
    const el = hostRef.current;
    if (!control || !el) return;
    let raf = 0;

    const push = () => {
      raf = 0;
      if (!createdRef.current) return;
      const bounds = measure();
      if (!bounds || sameBounds(lastBounds.current, bounds)) return;
      lastBounds.current = bounds;
      control.updateBounds(id, bounds).catch(() => {
        // A bounds push racing a close is expected; the next one re-syncs.
      });
    };
    // Coalesce: a store change and a resize in the same frame push once, after
    // React has committed the new layout.
    const schedule = () => {
      if (!raf) raf = requestAnimationFrame(push);
    };

    const observer = new ResizeObserver(schedule);
    observer.observe(el);
    const unsubscribe = layoutStore.subscribe(schedule);
    win.addEventListener('resize', schedule);
    // The pane entrance animation moves the box without resizing it.
    win.addEventListener('animationend', schedule, true);
    win.addEventListener('transitionend', schedule, true);
    // Backstop for movement nothing announces (the status bar moving to the top).
    const backstop = window.setInterval(push, BACKSTOP_MS);
    schedule();

    return () => {
      if (raf) cancelAnimationFrame(raf);
      clearInterval(backstop);
      observer.disconnect();
      unsubscribe();
      win.removeEventListener('resize', schedule);
      win.removeEventListener('animationend', schedule, true);
      win.removeEventListener('transitionend', schedule, true);
    };
  }, [control, id, measure, win]);

  // --- visibility ----------------------------------------------------------
  // Five independent reasons to yield the region, combined into one flag.
  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    let onScreen = true;
    let suppressed = nativeOverlaysSuppressed();
    let freezing = false;
    let alive = true;
    const wanted = () => active && showing && onScreen && !suppressed && !win.document.hidden;
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
    win.document.addEventListener('visibilitychange', apply);
    apply();
    return () => {
      alive = false;
      io.disconnect();
      unsub();
      win.document.removeEventListener('visibilitychange', apply);
    };
  }, [active, showing, id, win]);

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
