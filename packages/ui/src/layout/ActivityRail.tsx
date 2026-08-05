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
import { Fragment, useState, useSyncExternalStore } from 'react';
import {
  addContextMenuProvider,
  dockSidesOf,
  focusInstance,
  getRailPrefs,
  hideRailView,
  isDockable,
  layoutStore,
  listPanes,
  moveViewToDock,
  openContextMenu,
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

/**
 * The rail's two right-click surfaces, registered on the shared context-menu
 * registry rather than rendered inline.
 *
 * These are shell chrome, not a module, so they register at import time instead of
 * through a `ModuleManifest`. The target carries the state the items need
 * (`entries`, `hidden`) because that state is derived from the `frame` prop and a
 * provider has no component to read it from — and because a target is just data,
 * another module can still add an item to a rail glyph without knowing any of it.
 */
addContextMenuProvider({
  kind: 'rail.glyph',
  items: (target) => {
    const viewId = String(target.viewId ?? '');
    const dockSide = target.dockSide as DockSide;
    return [
      {
        id: 'rail.hide',
        label: `Hide “${String(target.title ?? viewId)}”`,
        run: () => hideRailView(viewId),
      },
      ...(['left', 'right', 'bottom'] as const)
        .filter((d) => d !== dockSide)
        .map((d) => ({
          id: `rail.moveTo.${d}`,
          label: `Move to ${DOCK_LABEL[d]}`,
          run: () => moveViewToDock(viewId, d),
        })),
    ];
  },
});

interface RailEntryRef {
  viewId: string;
  title: string;
}

addContextMenuProvider({
  kind: 'rail',
  items: (target) => {
    const shown = (target.entries as RailEntryRef[] | undefined) ?? [];
    const hidden = (target.hidden as RailEntryRef[] | undefined) ?? [];
    return [
      // A checklist: `checked` draws the tick column, so visible and hidden rows
      // line up instead of being distinguished by two leading spaces.
      ...shown.map((e) => ({
        id: `rail.toggle.${e.viewId}`,
        label: e.title,
        checked: true,
        run: () => hideRailView(e.viewId),
      })),
      ...hidden.map((e) => ({
        id: `rail.toggle.${e.viewId}`,
        label: e.title,
        checked: false,
        run: () => setViewHidden(e.viewId, false),
      })),
    ];
  },
});

// A second provider, so "Reset" lands in its own group with a separator above it
// — the same visual break the inline menu drew by hand, but now it appears only
// when there is something above it to separate from.
addContextMenuProvider({
  kind: 'rail',
  order: 10,
  items: () => [{ id: 'rail.reset', label: 'Reset rail layout', run: () => resetRailPrefs() }],
});

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
  const [dropHint, setDropHint] = useState<{ dockSide: DockSide; index: number } | null>(null);

  // A drop is only offered for a dockable view; anything else drags past us.
  const armed = dragging !== null && isDockable(dragging.viewId);

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
        openContextMenu(e, {
          kind: 'rail',
          side,
          entries: sections.flatMap((s) =>
            s.entries.map((entry) => ({ viewId: entry.viewId, title: entry.title })),
          ),
          hidden: hiddenHere,
        });
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
                  openContextMenu(e, {
                    kind: 'rail.glyph',
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
    </nav>
  );
}
