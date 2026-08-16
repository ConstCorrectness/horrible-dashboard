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

/** The one widget every install has, so an unconfigured board is not blank. */
const DEFAULT_WIDGETS = ['dashboard.welcome'];

export function BoardBackdrop({ params }: { params?: Record<string, unknown> }) {
  const requested = Array.isArray(params?.widgets)
    ? (params.widgets as unknown[]).filter((v): v is string => typeof v === 'string')
    : DEFAULT_WIDGETS;
  const columns = clampInt(params?.columns, 3, 1, 6);

  const tiles = requested
    .map((id) => ({ id, decl: resolveView(id) }))
    .filter(
      (t): t is { id: string; decl: NonNullable<ReturnType<typeof resolveView>> } => !!t.decl,
    );

  if (!tiles.length) {
    return (
      <div className="os-backdrop-board is-empty">
        <p>
          No widgets on this board yet. Right-click the desktop and choose{' '}
          <strong>Configure backdrop</strong> to add some.
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
