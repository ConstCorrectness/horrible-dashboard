/**
 * The VS Code-style activity rails: one down each side, each driving the dock on
 * that side. A glyph reflects where its view currently *is* — active in the dock,
 * stacked behind another tool, open out in the center grid, or closed — and
 * clicking does the matching thing (hide / reveal / focus / open).
 *
 * Rails are customizable: drag a glyph onto another rail section to move it
 * there (left ↔ right ↔ bottom) or within its own section to reorder;
 * right-click a glyph to hide it or move it by menu; right-click the rail
 * background for the visibility checklist and reset. Preferences persist via
 * rail-prefs in core.
 *
 * State derivation is `railEntries` in core (pure, unit-tested); this file is
 * only the rendering, the click routing, and the drag/menu gestures.
 */
import { Fragment, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  dockSidesOf,
  focusInstance,
  getRailPrefs,
  hideRailView,
  isDockable,
  layoutStore,
  listPanes,
  moveViewToDock,
  openToolInDock,
  paneDrag,
  railEntries,
  railPrefsStore,
  RAIL_SECTIONS,
  registry,
  resetRailPrefs,
  setViewHidden,
  toggleDock,
  type DockSide,
  type FrameState,
  type RailEntry,
  type RailSide,
  type RailState,
} from '@horrible/core';

const TITLE_SUFFIX: Record<RailState, string> = {
  active: ' — hide',
  docked: ' — show',
  center: ' — focus (open in the center)',
  closed: '',
};

const DOCK_LABEL: Record<DockSide, string> = {
  left: 'left rail',
  right: 'right rail',
  bottom: 'bottom dock',
};

function onPick(entry: RailEntry, side: DockSide, frame: FrameState): void {
  switch (entry.state) {
    case 'active':
      toggleDock(side, false);
      return;
    case 'docked':
      layoutStore.dispatch({ type: 'SET_ACTIVE_TOOL', side, instanceId: entry.instanceId! });
      return;
    case 'center': {
      // Already open out in the grid — focus it rather than opening a second copy.
      const located = listPanes(frame).find((p) => p.pane.instanceId === entry.instanceId);
      if (located) focusInstance(located);
      return;
    }
    case 'closed':
      openToolInDock(entry.viewId, side);
  }
}

type Menu =
  | { kind: 'glyph'; x: number; y: number; viewId: string; title: string; dockSide: DockSide }
  | { kind: 'rail'; x: number; y: number };

/** Insertion index from the pointer's Y among a section's glyph buttons. */
function dropIndexAt(section: HTMLElement, clientY: number): number {
  const buttons = Array.from(section.querySelectorAll<HTMLElement>('.frame-rail-btn[data-view]'));
  let index = buttons.length;
  for (let i = 0; i < buttons.length; i++) {
    const r = buttons[i].getBoundingClientRect();
    if (clientY < r.top + r.height / 2) {
      index = i;
      break;
    }
  }
  return index;
}

