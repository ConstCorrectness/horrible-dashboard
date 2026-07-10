/**
 * The Blender-style header strip of a center area: a type switcher (icon
 * dropdown of views legal for the area's role), the tab row for document areas,
 * region toggles for the active pane, and the area menu (split/join/fullscreen/
 * collapse/float/close). Collapsible per-area for chrome-less dashboards.
 */
import { useEffect, useRef, useState } from 'react';
import {
  fullscreenArea,
  joinAreaDirection,
  layoutStore,
  openFramePane,
  registry,
  resolveView,
  roleOf,
  splitAreaBy,
  toggleRegion,
  type AreaNode,
  type PaneState,
  type RegionPosition,
} from '@horrible/core';

const REGION_GLYPH: Record<RegionPosition, string> = { left: '◧', right: '◨', bottom: '⬓' };
const REGION_KEY: Record<RegionPosition, string> = { left: 't', right: 'n', bottom: 'b' };

/** Views that can replace this area's content (documents+widgets; never tools). */
function switchableViews(area: AreaNode) {
  const role = area.tabs.length ? roleOf(area.tabs[0].viewId) : null;
  return [...registry.panels, ...registry.widgets].filter((v) => {
    const r = roleOf(v.id);
    if (r === 'tool') return false;
    // A tabbed document area only switches among documents; a widget (or empty)
    // area takes either kind — switching just replaces its single pane.
    return role === 'document' && area.tabs.length > 1 ? r === 'document' : true;
  });
}

function useCloseOnOutside(open: boolean, close: () => void) {
  useEffect(() => {
    if (!open) return;
    const onDown = () => close();
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onDown);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onDown);
    };
  }, [open, close]);
}

export function AreaHeader({ area }: { area: AreaNode }) {
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  useCloseOnOutside(switcherOpen, () => setSwitcherOpen(false));
  useCloseOnOutside(menuOpen, () => setMenuOpen(false));
  const headerRef = useRef<HTMLDivElement>(null);

  const active: PaneState | undefined = area.tabs[area.activeTab];
  const activeDecl = active ? resolveView(active.viewId) : undefined;
  const isDocumentArea = active ? roleOf(active.viewId) === 'document' : false;
  const regionPositions = (resolveView(active?.viewId ?? '')?.regions ?? []).reduce(
    (set, r) => set.add(r.position ?? 'right'),
    new Set<RegionPosition>(),
  );

  const changeType = (viewId: string) => {
    setSwitcherOpen(false);
    if (!active) {
      layoutStore.dispatch({ type: 'FOCUS_AREA', areaId: area.id });
      openFramePane(viewId);
      return;
    }
    registry.layoutController?.changePaneType(active.instanceId, viewId);
  };

  const closeTab = (pane: PaneState) =>
    layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId: pane.instanceId });

  return (
    <div ref={headerRef} className="frame-area-header">
      <div className="frame-area-switcher">
        <button
          className="frame-area-switcher-btn"
          title={activeDecl ? `${activeDecl.title} — switch view` : 'Pick a view'}
          onClick={() => setSwitcherOpen((v) => !v)}
        >
          {activeDecl?.icon ?? '▢'}
          <span className="frame-area-switcher-caret">▾</span>
        </button>
        {switcherOpen && (
          <div className="frame-menu" onMouseDown={(e) => e.stopPropagation()}>
            {switchableViews(area).map((v) => (
              <button
                key={v.id}
                className={`frame-menu-item${v.id === active?.viewId ? ' active' : ''}`}
                onClick={() => changeType(v.id)}
              >
                <span className="frame-menu-icon">{v.icon ?? v.title[0]}</span>
                {v.title}
              </button>
            ))}
          </div>
        )}
      </div>

      {isDocumentArea ? (
        <div className="frame-area-tabs">
          {area.tabs.map((tab, i) => {
            const decl = resolveView(tab.viewId);
            const title = (tab.params?.title as string | undefined) ?? decl?.title ?? tab.viewId;
            return (
              <span
                key={tab.instanceId}
                className={`frame-area-tab${i === area.activeTab ? ' active' : ''}`}
                onAuxClick={(e) => {
                  if (e.button === 1) closeTab(tab);
                }}
              >
                <button
                  className="frame-area-tab-label"
                  onClick={() =>
                    layoutStore.dispatch({ type: 'SET_ACTIVE_TAB', areaId: area.id, index: i })
                  }
                >
                  {title}
                </button>
                <button
                  className="frame-area-tab-close"
                  title="Close"
                  onClick={() => closeTab(tab)}
                >
                  ✕
                </button>
              </span>
            );
          })}
        </div>
      ) : (
        <span className="frame-area-title">{activeDecl?.title ?? 'Empty area'}</span>
      )}

      <div className="frame-area-header-actions">
        {active &&
          [...regionPositions].map((position) => {
            const open = active.regions?.[position]?.open ?? false;
            return (
              <button
                key={position}
                className={`frame-area-btn${open ? ' active' : ''}`}
                title={`Toggle ${position} region (${REGION_KEY[position]})`}
                aria-pressed={open}
                onClick={() => toggleRegion(active.instanceId, position)}
              >
                {REGION_GLYPH[position]}
              </button>
            );
          })}
        <div className="frame-area-switcher">
          <button
            className="frame-area-btn"
            title="Area menu"
            onClick={() => setMenuOpen((v) => !v)}
          >
            ☰
          </button>
          {menuOpen && (
            <div className="frame-menu frame-menu--right" onMouseDown={(e) => e.stopPropagation()}>
              <button
                className="frame-menu-item"
                onClick={() => {
                  setMenuOpen(false);
                  splitAreaBy(area.id, 'right');
                }}
              >
                Split right
              </button>
              <button
                className="frame-menu-item"
                onClick={() => {
                  setMenuOpen(false);
                  splitAreaBy(area.id, 'below');
                }}
              >
                Split down
              </button>
              <button
                className="frame-menu-item"
                onClick={() => {
                  setMenuOpen(false);
                  // Absorb the first joinable neighbor, trying right/down/left/up.
                  const directions = ['right', 'down', 'left', 'up'] as const;
                  for (const d of directions) {
                    if (joinAreaDirection(area.id, d)) break;
                  }
                }}
              >
                Join neighbor
              </button>
              <button
                className="frame-menu-item"
                onClick={() => {
                  setMenuOpen(false);
                  fullscreenArea(area.id);
                }}
              >
                Fullscreen area
              </button>
              {active && (
                <button
                  className="frame-menu-item"
                  onClick={() => {
                    setMenuOpen(false);
                    registry.layoutController?.setPaneFloating(active.instanceId, true);
                  }}
                >
                  Float pane
                </button>
              )}
              <button
                className="frame-menu-item"
                onClick={() => {
                  setMenuOpen(false);
                  layoutStore.dispatch({
                    type: 'SET_HEADER_COLLAPSED',
                    areaId: area.id,
                    collapsed: true,
                  });
                }}
              >
                Hide header
              </button>
              {active && (
                <button
                  className="frame-menu-item frame-menu-item--danger"
                  onClick={() => {
                    setMenuOpen(false);
                    closeTab(active);
                  }}
                >
                  Close pane
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
