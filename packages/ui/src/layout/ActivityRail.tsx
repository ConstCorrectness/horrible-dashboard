/**
 * The VS Code-style activity rails: one down each side, each driving the dock on
 * that side. A glyph reflects where its view currently *is* — active in the dock,
 * stacked behind another tool, open out in the center grid, or closed — and
 * clicking does the matching thing (hide / reveal / focus / open).
 *
 * State derivation is `railEntries` in core (pure, unit-tested); this file is
 * only the rendering and the click routing.
 */
import {
  focusInstance,
  layoutStore,
  listPanes,
  openToolInDock,
  paneDrag,
  railEntries,
  RAIL_SECTIONS,
  registry,
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

export function ActivityRail({ side, frame }: { side: RailSide; frame: FrameState }) {
  const sections = RAIL_SECTIONS[side]
    .map((dockSide) => ({ dockSide, entries: railEntries(frame, dockSide) }))
    .filter((s) => s.entries.length > 0);

  return (
    <nav className={`frame-rail frame-rail--${side}`} aria-label={`${side} tools`}>
      {sections.map(({ dockSide, entries }) => (
        <div key={dockSide} className="frame-rail-section">
          {entries.map((entry) => (
            <button
              key={entry.viewId}
              className={`frame-rail-btn frame-rail-btn--${entry.state}`}
              title={`${entry.title}${TITLE_SUFFIX[entry.state]}`}
              aria-pressed={entry.state === 'active'}
              onClick={() => onPick(entry, dockSide, frame)}
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
          ))}
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
