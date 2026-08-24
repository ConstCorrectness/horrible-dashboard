/**
 * One OS-style window: titlebar (with tabs when it holds more than one pane),
 * body, and eight resize edges.
 *
 * Three things here are load-bearing and easy to lose in a refactor:
 *
 * 1. The titlebar must NOT carry `data-tauri-drag-region`. That attribute lives on
 *    the app's own top strip; on a window it would drag the whole OS window instead
 *    of the window under the pointer, and the user would have no way to move it.
 * 2. Dragging **releases keyboard/pointer capture** first. A captured pane (a game,
 *    a terminal, the server-rendered browser) otherwise keeps the pointer and the
 *    drag never reaches this handler.
 * 3. Geometry is committed to the store on pointer-**up**, not on every move. The
 *    in-flight rect is local state; dispatching per frame would bump `revision` a
 *    hundred times per drag and drive the autosave debounce into a write loop.
 */
import { useCallback, useRef, useState, useSyncExternalStore } from 'react';
import {
  clampRect,
  closePaneGuarded,
  focusWindow,
  layoutStore,
  MIN_WINDOW_SIZE,
  presentPane,
  releaseCapture,
  resolveView,
  setPaneWindowed,
  toggleWindowMaximized,
  toggleWindowMinimized,
  type SnapZone,
  type WindowRect,
  type WindowState,
} from '@horrible/core';

import { useHorizontalWheel } from '../hooks/useHorizontalWheel';
import { PaneHost } from '../layout/PaneHost';
import type { DragState } from './WindowLayer';

/** The eight resize grips, as [class suffix, x-edge, y-edge]. */
const EDGES = [
  ['n', 0, -1],
  ['s', 0, 1],
  ['w', -1, 0],
  ['e', 1, 0],
  ['nw', -1, -1],
  ['ne', 1, -1],
  ['sw', -1, 1],
  ['se', 1, 1],
] as const;

