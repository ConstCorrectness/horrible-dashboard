/**
 * The desktop's window layer: free OS-style windows over the whole shell.
 *
 * This replaces the old in-frame floating layer, and the difference that matters is
 * where it is mounted. The floating layer lived *inside* `.frame-center` and stored
 * rects as fractions of it, so a "floating" pane was clipped to the middle of the
 * tiling frame. This mounts as a sibling of the shell's views and works in viewport
 * pixels, so a window genuinely floats over the desktop — including over a tiling
 * frame, which is what makes a window the escape hatch on a tiling desktop.
 *
 * The layer measures itself and owns the single `SET_WINDOW_VIEWPORT` dispatch: every
 * rect in the store is relative to the size reported here, so exactly one element may
 * be the authority on what that size is.
 *
 * See docs/architecture/desktop-shell.mdx.
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import {
  layoutStore,
  setDesktopMeasurer,
  snapZoneAt,
  type SnapZone,
  type WindowState,
} from '@horrible/core';

import { DesktopWindow } from './Window';
import { SnapOverlay } from './SnapOverlay';

export interface DragState {
  windowId: string;
  /** The snap zone the pointer is currently over, previewed but not yet applied. */
  zone: SnapZone | null;
  /** A window whose titlebar the pointer is over — dropping there merges tabs. */
  mergeTargetId: string | null;
}

export function WindowLayer() {
  const { frame, workspaceId, hydrated } = useSyncExternalStore(
    layoutStore.subscribe,
    layoutStore.getSnapshot,
  );
  const ref = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<DragState | null>(null);

  // Measure, and keep measuring. The store rescales every rect through the size
  // reported here, so this is the one place allowed to decide what it is.
  const report = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    layoutStore.dispatch({
      type: 'SET_WINDOW_VIEWPORT',
      viewport: { w: Math.round(rect.width), h: Math.round(rect.height) },
    });
  }, []);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    report();
    const observer = new ResizeObserver(report);
    observer.observe(el);
    return () => observer.disconnect();
  }, [report]);

  // Re-report after every workspace load.
  //
  // Not redundant with the observer: `LOAD_WORKSPACE` replaces the whole frame with
  // the deserialized one, whose `windowViewport` is whatever was stored — null for a
  // fresh or migrated blob. The element has not resized, so the observer stays quiet,
  // and the layer would sit on a null viewport until the user happened to resize the
  // app. Every rect would then be computed against the nominal fallback instead of
  // the real surface, and a migrated v1 window (saved on a 1×1 basis) would never be
  // scaled up into pixels at all — it would render a few pixels wide.
  useLayoutEffect(() => {
    if (frame.windowViewport === null) report();
  }, [workspaceId, hydrated, frame.windowViewport, report]);

  // Let the pure verbs (keybindings, agent tools) resolve pixel geometry without
  // reaching into the DOM themselves.
  useEffect(() => {
    setDesktopMeasurer(() => {
      const rect = ref.current?.getBoundingClientRect();
      return rect && rect.width > 0
        ? { w: Math.round(rect.width), h: Math.round(rect.height) }
        : null;
    });
    return () => setDesktopMeasurer(null);
  }, []);

  const bounds = useCallback(() => ref.current?.getBoundingClientRect() ?? null, []);

  /**
   * Live drag feedback: which zone would apply, and which titlebar is under us.
   *
   * **Returns** the resolved state as well as storing it, and the caller keeps the
   * returned value rather than reading it back through React. The drop decision has
   * to be available synchronously at pointerup: React batches state updates, so a
   * user who drags quickly and releases within the same frame reaches pointerup with
   * `drag` still holding the previous frame's value — or null — and the window lands
   * free instead of snapping. Intermittent, and only when moving fast.
   */
  const onDragMove = useCallback(
    (windowId: string, client: { x: number; y: number }): DragState | null => {
      const b = bounds();
      if (!b) return null;
      const local = { x: client.x - b.left, y: client.y - b.top };
      const el = document.elementFromPoint(client.x, client.y);
      const titlebar = el?.closest<HTMLElement>('[data-window-titlebar]');
      const over = titlebar?.dataset.windowId ?? null;
      const mergeTargetId = over === windowId ? null : over;
      const next: DragState = {
        windowId,
        // A merge target wins: the pointer is over another window's tab strip, so
        // the user is aiming at that strip, not at the edge behind it.
        zone: mergeTargetId ? null : snapZoneAt(local, { w: b.width, h: b.height }),
        mergeTargetId,
      };
      setDrag(next);
      return next;
    },
    [bounds],
  );

  const onDragEnd = useCallback(() => setDrag(null), []);

  if (frame.windows.length === 0) return <div className="os-window-layer" ref={ref} aria-hidden />;

  const ordered = [...frame.windows].sort((a, b) => a.z - b.z);
  // The presented pane's window, if any. Resolved to a *window* id here so
  // `DesktopWindow` takes a boolean and does not have to search the frame.
  const presentedWindowId = frame.presentedInstanceId
    ? (ordered.find((w) => w.area.tabs.some((t) => t.instanceId === frame.presentedInstanceId))
        ?.id ?? null)
    : null;

  return (
    <div
      className={`os-window-layer${presentedWindowId ? ' is-presenting' : ''}`}
      ref={ref}
      // A presented window escapes this layer's bounds, so the layer must stop
      // clipping it — `overflow: hidden` is what keeps a dragged window from
      // scrolling the shell the rest of the time.
    >
      {drag?.zone && <SnapOverlay zone={drag.zone} />}
      {ordered.map((win: WindowState) => (
        <DesktopWindow
          key={win.id}
          win={win}
          // Presented, not re-rendered elsewhere: promoting the window in place
          // keeps the pane at the same position in the React tree. Rendering it
          // into a separate full-screen layer would reconcile as a different
          // element and remount the pane — killing a live PTY, socket or engine,
          // which is exactly what `pane-lifetime` exists to prevent.
          presented={presentedWindowId === win.id}
          focused={frame.focusedWindowId === win.id}
          mergeTarget={drag?.mergeTargetId === win.id}
          bounds={bounds}
          onDragMove={onDragMove}
          onDragEnd={onDragEnd}
        />
      ))}
    </div>
  );
}
