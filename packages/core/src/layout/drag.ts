/**
 * Pane drag-out: the in-flight drag payload, plus the drop verb.
 *
 * Kept as a tiny observable atom rather than React context or HTML5
 * `dataTransfer` for two reasons: the drop targets (center areas) are rendered
 * far from the drag sources (rails, dock headers, region strips) with no common
 * ancestor below the Frame, and `dataTransfer.getData` is unreadable during
 * `dragover` — so a target could not decide whether to accept a drop or how to
 * label it. Everything here is plain data; the gestures live in packages/ui.
 *
 * See docs/architecture/windowing.mdx.
 */
import { openPaneInArea, resolveView } from './controller';
import { areaOfInstance, findPaneAnywhere } from './model';
import { layoutStore } from './store';

/**
 * What is being dragged. A `pane` already exists somewhere and moves; a `view`
 * is not open yet (a dimmed rail glyph) and gets opened where it lands.
 */
export type DragPayload =
  | { kind: 'pane'; instanceId: string; viewId: string; title: string }
  | { kind: 'view'; viewId: string; title: string };

let active: DragPayload | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  for (const l of listeners) l();
}

export const paneDrag = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot(): DragPayload | null {
    return active;
  },
  begin(payload: DragPayload): void {
    active = payload;
    emit();
  },
  /** Always call on dragend, including cancelled drags, or targets stay armed. */
  end(): void {
    if (active === null) return;
    active = null;
    emit();
  },
};

/**
 * Drop `payload` into a center area: move it there if it is already open,
 * otherwise open it there. Returns the resulting instance id, or null.
 *
 * The single drop path — every gesture in packages/ui funnels here, so the rules
 * live in one testable place.
 */
export function dropPaneOnArea(payload: DragPayload, areaId: string): string | null {
  if (payload.kind === 'view') return openPaneInArea(payload.viewId, areaId);

  const before = layoutStore.getSnapshot();
  const located = findPaneAnywhere(before.frame, payload.instanceId);
  // The pane vanished mid-drag (closed from elsewhere): fall back to opening the
  // view, so the drop still does what the user visibly asked for.
  if (!located) {
    return resolveView(payload.viewId) ? openPaneInArea(payload.viewId, areaId) : null;
  }
  const after = layoutStore.dispatch({
    type: 'UNDOCK_PANE_TO_AREA',
    instanceId: payload.instanceId,
    areaId,
  });
  return after === before ? null : payload.instanceId;
}

/**
 * Drop onto a **position** in an area's tab strip: the same landing as
 * `dropPaneOnArea`, followed by a slide to `index`.
 *
 * Two cases, deliberately one code path. A tab dragged within its own strip is a
 * reorder and nothing else — `dropPaneOnArea` no-ops for it (the pane is already
 * in the area), so the reorder below is the whole operation. A tab dragged in from
 * another area, a dock, or the rail arrives at the end first and is then slid into
 * place. Composing rather than teaching the insert verb about indices keeps every
 * source honest: whatever `dropPaneOnArea` decides an arriving pane is, it stays
 * that, and this only moves it along the row.
 */
export function dropPaneOnTab(payload: DragPayload, areaId: string, index: number): string | null {
  // A pane already in this area is a pure reorder, and must skip the insert:
  // `dropPaneOnArea` reports null for it (correctly — it moved nothing), which
  // read as a failed drop and made a tab dragged within its own strip do nothing.
  const home =
    payload.kind === 'pane'
      ? areaOfInstance(layoutStore.getSnapshot().frame.center, payload.instanceId)
      : null;
  const instanceId =
    home?.id === areaId && payload.kind === 'pane'
      ? payload.instanceId
      : dropPaneOnArea(payload, areaId);
  if (!instanceId) return null;
  const area = areaOfInstance(layoutStore.getSnapshot().frame.center, instanceId);
  if (!area) return instanceId;
  const from = area.tabs.findIndex((t) => t.instanceId === instanceId);
  // Dropping on the tab to the right of where a pane already sits means "put it
  // there", which after its own removal is one slot left — clamped, because the
  // caller reports the index it drew, not the index after the splice.
  const to = Math.min(Math.max(index, 0), area.tabs.length - 1);
  if (from >= 0 && from !== to) {
    layoutStore.dispatch({ type: 'REORDER_TAB', areaId: area.id, from, to });
  }
  return instanceId;
}
