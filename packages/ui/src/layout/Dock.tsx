/**
 * One fixed tool dock (left/right/bottom). Tools stack as tabs in the dock
 * header, one visible at a time; the visible tool renders with its region
 * strips. Hidden entirely when not visible — the activity rail and the
 * `dock.toggle:*` commands re-open it.
 */
import {
  layoutStore,
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

  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;
    const startSize = dock.size;
    const onMove = (me: PointerEvent) => {
      let next: number;
      if (side === 'left') next = startSize + (me.clientX - startX);
      else if (side === 'right') next = startSize - (me.clientX - startX);
      else next = startSize - (me.clientY - startY);
      next = Math.max(MIN_SIZE, Math.min(MAX_SIZE[side], next));
      layoutStore.dispatch({ type: 'SET_DOCK', side, patch: { size: next } });
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
      style={vertical ? { width: dock.size } : { height: dock.size }}
    >
      {(side === 'right' || side === 'bottom') && handle}
      <div className="frame-dock-content">
        <div className="frame-dock-header">
          <div className="frame-dock-tabs">
            {dock.tools.map((tool) => {
              const decl = resolveView(tool.viewId);
              return (
                <span
                  key={tool.instanceId}
                  className={`frame-dock-tab${tool.instanceId === active.instanceId ? ' active' : ''}`}
                >
                  <button
                    className="frame-dock-tab-label"
                    title={decl?.title ?? tool.viewId}
                    onClick={() =>
                      layoutStore.dispatch({
                        type: 'SET_ACTIVE_TOOL',
                        side,
                        instanceId: tool.instanceId,
                      })
                    }
                  >
                    {decl?.icon ? <span className="frame-dock-tab-icon">{decl.icon}</span> : null}
                    <span>{decl?.title ?? tool.viewId}</span>
                  </button>
                  <button
                    className="frame-dock-tab-close"
                    title={`Close ${decl?.title ?? tool.viewId}`}
                    onClick={() =>
                      layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId: tool.instanceId })
                    }
                  >
                    ✕
                  </button>
                </span>
              );
            })}
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