export function ActivityRail({ side, frame }: { side: RailSide; frame: FrameState }) {
  // Re-render on any prefs change (hide/move/reorder from either rail).
  useSyncExternalStore(railPrefsStore.subscribe, railPrefsStore.getSnapshot);
  const dragging = useSyncExternalStore(paneDrag.subscribe, paneDrag.getSnapshot);
  const [menu, setMenu] = useState<Menu | null>(null);
  const [dropHint, setDropHint] = useState<{ dockSide: DockSide; index: number } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // A drop is only offered for a dockable view; anything else drags past us.
  const armed = dragging !== null && isDockable(dragging.viewId);

  // Dismiss the context menu on any outside press.
  useEffect(() => {
    if (!menu) return;
    const dismiss = (e: PointerEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenu(null);
    };
    window.addEventListener('pointerdown', dismiss, true);
    return () => window.removeEventListener('pointerdown', dismiss, true);
  }, [menu]);

  const sections = RAIL_SECTIONS[side]
    .map((dockSide) => ({ dockSide, entries: railEntries(frame, dockSide) }))
    // While a dockable drag is in flight every section renders, so an empty
    // bottom section is still a drop target.
    .filter((s) => armed || s.entries.length > 0);

  // Hidden views whose home is one of this rail's sections — the checklist's
  // unhide half (railEntries filters them out of the sections themselves).
  const hiddenHere = getRailPrefs()
    .hidden.filter((viewId) => {
      const home = dockSidesOf(viewId)[0];
      return home !== undefined && RAIL_SECTIONS[side].includes(home);
    })
    .map((viewId) => ({
      viewId,
      title:
        [...registry.panels, ...registry.widgets].find((v) => v.id === viewId)?.title ?? viewId,
    }));

  const sectionDrag = (dockSide: DockSide) =>
    armed
      ? {
          onDragOver: (e: React.DragEvent<HTMLDivElement>) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            setDropHint({ dockSide, index: dropIndexAt(e.currentTarget, e.clientY) });
          },
          onDragLeave: (e: React.DragEvent<HTMLDivElement>) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropHint(null);
          },
          onDrop: (e: React.DragEvent<HTMLDivElement>) => {
            e.preventDefault();
            e.stopPropagation();
            const index = dropIndexAt(e.currentTarget, e.clientY);
            setDropHint(null);
            if (dragging) moveViewToDock(dragging.viewId, dockSide, index);
            paneDrag.end();
          },
        }
      : {};

  return (
    <nav
      className={`frame-rail frame-rail--${side}${armed ? ' frame-rail--drop-armed' : ''}`}
      aria-label={`${side} tools`}
      onContextMenu={(e) => {
        // Background menu; glyph buttons open their own and stop propagation.
        e.preventDefault();
        setMenu({ kind: 'rail', x: e.clientX, y: e.clientY });
      }}
    >
      {sections.map(({ dockSide, entries }) => (
        <div
          key={dockSide}
          className="frame-rail-section"
          data-dock={dockSide}
          {...sectionDrag(dockSide)}
        >
          {entries.map((entry, i) => (
            <Fragment key={entry.viewId}>
              {dropHint?.dockSide === dockSide && dropHint.index === i && (
                <div className="frame-rail-drop-line" />
              )}
              <button
                className={`frame-rail-btn frame-rail-btn--${entry.state}`}
                data-view={entry.viewId}
                title={`${entry.title}${TITLE_SUFFIX[entry.state]}`}
                aria-pressed={entry.state === 'active'}
                onClick={() => onPick(entry, dockSide, frame)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setMenu({
                    kind: 'glyph',
                    x: e.clientX,
                    y: e.clientY,
                    viewId: entry.viewId,
                    title: entry.title,
                    dockSide,
                  });
                }}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.effectAllowed = 'move';
                  // Payload rides in the store (dataTransfer is unreadable during
                  // dragover); this only exists so the drag has a drag image.
                  e.dataTransfer.setData('text/plain', entry.title);
                  paneDrag.begin(
                    entry.instanceId
                      ? {
                          kind: 'pane',
                          instanceId: entry.instanceId,
                          viewId: entry.viewId,
                          title: entry.title,
                        }
                      : { kind: 'view', viewId: entry.viewId, title: entry.title },
                  );
                }}
                onDragEnd={() => paneDrag.end()}
              >
                {entry.icon}
              </button>
            </Fragment>
          ))}
          {dropHint?.dockSide === dockSide && dropHint.index >= entries.length && (
            <div className="frame-rail-drop-line" />
          )}
        </div>
      ))}
      {side === 'left' && (
        <>
          <div className="frame-rail-spacer" />
          <button
            className="frame-rail-btn"
            title="Commands (Ctrl+K)"
            onClick={() => void registry.runCommand('shell.commandPalette')}
          >
            ⌘
          </button>
        </>
      )}
      {menu && (
        <div
          ref={menuRef}
          className="frame-menu frame-menu--context frame-rail-menu"
          style={{ left: menu.x, top: menu.y }}
          onContextMenu={(e) => e.preventDefault()}
        >
          {menu.kind === 'glyph' ? (
            <>
              <button
                className="frame-menu-item"
                onClick={() => {
                  setMenu(null);
                  hideRailView(menu.viewId);
                }}
              >
                Hide “{menu.title}”
              </button>
              {(['left', 'right', 'bottom'] as const)
                .filter((d) => d !== menu.dockSide)
                .map((d) => (
                  <button
                    key={d}
                    className="frame-menu-item"
                    onClick={() => {
                      setMenu(null);
                      moveViewToDock(menu.viewId, d);
                    }}
                  >
                    Move to {DOCK_LABEL[d]}
                  </button>
                ))}
            </>
          ) : (
            <>
              {sections.flatMap(({ entries }) =>
                entries.map((entry) => (
                  <button
                    key={entry.viewId}
                    className="frame-menu-item frame-menu-item--checked"
                    onClick={() => hideRailView(entry.viewId)}
                  >
                    ✓ {entry.title}
                  </button>
                )),
              )}
              {hiddenHere.map(({ viewId, title }) => (
                <button
                  key={viewId}
                  className="frame-menu-item frame-menu-item--unchecked"
                  onClick={() => setViewHidden(viewId, false)}
                >
                  {'  '}
                  {title}
                </button>
              ))}
              {(sections.some((s) => s.entries.length > 0) || hiddenHere.length > 0) && (
                <div className="frame-menu-separator" />
              )}
              <button
                className="frame-menu-item"
                onClick={() => {
                  setMenu(null);
                  resetRailPrefs();
                }}
              >
                Reset rail layout
              </button>
            </>
          )}
        </div>
      )}
    </nav>
  );
}
