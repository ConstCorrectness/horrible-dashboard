/**
 * Renders when a pane's view is the **primary** of a panel group.
 *
 * - Primary always fills the main area.
 * - Toggle strip at the top shows/hides each companion (all collapsed by default).
 * - Open companions appear as resizable side-panels (right/left/bottom of primary).
 * - A cycle button in each companion header rotates its dock position: right → bottom → left → right.
 *
 * See docs/architecture/panel-groups.mdx.
 */
import { registry, type PanelGroupDecl, type PanelGroupCompanion } from '@horrible/core';
import { useRef, useState } from 'react';

interface Props {
  group: PanelGroupDecl;
}

type DockPosition = 'right' | 'bottom' | 'left';

interface CompanionViewState {
  position: DockPosition;
}

const DEFAULT_SIDE_SIZE = 300;
const DEFAULT_BOTTOM_SIZE = 220;
const DEFAULT_SIZE: Record<DockPosition, number> = {
  right: DEFAULT_SIDE_SIZE,
  left: DEFAULT_SIDE_SIZE,
  bottom: DEFAULT_BOTTOM_SIZE,
};
const MIN_SIZE = 120;
const MAX_SIDE_SIZE = 700;
const MAX_BOTTOM_SIZE = 480;

const NEXT_POSITION: Record<DockPosition, DockPosition> = {
  right: 'bottom',
  bottom: 'left',
  left: 'right',
};

const DOCK_ICON: Record<DockPosition, string> = {
  right: '→',
  bottom: '↓',
  left: '←',
};

function resolveComponent(id: string) {
  return (
    (registry.panels.find((p) => p.id === id) ?? registry.widgets.find((w) => w.id === id))
      ?.component ?? null
  );
}

