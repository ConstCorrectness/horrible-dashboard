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
import {
  getActiveScope,
  isEditableTarget,
  registry,
  type PanelGroupDecl,
  type PanelGroupCompanion,
} from '@horrible/core';
import { useEffect, useRef, useState } from 'react';

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

// Collapse a dock region toward its edge (Blender-style toolbar/sidebar hide) and
// the glyph on the thin rail that expands it back.
const COLLAPSE_ICON: Record<DockPosition, string> = {
  right: '»',
  bottom: '⤓',
  left: '«',
};
const EXPAND_ICON: Record<DockPosition, string> = {
  right: '«',
  bottom: '⤒',
  left: '»',
};
const REGION_LABEL: Record<DockPosition, string> = {
  right: 'right subpane',
  bottom: 'bottom subpane',
  left: 'left subpane',
};

// Keyboard toggles for the three dock regions (Blender toolbar/sidebar region
// keys). A contiguous bracket cluster rather than Blender's literal T/N — those
// letters collide with per-companion toggle keys (`key`), whereas `[ ] \` are
// reserved and never shadow a module's companion letters or hijack typing.
const REGION_KEY: Record<DockPosition, string> = {
  left: '[',
  right: ']',
  bottom: '\\',
};
const KEY_BY_CHAR: Record<string, DockPosition> = { '[': 'left', ']': 'right', '\\': 'bottom' };

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
  // Whether each dock region is collapsed (hidden to a thin rail) — the Blender
  // toolbar/sidebar region toggle. Independent of which companions are open there:
  // collapsing hides the whole side without forgetting its companions, and
  // expanding restores them exactly. A region with no open companions has no dock,
  // so its toggle simply doesn't appear.
  const [collapsedByPosition, setCollapsedByPosition] = useState<
    Partial<Record<DockPosition, boolean>>
  >({});
  // Ref so resize drag closures always read current state without re-subscribing.
  const openStatesRef = useRef(openStates);
  openStatesRef.current = openStates;

  const toggleRegionCollapsed = (position: DockPosition) =>
    setCollapsedByPosition((prev) => ({ ...prev, [position]: !prev[position] }));
  // Reveal a region when a companion lands there — a freshly opened/moved companion
  // in a collapsed dock would otherwise be invisible.
  const expandRegion = (position: DockPosition) =>
    setCollapsedByPosition((prev) => (prev[position] ? { ...prev, [position]: false } : prev));

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
    if (!wasOpen) expandRegion('right'); // opening lands it at right — make sure it shows
  };

  // Force a companion open (idempotent) and make it the active tab in its dock —
  // used by programmatic reveals (registry.revealCompanion), e.g. the Game Board
  // popping when a match starts. A no-op re-activate if it's already open.
  const openCompanion = (id: string) => {
    const existing = openStatesRef.current[id];
    if (existing) {
      setActiveByPosition((prev) => ({ ...prev, [existing.position]: id }));
      expandRegion(existing.position);
      return;
    }
    setOpenStates((prev) => (id in prev ? prev : { ...prev, [id]: { position: 'right' } }));
    setActiveByPosition((prev) => ({ ...prev, right: id }));
    expandRegion('right');
  };

  // Reveal companions requested via registry.revealCompanion — both those buffered
  // before this shell mounted and any that arrive while it's open.
  useEffect(() => {
    const reveal = (id: string) => {
      if (group.companions.some((c) => c.id === id) && registry.claimReveal(id)) {
        openCompanion(id);
      }
    };
    for (const c of group.companions) reveal(c.id);
    return registry.onRevealCompanion(reveal);
    // openCompanion reads live state through refs; only the group identity matters here.
  }, [group]);

  // Keyboard toggles (Blender T/N style): a scoped keybinding fires a
  // `panelGroup.toggle:<id>` command → registry.toggleCompanion → here. Flip the
  // companion if it belongs to this group. `toggle` reads live state via refs.
  useEffect(() => {
    return registry.onToggleCompanion((id) => {
      if (group.companions.some((c) => c.id === id)) toggle(id);
    });
  }, [group]);

  // Region collapse keys (`[` left, `]` right, `\` bottom) — the Blender
  // toolbar/sidebar region toggle, from the keyboard. Only fires while this group's
  // pane is the active scope, never while typing, and only for a region that
  // actually has a dock (an empty side has nothing to toggle).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (getActiveScope() !== group.primary) return;
      if (isEditableTarget(e.target)) return;
      const pos = KEY_BY_CHAR[e.key];
      if (!pos) return;
      const hasDock = Object.values(openStatesRef.current).some((s) => s.position === pos);
      if (!hasDock) return;
      e.preventDefault();
      toggleRegionCollapsed(pos);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [group]);

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
    expandRegion(newPos); // don't drop a companion into a collapsed dock
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
    const isVertical = position === 'left' || position === 'right';

    // Collapsed: render a thin rail on the edge (Blender's hidden toolbar/sidebar)
    // that expands the region back on click. The companions stay open underneath.
    if (collapsedByPosition[position]) {
      return (
        <button
          key={position}
          className={`pane-group-rail pane-group-rail--${position}`}
          title={`Show ${REGION_LABEL[position]} (${REGION_KEY[position]})`}
          aria-label={`Show ${REGION_LABEL[position]}`}
          onClick={() => toggleRegionCollapsed(position)}
        >
          <span className="pane-group-rail-glyph">{EXPAND_ICON[position]}</span>
          {entries.map(({ companion: c }) => (
            <span key={c.id} className="pane-group-rail-tab">
              {c.icon ?? c.label[0]}
            </span>
          ))}
        </button>
      );
    }

    const activeId = activeByPosition[position] ?? entries[0].companion.id;
    const active = entries.find((e) => e.companion.id === activeId) ?? entries[0];
    const Comp = resolveComponent(active.companion.id);
    const size = sizeByPosition[position];
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
            title={`Collapse ${REGION_LABEL[position]} (${REGION_KEY[position]})`}
            aria-label={`Collapse ${REGION_LABEL[position]}`}
            onClick={() => toggleRegionCollapsed(position)}
          >
            {COLLAPSE_ICON[position]}
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
      {/* Companion toggle strip — omitted for a companion-less group (e.g. AgentTown),
          which is just a primary in a group shell with room to grow companions later. */}
      {group.companions.length > 0 && (
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
      )}

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