export function DesktopWindow({
  win,
  focused,
  presented,
  mergeTarget,
  bounds,
  onDragMove,
  onDragEnd,
}: {
  win: WindowState;
  focused: boolean;
  /** This window holds the presented pane — it fills the screen, chrome and all. */
  presented: boolean;
  /** True while another window is being dragged over THIS one's tab strip. */
  mergeTarget: boolean;
  bounds: () => DOMRect | null;
  onDragMove: (windowId: string, client: { x: number; y: number }) => DragState | null;
  onDragEnd: () => void;
}) {
  // The rect being dragged right now. Null except mid-gesture — see note 3 above.
  // Mirrored into a ref because the pointerup handler is created once per gesture
  // and would otherwise close over the rect as it was when the drag started.
  const [live, setLive] = useState<WindowRect | null>(null);
  const liveRef = useRef<WindowRect | null>(null);
  // The drop decision, kept synchronously — see the note on `onDragMove`. Reading
  // `drag` (a React prop) at pointerup loses the last frame's zone whenever the
  // move and the release land in the same frame.
  const zoneRef = useRef<SnapZone | null>(null);
  const mergeRef = useRef<string | null>(null);
  // Merged windows can hold more tabs than the titlebar is wide.
  const tabWheelRef = useHorizontalWheel<HTMLDivElement>();

  const rect = live ?? win.rect;
  const active = win.area.tabs[win.area.activeTab];
  const decl = active ? resolveView(active.viewId) : null;
  // Windows exist on a tiling desktop too (a pane popped out of the frame), so
  // this is read per render rather than assumed from "we are rendering a window".
  const floatingDesktop =
    useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot).frame.mode === 'floating';

  const startGesture = useCallback(
    (e: React.PointerEvent, kind: 'move' | 'resize', edge?: readonly [string, number, number]) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      // See note 2: a captured pane would otherwise swallow the whole drag.
      releaseCapture();
      focusWindow(win.id);
      const b = bounds();
      if (!b) return;
      const start = { x: e.clientX, y: e.clientY };
      const from = { ...win.rect };
      const surface = { w: b.width, h: b.height };
      liveRef.current = null;
      zoneRef.current = null;
      mergeRef.current = null;

      const onMove = (me: PointerEvent) => {
        const dx = me.clientX - start.x;
        const dy = me.clientY - start.y;
        let next: WindowRect;
        if (kind === 'move') {
          next = { ...from, x: from.x + dx, y: from.y + dy };
          const feedback = onDragMove(win.id, { x: me.clientX, y: me.clientY });
          zoneRef.current = feedback?.zone ?? null;
          mergeRef.current = feedback?.mergeTargetId ?? null;
        } else {
          const [, ex, ey] = edge!;
          // Dragging a left/top edge moves the origin as well as the size, and the
          // minimum size has to stop the origin too — otherwise a window shrunk to
          // its minimum keeps sliding its left edge rightwards.
          const w = Math.max(MIN_WINDOW_SIZE.w, from.w + dx * ex);
          const h = Math.max(MIN_WINDOW_SIZE.h, from.h + dy * ey);
          next = {
            x: ex < 0 ? from.x + (from.w - w) : from.x,
            y: ey < 0 ? from.y + (from.h - h) : from.y,
            w,
            h,
          };
        }
        const clamped = clampRect(next, surface);
        liveRef.current = clamped;
        setLive(clamped);
      };

      const onUp = (ue: PointerEvent) => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        window.removeEventListener('pointercancel', onUp);
        const committed = liveRef.current;
        const zone = zoneRef.current;
        const mergeId = mergeRef.current;
        liveRef.current = null;
        zoneRef.current = null;
        mergeRef.current = null;
        setLive(null);
        onDragEnd();

        if (kind === 'move') {
          // Prefer what the last move resolved; fall back to hit-testing the
          // release point, which covers a click-drag so short it never moved.
          const el = document.elementFromPoint(ue.clientX, ue.clientY);
          const titlebar = el?.closest<HTMLElement>('[data-window-titlebar]');
          const targetId = mergeId ?? titlebar?.dataset.windowId;
          if (targetId && targetId !== win.id && active) {
            // Dropped on another window's tab strip: merge instead of moving.
            layoutStore.dispatch({
              type: 'MERGE_INTO_WINDOW',
              instanceId: active.instanceId,
              windowId: targetId,
            });
            return;
          }
          if (zone) {
            layoutStore.dispatch({
              type: 'SET_WINDOW_MODE',
              windowId: win.id,
              mode: zone === 'max' ? 'maximized' : 'normal',
              ...(zone === 'max' ? {} : { snap: zone }),
              viewport: surface,
            });
            return;
          }
        }
        if (committed) {
          layoutStore.dispatch({ type: 'SET_WINDOW_RECT', windowId: win.id, rect: committed });
        }
      };

      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      window.addEventListener('pointercancel', onUp);
    },
    [win.id, win.rect, active, bounds, onDragMove, onDragEnd],
  );

  const minimized = win.mode === 'minimized';
  const maximized = win.mode === 'maximized' || win.snap === 'max';

  // Minimizing hides this tree; it never renders a *different* one.
  //
  // An early `return` with its own JSX was the first attempt and it silently
  // remounted the pane: React reconciles by position, and a minimized tree of
  // `[body]` puts the body where the normal tree's `[titlebar, body, …grips]` has
  // its titlebar, so the whole pane subtree is torn down and rebuilt. That kills a
  // live PTY, a browser engine or a socket — the exact failure `pane-lifetime`
  // exists to prevent. One tree, one position, class-only hiding.
  return (
    <div
      className={[
        'os-window',
        focused ? 'os-window--focused' : '',
        minimized ? 'os-window--minimized' : '',
        maximized ? 'os-window--maximized' : '',
        presented ? 'os-window--presented' : '',
        mergeTarget ? 'os-window--merge-target' : '',
        live ? 'os-window--dragging' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      aria-hidden={minimized || undefined}
      data-window-id={win.id}
      // Presented geometry is `position: fixed` in the stylesheet, so the inline
      // rect has to be dropped entirely — leaving `left`/`top` behind would
      // offset a fixed element by the window's old desktop position.
      style={
        presented
          ? undefined
          : {
              left: rect.x,
              top: rect.y,
              width: rect.w,
              height: rect.h,
              zIndex: win.z,
            }
      }
      onPointerDownCapture={() => {
        if (!focused) focusWindow(win.id);
      }}
    >
      <div
        className="os-window-titlebar"
        // The hook the merge hit-test looks for. Deliberately NOT
        // `data-tauri-drag-region` — see note 1 at the top of this file.
        data-window-titlebar=""
        data-window-id={win.id}
        // Neither gesture while presented: the window is `position: fixed` and
        // has no rect of its own to move or restore to, so both would commit
        // geometry that nothing is rendering and the user would find the window
        // somewhere unexpected on leaving fullscreen.
        onPointerDown={(e) => {
          if (!presented) startGesture(e, 'move');
        }}
        onDoubleClick={() => {
          if (!presented) toggleWindowMaximized(win.id);
        }}
      >
        {win.area.tabs.length > 1 ? (
          <div className="os-window-tabs" ref={tabWheelRef} role="tablist">
            {win.area.tabs.map((tab, i) => (
              <button
                key={tab.instanceId}
                role="tab"
                aria-selected={i === win.area.activeTab}
                className={`os-window-tab${i === win.area.activeTab ? ' is-active' : ''}`}
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() =>
                  layoutStore.dispatch({
                    type: 'SET_ACTIVE_TAB',
                    areaId: win.area.id,
                    index: i,
                  })
                }
                onAuxClick={(e) => {
                  if (e.button === 1) void closePaneGuarded(tab.instanceId);
                }}
              >
                {resolveView(tab.viewId)?.title ?? tab.viewId}
              </button>
            ))}
          </div>
        ) : (
          <span className="os-window-title">
            {decl?.icon ? <span className="os-window-icon">{decl.icon}</span> : null}
            {decl?.title ?? active?.viewId ?? 'Window'}
          </span>
        )}
        <div className="os-window-controls">
          {/* Dock-back is a **tiling-desktop** verb, and only there.
           *
           * A floating desktop retains the centre tree but never draws it, so
           * putting a pane back in the frame there hides it and leaves it on the
           * taskbar as minimized — the same outcome, and the same recovery, as
           * the ─ button beside it. Two controls doing one thing under different
           * names is what made this corner confusing, so the one that describes a
           * destination the user cannot see is the one that goes. */}
          {!floatingDesktop && (
            <button
              className="os-window-btn"
              title="Dock back into the frame"
              aria-label="Dock back into the frame"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() => active && setPaneWindowed(active.instanceId, false)}
            >
              ⤵
            </button>
          )}
          <button
            className="os-window-btn"
            title="Minimize"
            aria-label="Minimize"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => toggleWindowMinimized(win.id)}
          >
            ─
          </button>
          <button
            className="os-window-btn"
            title={maximized ? 'Restore' : 'Maximize'}
            aria-label={maximized ? 'Restore' : 'Maximize'}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => toggleWindowMaximized(win.id)}
          >
            {maximized ? '❐' : '□'}
          </button>
          {/* Only for a view that declares `fullscreen`. Presenting is a real
              escalation — it hides the taskbar and, on desktop, takes the whole
              display — so it is offered where a pane has said it wants the
              screen, not on every window as a bigger maximize. */}
          {decl?.fullscreen && (
            <button
              className="os-window-btn"
              title={presented ? 'Leave fullscreen (Esc)' : 'Fullscreen'}
              aria-label={presented ? 'Leave fullscreen (Esc)' : 'Fullscreen'}
              aria-pressed={presented}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() => presentPane(presented ? null : (active?.instanceId ?? null))}
            >
              {presented ? '⤡' : '⛶'}
            </button>
          )}
          <button
            className="os-window-btn os-window-btn--close"
            title="Close"
            aria-label="Close"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => active && void closePaneGuarded(active.instanceId)}
          >
            ✕
          </button>
        </div>
      </div>

      <div className="os-window-body">{active && <PaneHost pane={active} />}</div>

      {/* Resize grips last so they sit above the body's own pointer handlers. */}
      {!maximized &&
        !minimized &&
        !presented &&
        EDGES.map((edge) => (
          <div
            key={edge[0]}
            className={`os-window-resize os-window-resize--${edge[0]}`}
            onPointerDown={(e) => startGesture(e, 'resize', edge)}
          />
        ))}
    </div>
  );
}
