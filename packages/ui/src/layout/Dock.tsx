/**
 * One fixed tool dock (left/right/bottom). A dock shows exactly **one** tool at
 * a time — its title in the header, its body below with region strips. There is
 * no in-dock tab strip: the activity rail is the single tool switcher (clicking
 * a rail glyph makes that tool the dock's active one via `SET_ACTIVE_TOOL`,
 * which also reveals the dock). A dock can still hold several tools in its
 * `tools` list; only the active one is visible, and the rail cycles between
 * them. Hidden entirely when not visible — the rail and the `dock.toggle:*`
 * commands re-open it.
 */
import {
  closePaneGuarded,
  layoutStore,
  paneDrag,
  resolveView,
  toggleDock,
  type DockSide,
  type DockState,
} from '@horrible/core';

import { PaneWithRegions } from './Region';

const MIN_SIZE = 140;
const MAX_SIZE: Record<DockSide, number> = { left: 800, right: 800, bottom: 600 };

export function Dock({ side, dock }: { side: DockSide; dock: DockState }) {
  if (!dock.visible) return null;
  const active = dock.tools.find((t) => t.instanceId === dock.activeTool) ?? dock.tools[0];
  if (!active) return null;
  const vertical = side !== 'bottom';
  // Per-tool remembered extent, falling back to the dock's last-used one. This is
  // why switching the active tool can change the dock's width.
  const size = active.dockSize ?? dock.size;

  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;
    const startSize = size;
    const onMove = (me: PointerEvent) => {
      let next: number;
      if (side === 'left') next = startSize + (me.clientX - startX);
      else if (side === 'right') next = startSize - (me.clientX - startX);
      else next = startSize - (me.clientY - startY);
      next = Math.max(MIN_SIZE, Math.min(MAX_SIZE[side], next));
      layoutStore.dispatch({
        type: 'SET_TOOL_SIZE',
        side,
        instanceId: active.instanceId,
        size: next,
      });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const handle = (
    <div
      className={`frame-dock-handle frame-dock-handle--${vertical ? 'v' : 'h'}`}
      onPointerDown={startResize}
    />
  );

  return (
    <aside
      className={`frame-dock frame-dock--${side}`}
      style={vertical ? { width: size } : { height: size }}
    >
      {(side === 'right' || side === 'bottom') && handle}
      <div className="frame-dock-content">
        <div
          className="frame-dock-header"
          draggable
          title="Drag into the center to undock"
          onDragStart={(e) => {
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData(
              'text/plain',
              resolveView(active.viewId)?.title ?? active.viewId,
            );
            paneDrag.begin({
              kind: 'pane',
              instanceId: active.instanceId,
              viewId: active.viewId,
              title: resolveView(active.viewId)?.title ?? active.viewId,
            });
          }}
          onDragEnd={() => paneDrag.end()}
        >
          <div className="frame-dock-title-container">
            {resolveView(active.viewId)?.icon ? (
              <span className="frame-dock-title-icon">{resolveView(active.viewId)!.icon}</span>
            ) : null}
            <span className="frame-dock-title">
              {resolveView(active.viewId)?.title ?? active.viewId}
            </span>
            <button
              className="frame-dock-tab-close"
              title={`Close ${resolveView(active.viewId)?.title ?? active.viewId}`}
              onClick={() => void closePaneGuarded(active.instanceId)}
            >
              ✕
            </button>
          </div>
          <button
            className="frame-dock-btn"
            title="Hide dock"
            onClick={() => toggleDock(side, false)}
          >
            ▁
          </button>
        </div>
        <div className="frame-dock-body">
          <PaneWithRegions pane={active} />
        </div>
      </div>
      {side === 'left' && handle}
    </aside>
  );
}
