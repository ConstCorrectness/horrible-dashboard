/**
 * The Blender-style header strip of a center area: a type switcher (icon
 * dropdown of every view the area can host, grouped by role), the tab row for
 * document areas,
 * region toggles for the active pane, and the area menu (split/join/fullscreen/
 * collapse/float/close). Collapsible per-area for chrome-less dashboards.
 */
import { useEffect, useRef, useState } from 'react';
import {
  addContextMenuProvider,
  closePaneGuarded,
  dropPaneOnTab,
  findArea,
  fullscreenArea,
  joinAreaDirection,
  layoutStore,
  minimizePane,
  moveTabToSplit,
  openContextMenu,
  openFramePane,
  openPaneInArea,
  paneDrag,
  registerTransient,
  registry,
  paneDisplayTitle,
  resolveView,
  roleOf,
  splitAreaBy,
  toggleRegion,
  type AreaNode,
  type ContextMenuItem,
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

/** A pane's display name — the shared rule, so a tab and its taskbar button agree. */
function paneTitle(pane: PaneState): string {
  return paneDisplayTitle(pane);
}

/**
 * The document tab menu — the same contract the workspace strip and the activity
 * rail already use, so a module can add an item to a tab (an editor offering
 * "Reveal in Explorer", say) without this file learning about it.
 *
 * The bulk-close verbs read the *current* tab list from the store rather than a
 * list captured when the menu opened, and close by instance id rather than by
 * index. Both matter for the same reason: each close is guarded and may prompt,
 * so the strip can shift under a half-finished "Close Others" — closing by
 * position would then start eating the wrong panes.
 */
addContextMenuProvider({
  kind: 'area.tab',
  items: (target) => {
    const areaId = String(target.areaId ?? '');
    const instanceId = String(target.instanceId ?? '');
    const tabsNow = () => {
      const area = findAreaTabs(areaId);
      return area ?? [];
    };
    const closeMany = (pick: (ids: string[], self: number) => string[]) => () => {
      const ids = tabsNow().map((t) => t.instanceId);
      const self = ids.indexOf(instanceId);
      if (self < 0) return;
      for (const id of pick(ids, self)) void closePaneGuarded(id);
    };
    const count = tabsNow().length;
    const index = tabsNow().findIndex((t) => t.instanceId === instanceId);

    const items: ContextMenuItem[] = [
      {
        id: 'tab.close',
        label: 'Close',
        hint: 'mod+w',
        run: () => void closePaneGuarded(instanceId),
      },
    ];
    // Absent, not disabled, when there is nothing they could close — a permanently
    // grey "Close Others" on a single-tab area only invites clicking it.
    if (count > 1) {
      items.push({
        id: 'tab.closeOthers',
        label: 'Close Others',
        run: closeMany((ids) => ids.filter((id) => id !== instanceId)),
      });
    }
    if (index >= 0 && index < count - 1) {
      items.push({
        id: 'tab.closeRight',
        label: 'Close to the Right',
        run: closeMany((ids, self) => ids.slice(self + 1)),
      });
    }
    items.push({ id: 'tab.closeAll', label: 'Close All', run: closeMany((ids) => ids) });
    return items;
  },
});

/** Tab-splitting and floating: a second group, so it reads as a different verb. */
addContextMenuProvider({
  kind: 'area.tab',
  order: 1,
  items: (target) => {
    const areaId = String(target.areaId ?? '');
    const instanceId = String(target.instanceId ?? '');
    return [
      {
        id: 'tab.splitRight',
        label: 'Split Right',
        run: () => void moveTabToSplit(areaId, instanceId, 'right'),
      },
      {
        id: 'tab.splitDown',
        label: 'Split Down',
        run: () => void moveTabToSplit(areaId, instanceId, 'below'),
      },
      {
        id: 'tab.minimize',
        label: 'Minimize',
        hint: 'taskbar',
        // Named in the hint because a minimized tile leaves no trace in the
        // frame — the taskbar button is the only way back, and a verb that
        // makes a pane vanish should say where it went.
        run: () => void minimizePane(instanceId),
      },
      {
        id: 'tab.float',
        label: 'Float',
        run: () => void registry.layoutController?.setPaneFloating(instanceId, true),
      },
    ];
  },
});

/** This area's live tab list, read from the store at call time. */
function findAreaTabs(areaId: string): PaneState[] | null {
  return findArea(layoutStore.getSnapshot().frame.center, areaId)?.tabs ?? null;
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
  const [addOpen, setAddOpen] = useState(false);
  useCloseOnOutside(switcherOpen, () => setSwitcherOpen(false));
  useCloseOnOutside(menuOpen, () => setMenuOpen(false));
  useCloseOnOutside(addOpen, () => setAddOpen(false));
  const headerRef = useRef<HTMLDivElement>(null);
  /** Which tab slot the in-flight drag would land in, for the insertion marker. */
  const [dropIndex, setDropIndex] = useState<number | null>(null);

  // Minimized tabs stay in `area.tabs` (that is how restoring is exact) but are
  // not part of the strip and are never the pane the header describes.
  const activeTab: PaneState | undefined = area.tabs[area.activeTab];
  const active: PaneState | undefined = activeTab?.minimized ? undefined : activeTab;
  const shownTabs = area.tabs.filter((t) => !t.minimized).length;
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

  /**
   * The `+` on the strip: open a view **as another tab of this area**, rather than
   * letting the role router send it to its default home. That routing is right for
   * a command-palette open and wrong here — the user pointed at a specific group.
   */
  const addTab = (viewId: string) => {
    setAddOpen(false);
    layoutStore.dispatch({ type: 'FOCUS_AREA', areaId: area.id });
    openPaneInArea(viewId, area.id);
  };

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

      {shownTabs > 0 ? (
        <div className="frame-area-tabs" role="tablist">
          {/* Mapped over the full list, skipping minimized ones, so `index` stays
              the real index into `area.tabs` — every tab verb here (activate,
              reorder, drop) addresses tabs by position. */}
          {area.tabs.map((pane, index) =>
            pane.minimized ? null : (
              <div
                key={pane.instanceId}
                // `draggable` on the wrapper, not the label button: a drag started on
                // the close button would otherwise never fire its click.
                draggable
                className={[
                  'frame-area-tab',
                  index === area.activeTab ? 'active' : '',
                  dropIndex === index ? 'frame-area-tab--drop' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                onDragStart={(e) => {
                  e.dataTransfer.effectAllowed = 'move';
                  // Some browsers cancel a drag with an empty dataTransfer; the
                  // payload itself rides the store (see layout/drag.ts).
                  e.dataTransfer.setData('text/plain', paneTitle(pane));
                  paneDrag.begin({
                    kind: 'pane',
                    instanceId: pane.instanceId,
                    viewId: pane.viewId,
                    title: paneTitle(pane),
                  });
                }}
                onDragEnd={() => {
                  paneDrag.end();
                  setDropIndex(null);
                }}
                onDragOver={(e) => {
                  // Read the payload from the store, not from the render closure:
                  // `dragstart` and `dragover` can both land before React has
                  // re-rendered, and a closure captured pre-drag still says "no
                  // drag in flight" — the drop then silently does nothing.
                  if (!paneDrag.getSnapshot()) return;
                  // Stopped, or the area beneath claims the drop and the pane lands
                  // at the end of the strip instead of where the marker is drawn.
                  e.preventDefault();
                  e.stopPropagation();
                  e.dataTransfer.dropEffect = 'move';
                  setDropIndex(index);
                }}
                onDragLeave={() => setDropIndex((at) => (at === index ? null : at))}
                onDrop={(e) => {
                  const payload = paneDrag.getSnapshot();
                  if (!payload) return;
                  e.preventDefault();
                  e.stopPropagation();
                  setDropIndex(null);
                  dropPaneOnTab(payload, area.id, index);
                  paneDrag.end();
                }}
                onContextMenu={(e) => {
                  if (
                    openContextMenu(e, {
                      kind: 'area.tab',
                      areaId: area.id,
                      instanceId: pane.instanceId,
                      viewId: pane.viewId,
                      title: paneTitle(pane),
                    })
                  ) {
                    e.preventDefault();
                  }
                }}
              >
                <button
                  role="tab"
                  aria-selected={index === area.activeTab}
                  className="frame-area-tab-btn"
                  title={paneTitle(pane)}
                  onClick={() =>
                    layoutStore.dispatch({ type: 'SET_ACTIVE_TAB', areaId: area.id, index })
                  }
                  // Middle-click closes, as everywhere else tabs exist.
                  onAuxClick={(e) => {
                    if (e.button === 1) {
                      e.preventDefault();
                      closeTab(pane);
                    }
                  }}
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
            ),
          )}
          <div className="frame-area-tab-add">
            <button
              className="frame-area-tab-add-btn"
              title="Add a pane to this group"
              aria-haspopup="menu"
              aria-expanded={addOpen}
              onClick={() => setAddOpen((v) => !v)}
            >
              ＋
            </button>
            {addOpen && (
              <div className="frame-menu" onMouseDown={(e) => e.stopPropagation()}>
                {switchableViews(area).map(({ group, members }) => (
                  <div key={group} className="frame-menu-group">
                    <div className="frame-menu-group-label">{GROUP_LABEL[group]}</div>
                    {members.map((v) => (
                      <button key={v.id} className="frame-menu-item" onClick={() => addTab(v.id)}>
                        <span className="frame-menu-icon">{v.icon ?? v.title[0]}</span>
                        {v.title}
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : (
        <span className="frame-area-title">Empty area</span>
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
