/**
 * Lightweight in-window floating panes over the center grid. Rects are stored
 * as fractions of the center's bounds, so they survive window resizes; drag the
 * header to move, the corner to resize, the header buttons to dock back or
 * close. Not OS windows — the phase-2 shell may promote them.
 */
import type { RefObject } from 'react';
import { closePaneGuarded, layoutStore, resolveView, type FloatingPane } from '@horrible/core';

import { PaneHost } from './PaneHost';

const MIN_FRAC = 0.12;

export function FloatingLayer({
  floating,
  containerRef,
}: {
  floating: FloatingPane[];
  containerRef: RefObject<HTMLDivElement | null>;
}) {
  if (floating.length === 0) return null;

  const startDrag = (e: React.PointerEvent, float: FloatingPane, mode: 'move' | 'resize') => {
    e.preventDefault();
    const container = containerRef.current;
    if (!container) return;
    const bounds = container.getBoundingClientRect();
    if (bounds.width <= 0 || bounds.height <= 0) return;
    const start = { x: e.clientX, y: e.clientY };
    const startRect = { ...float.rect };
    layoutStore.dispatch({
      type: 'BRING_FLOATING_FRONT',
      instanceId: float.pane.instanceId,
    });
    const onMove = (me: PointerEvent) => {
      const dx = (me.clientX - start.x) / bounds.width;
      const dy = (me.clientY - start.y) / bounds.height;
      const rect =
        mode === 'move'
          ? {
              ...startRect,
              x: Math.min(Math.max(startRect.x + dx, 0), 1 - startRect.w * 0.25),
              y: Math.min(Math.max(startRect.y + dy, 0), 0.95),
            }
          : {
              ...startRect,
              w: Math.min(Math.max(startRect.w + dx, MIN_FRAC), 1),
              h: Math.min(Math.max(startRect.h + dy, MIN_FRAC), 1),
            };
      layoutStore.dispatch({
        type: 'SET_FLOATING_RECT',
        instanceId: float.pane.instanceId,
        rect,
      });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  return (
    <div className="frame-floating-layer">
      {[...floating]
        .sort((a, b) => a.z - b.z)
        .map((float) => {
          const decl = resolveView(float.pane.viewId);
          const { rect } = float;
          return (
            <div
              key={float.pane.instanceId}
              className="frame-floating"
              style={{
                left: `${rect.x * 100}%`,
                top: `${rect.y * 100}%`,
                width: `${rect.w * 100}%`,
                height: `${rect.h * 100}%`,
                zIndex: 10 + float.z,
              }}
              onPointerDownCapture={() =>
                layoutStore.dispatch({
                  type: 'BRING_FLOATING_FRONT',
                  instanceId: float.pane.instanceId,
                })
              }
            >
              <div
                className="frame-floating-header"
                onPointerDown={(e) => startDrag(e, float, 'move')}
              >
                <span className="frame-floating-title">
                  {decl?.icon ? `${decl.icon} ` : ''}
                  {decl?.title ?? float.pane.viewId}
                </span>
                <button
                  className="frame-floating-btn"
                  title="Dock back"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() =>
                    layoutStore.dispatch({
                      type: 'DOCK_FLOATING',
                      instanceId: float.pane.instanceId,
                    })
                  }
                >
                  ⤵
                </button>
                <button
                  className="frame-floating-btn"
                  title="Close"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() => void closePaneGuarded(float.pane.instanceId)}
                >
                  ✕
                </button>
              </div>
              <div className="frame-floating-body">
                <PaneHost pane={float.pane} />
              </div>
              <div
                className="frame-floating-resize"
                onPointerDown={(e) => startDrag(e, float, 'resize')}
              />
            </div>
          );
        })}
    </div>
  );
}
