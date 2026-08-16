/**
 * The dashboard-style backdrop: widgets laid straight onto the desktop, under
 * whatever windows are open. `params.widgets` is a list of registered view ids;
 * anything unknown (a plugin since uninstalled) is skipped rather than rendered
 * as an error tile, because a backdrop is not a place to report a problem.
 *
 * A board tile is **not a pane**. It renders the view's component inside the two
 * contexts a view actually reads (`PaneInstanceContext`, `PaneParamsContext`)
 * and stops there: no focus dispatch, no capture, no sections, no lifetime
 * session. Routing it through `PaneHost` would put a `FOCUS_PANE` for an
 * instance that is not in the frame into the store, and the frame's focused-pane
 * resolution would then be pointing at something that does not exist.
 */
import { PaneInstanceContext, PaneParamsContext, registry, resolveView } from '@horrible/core';
import { useMemo } from 'react';

/**
 * What an unconfigured board shows: every view that declares `role: 'widget'`.
 *
 * This is the one place the third role means something outside the tiling grid.
 * A widget is defined as a readout — glanceable, no interaction depth, sized to
 * a tile — which is exactly the board's tenancy, so "the registered widgets" is
 * a better default than one hardcoded id and it grows as modules are added.
 *
 * Capped, because a board is a glance and twenty tiles is a wall of text; and
 * `dashboard.welcome` is pinned first so an install with no other widgets still
 * shows something rather than an empty-state message.
 */
const MAX_DEFAULT_TILES = 6;

function defaultWidgets(): string[] {
  const ids = registry.widgets
    .filter((w) => w.role === 'widget' && !w.embedded)
    .map((w) => w.id)
    .sort((a, b) => (a === 'dashboard.welcome' ? -1 : b === 'dashboard.welcome' ? 1 : 0));
  return ids.length ? ids.slice(0, MAX_DEFAULT_TILES) : ['dashboard.welcome'];
}

export function BoardBackdrop({ params }: { params?: Record<string, unknown> }) {
  const requested = Array.isArray(params?.widgets)
    ? (params.widgets as unknown[]).filter((v): v is string => typeof v === 'string')
    : defaultWidgets();
  const columns = clampInt(params?.columns, 3, 1, 6);

  const tiles = requested
    .map((id) => ({ id, decl: resolveView(id) }))
    .filter(
      (t): t is { id: string; decl: NonNullable<ReturnType<typeof resolveView>> } => !!t.decl,
    );

  if (!tiles.length) {
    return (
      <div className="os-backdrop-board is-empty">
        {/* Names something that exists. This used to send the reader to a
            "Configure backdrop" item on the desktop menu, which was never
            built — an empty state whose only instruction is a dead end is worse
            than one that just says the board is empty. */}
        <p>
          Nothing on this board. Ask the agent to put widgets on it — “put the training metrics on
          the desktop” — or pick another backdrop by right-clicking the desktop.
        </p>
      </div>
    );
  }

  return (
    <div
      className="os-backdrop-board"
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {tiles.map(({ id, decl }) => (
        <BoardTile key={id} id={id} title={decl.title} icon={decl.icon} Body={decl.component} />
      ))}
    </div>
  );
}

function BoardTile({
  id,
  title,
  icon,
  Body,
}: {
  id: string;
  title: string;
  icon?: string;
  Body: React.ComponentType;
}) {
  // A stable, board-scoped instance id: distinct from any pane instance so a
  // widget open in both places keeps two independent bits of state, and stable
  // across renders so whatever the widget keys off it is not rebuilt.
  const instanceId = useMemo(() => `backdrop:${id}`, [id]);
  const paneParams = useMemo(() => ({ panelId: id }), [id]);
  return (
    <section className="os-board-tile">
      <header className="os-board-tile-head">
        {icon && <span aria-hidden="true">{icon}</span>}
        <h2>{title}</h2>
        <button
          type="button"
          className="os-board-tile-open"
          title={`Open ${title} in a window`}
          onClick={() => registry.openPanel(id)}
        >
          ↗
        </button>
      </header>
      <div className="os-board-tile-body">
        <PaneInstanceContext.Provider value={instanceId}>
          <PaneParamsContext.Provider value={paneParams}>
            <Body />
          </PaneParamsContext.Provider>
        </PaneInstanceContext.Provider>
      </div>
    </section>
  );
}

function clampInt(value: unknown, fallback: number, min: number, max: number): number {
  const n = Math.round(typeof value === 'number' ? value : Number(value));
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}