export function PaneGroupShell({ group }: Props) {
  const [openStates, setOpenStates] = useState<Record<string, CompanionViewState>>({});
  // Which companion is the visible tab within its position's shared dock (one
  // dock per position — opening a second companion at the same position tabs
  // onto it rather than carving out another split).
  const [activeByPosition, setActiveByPosition] = useState<Partial<Record<DockPosition, string>>>(
    {},
  );
  // Size belongs to the dock (position), not the tab — so switching the active
  // companion within a dock keeps the width/height the user set.
  const [sizeByPosition, setSizeByPosition] = useState<Record<DockPosition, number>>(DEFAULT_SIZE);
  // Ref so resize drag closures always read current state without re-subscribing.
  const openStatesRef = useRef(openStates);
  openStatesRef.current = openStates;

  const toggle = (id: string) => {
    const wasOpen = id in openStatesRef.current;
    setOpenStates((prev) => {
      if (id in prev) {
        const next = { ...prev };
        delete next[id];
        return next;
      }
      return { ...prev, [id]: { position: 'right' } };
    });
    setActiveByPosition((prev) => {
      if (wasOpen) {
        const pos = openStatesRef.current[id].position;
        if (prev[pos] !== id) return prev;
        const remaining = Object.entries(openStatesRef.current)
          .filter(([cid, st]) => cid !== id && st.position === pos)
          .map(([cid]) => cid);
        return { ...prev, [pos]: remaining[0] };
      }
      return { ...prev, right: id };
    });
  };

  const cyclePosition = (id: string) => {
    const current = openStatesRef.current[id];
    if (!current) return;
    const oldPos = current.position;
    const newPos = NEXT_POSITION[oldPos];
    setOpenStates((prev) => (id in prev ? { ...prev, [id]: { position: newPos } } : prev));
    setActiveByPosition((prev) => {
      const next = { ...prev };
      if (next[oldPos] === id) {
        const remaining = Object.entries(openStatesRef.current)
          .filter(([cid, st]) => cid !== id && st.position === oldPos)
          .map(([cid]) => cid);
        next[oldPos] = remaining[0];
      }
      next[newPos] = id;
      return next;
    });
  };

  const startResize = (e: React.MouseEvent, position: DockPosition) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startY = e.clientY;
    const startSize = sizeByPosition[position];

    const onMove = (me: MouseEvent) => {
      let newSize: number;
      if (position === 'right') {
        // Left edge of right companion: drag left to grow
        newSize = startSize - (me.clientX - startX);
      } else if (position === 'left') {
        // Right edge of left companion: drag right to grow
        newSize = startSize + (me.clientX - startX);
      } else {
        // Top edge of bottom companion: drag up to grow
        newSize = startSize - (me.clientY - startY);
      }
      const max = position === 'bottom' ? MAX_BOTTOM_SIZE : MAX_SIDE_SIZE;
      newSize = Math.max(MIN_SIZE, Math.min(max, newSize));
      setSizeByPosition((prev) => ({ ...prev, [position]: newSize }));
    };

    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const PrimaryComp = resolveComponent(group.primary);

  const openEntries = group.companions
    .filter((c) => c.id in openStates)
    .map((c) => ({ companion: c, state: openStates[c.id] }));

  const left = openEntries.filter((e) => e.state.position === 'left');
  const right = openEntries.filter((e) => e.state.position === 'right');
  const bottom = openEntries.filter((e) => e.state.position === 'bottom');

  // One dock per position: every companion opened at that position tabs onto
  // this same box (VS Code–style) instead of each carving out its own split.
  const renderPositionGroup = (
    position: DockPosition,
    entries: { companion: PanelGroupCompanion; state: CompanionViewState }[],
  ) => {
    if (entries.length === 0) return null;
    const activeId = activeByPosition[position] ?? entries[0].companion.id;
    const active = entries.find((e) => e.companion.id === activeId) ?? entries[0];
    const Comp = resolveComponent(active.companion.id);
    const size = sizeByPosition[position];
    const isVertical = position === 'left' || position === 'right';
    const style = isVertical ? { width: size } : { height: size };
    const nextPos = NEXT_POSITION[position];

    // Header + body wrapped so the resize handle can sit on the inner edge as a sibling.
    // For right/left companions the outer flex-direction is row so the handle is a
    // vertical 4px strip; for bottom companions it's column so the handle is horizontal.
    const content = (
      <div className="pane-group-companion-content">
        <div className="pane-group-companion-header">
          {entries.length > 1 ? (
            <div className="pane-group-companion-tabs">
              {entries.map(({ companion: c }) => (
                <button
                  key={c.id}
                  className={`pane-group-companion-tab${c.id === activeId ? ' active' : ''}`}
                  title={c.label}
                  onClick={() => setActiveByPosition((prev) => ({ ...prev, [position]: c.id }))}
                >
                  {c.icon ? <span>{c.icon}</span> : null}
                  <span>{c.label}</span>
                </button>
              ))}
            </div>
          ) : (
            <>
              {active.companion.icon ? <span>{active.companion.icon}</span> : null}
              <span className="pane-group-companion-title">{active.companion.label}</span>
            </>
          )}
          <button
            className="pane-group-companion-btn"
            title={`Move to ${nextPos}`}
            onClick={() => cyclePosition(activeId)}
          >
            {DOCK_ICON[nextPos]}
          </button>
          <button
            className="pane-group-companion-btn"
            title={`Close ${active.companion.label}`}
            onClick={() => toggle(activeId)}
          >
            ✕
          </button>
        </div>
        <div className="pane-group-companion-body">{Comp && <Comp />}</div>
      </div>
    );

    const handle = (
      <div
        className={`pane-group-resize-handle pane-group-resize-handle--${isVertical ? 'v' : 'h'}`}
        onMouseDown={(e) => startResize(e, position)}
      />
    );

    return (
      <div
        key={position}
        className={`pane-group-companion pane-group-companion--${position}`}
        style={style}
      >
        {/* Handle sits on the edge facing the primary:
            right companion → handle on left (first in row)
            bottom companion → handle on top (first in column)
            left companion → handle on right (last in row) */}
        {(position === 'right' || position === 'bottom') && handle}
        {content}
        {position === 'left' && handle}
      </div>
    );
  };

  return (
    <div className="pane-group-shell">
      {/* Companion toggle strip */}
      <div className="pane-group-toggles" aria-label={`${group.label} companions`}>
        {group.companions.map((c) => (
          <button
            key={c.id}
            className={`pane-group-toggle${c.id in openStates ? ' active' : ''}`}
            aria-pressed={c.id in openStates}
            title={c.label}
            onClick={() => toggle(c.id)}
          >
            {c.icon ? <span className="pane-group-toggle-icon">{c.icon}</span> : null}
            <span className="pane-group-toggle-label">{c.label}</span>
          </button>
        ))}
      </div>

      {/* Content: middle row (left | primary | right) + optional bottom row */}
      <div className="pane-group-body">
        <div className="pane-group-middle-row">
          {renderPositionGroup('left', left)}
          <div className="pane-group-primary">{PrimaryComp && <PrimaryComp />}</div>
          {renderPositionGroup('right', right)}
        </div>
        {bottom.length > 0 && (
          <div className="pane-group-bottom-row">{renderPositionGroup('bottom', bottom)}</div>
        )}
      </div>
    </div>
  );
}
