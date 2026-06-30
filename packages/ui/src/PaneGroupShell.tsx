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
  size: number;
}

const DEFAULT_SIDE_SIZE = 300;
const DEFAULT_BOTTOM_SIZE = 220;
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
  // Ref so resize drag closures always read current state without re-subscribing.
  const openStatesRef = useRef(openStates);
  openStatesRef.current = openStates;

  const toggle = (id: string) => {
    setOpenStates((prev) => {
      if (id in prev) {
        const next = { ...prev };
        delete next[id];
        return next;
      }
      return { ...prev, [id]: { position: 'right', size: DEFAULT_SIDE_SIZE } };
    });
  };

  const cyclePosition = (id: string) => {
    setOpenStates((prev) => {
      if (!(id in prev)) return prev;
      const current = prev[id];
      const position = NEXT_POSITION[current.position];
      const size = position === 'bottom' ? DEFAULT_BOTTOM_SIZE : DEFAULT_SIDE_SIZE;
      return { ...prev, [id]: { position, size } };
    });
  };

  const startResize = (e: React.MouseEvent, id: string, position: DockPosition) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startY = e.clientY;
    const startSize = openStatesRef.current[id]?.size ?? DEFAULT_SIDE_SIZE;

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
      setOpenStates((prev) => ({ ...prev, [id]: { ...prev[id], size: newSize } }));
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

  const renderCompanion = (entry: {
    companion: PanelGroupCompanion;
    state: CompanionViewState;
  }) => {
    const { companion: c, state } = entry;
    const Comp = resolveComponent(c.id);
    const { position, size } = state;
    const isVertical = position === 'left' || position === 'right';
    const style = isVertical ? { width: size } : { height: size };
    const nextPos = NEXT_POSITION[position];

    // Header + body wrapped so the resize handle can sit on the inner edge as a sibling.
    // For right/left companions the outer flex-direction is row so the handle is a
    // vertical 4px strip; for bottom companions it's column so the handle is horizontal.
    const content = (
      <div className="pane-group-companion-content">
        <div className="pane-group-companion-header">
          {c.icon ? <span>{c.icon}</span> : null}
          <span className="pane-group-companion-title">{c.label}</span>
          <button
            className="pane-group-companion-btn"
            title={`Move to ${nextPos}`}
            onClick={() => cyclePosition(c.id)}
          >
            {DOCK_ICON[nextPos]}
          </button>
          <button
            className="pane-group-companion-btn"
            title={`Close ${c.label}`}
            onClick={() => toggle(c.id)}
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
        onMouseDown={(e) => startResize(e, c.id, position)}
      />
    );

    return (
      <div
        key={c.id}
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
          {left.map(renderCompanion)}
          <div className="pane-group-primary">{PrimaryComp && <PrimaryComp />}</div>
          {right.map(renderCompanion)}
        </div>
        {bottom.length > 0 && (
          <div className="pane-group-bottom-row">{bottom.map(renderCompanion)}</div>
        )}
      </div>
    </div>
  );
}
