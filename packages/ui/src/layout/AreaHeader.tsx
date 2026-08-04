/**
 * The Blender-style header strip of a center area: a type switcher (icon
 * dropdown of every view the area can host, grouped by role), the tab row for
 * document areas,
 * region toggles for the active pane, and the area menu (split/join/fullscreen/
 * collapse/float/close). Collapsible per-area for chrome-less dashboards.
 */
import { useEffect, useRef, useState } from 'react';
import {
  closePaneGuarded,
  fullscreenArea,
  joinAreaDirection,
  layoutStore,
  openFramePane,
  registerTransient,
  registry,
  resolveView,
  roleOf,
  splitAreaBy,
  toggleRegion,
  type AreaNode,
  type PaneRole,
  type PaneState,
  type RegionPosition,
} from '@horrible/core';

const REGION_GLYPH: Record<RegionPosition, string> = { left: '◧', right: '◨', bottom: '⬓' };
const REGION_KEY: Record<RegionPosition, string> = { left: 't', right: 'n', bottom: 'b' };

const GROUP_LABEL: Record<PaneRole, string> = {
  document: 'Documents',
  widget: 'Widgets',
  tool: 'Tools',
};
const GROUP_ORDER: PaneRole[] = ['document', 'widget', 'tool'];

/**
 * Views that can replace this area's content, grouped by role. A center area now
 * accepts tools too (they are only *defaulted* to a dock), so the list is long
 * enough that it is sectioned rather than filtered — documents first, tools last.
 */
function switchableViews(area: AreaNode) {
  const role = area.tabs.length ? roleOf(area.tabs[0].viewId) : null;
  const views = [...registry.panels, ...registry.widgets].filter((v) => {
    // Embedded views are not destinations — they belong to a host pane, and
    // offering one here would put the same content in two competing places.
    if (v.embedded) return false;
    // A tabbed document area only switches among documents; a widget (or empty)
    // area takes any kind — switching just replaces its single pane.
    return role === 'document' && area.tabs.length > 1 ? roleOf(v.id) === 'document' : true;
  });
  return GROUP_ORDER.flatMap((group) => {
    const members = views.filter((v) => roleOf(v.id) === group);
    return members.length ? [{ group, members }] : [];
  });
}

/** A pane's display name: its own `title` param (a file name) before the view's. */
function paneTitle(pane: PaneState): string {
  return (
    (pane.params?.title as string | undefined) ?? resolveView(pane.viewId)?.title ?? pane.viewId
  );
}

function useCloseOnOutside(open: boolean, close: () => void) {
  useEffect(() => {
    if (!open) return;
    const onDown = () => close();
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onDown);
    // Also register with the Escape ladder, so Escape closes the menu as its own
    // rung instead of racing the blanket keydown listener above.
    const unregister = registerTransient(close);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onDown);
      unregister();
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

  // Guarded so a pane with unsaved changes (an editor buffer) can prompt first.
  const closeTab = (pane: PaneState) => void closePaneGuarded(pane.instanceId);

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
            {switchableViews(area).map(({ group, members }) => (
              <div key={group} className="frame-menu-group">
                <div className="frame-menu-group-label">{GROUP_LABEL[group]}</div>
                {members.map((v) => (
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
            ))}
          </div>
        )}
      </div>

      {area.tabs.length > 1 ? (
        <div className="frame-area-tabs" role="tablist">
          {area.tabs.map((pane, index) => (
            <div
              key={pane.instanceId}
              className={`frame-area-tab${index === area.activeTab ? ' active' : ''}`}
            >
              <button
                role="tab"
                aria-selected={index === area.activeTab}
                className="frame-area-tab-btn"
                title={paneTitle(pane)}
                onClick={() =>
                  layoutStore.dispatch({ type: 'SET_ACTIVE_TAB', areaId: area.id, index })
                }
              >
                {paneTitle(pane)}
              </button>
              <button
                className="frame-area-tab-close"
                title={`Close ${paneTitle(pane)}`}
                onClick={() => closeTab(pane)}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      ) : (
        <span className="frame-area-title">{active ? paneTitle(active) : 'Empty area'}</span>
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
