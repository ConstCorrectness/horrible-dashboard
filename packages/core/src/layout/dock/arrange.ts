/**
 * Re-laying the centre in one move — the workbench answer to "arrange my windows".
 *
 * The floating desktop's arrangements (grid, cascade, columns, rows) moved free
 * rects. The workbench has no free rects, so an arrangement here rebuilds the
 * centre split tree instead. Docks and popouts are left alone: they are tools and
 * windows the user put somewhere on purpose, and the agent's "lay everything out"
 * is about the documents.
 *
 * Pure: ids come from `frame.paneSeq`, like every other structural edit.
 */
import { areaId } from '../model';
import type { AreaNode, PaneState } from '../types';
import { sanitize, targetCenterArea } from './frame';
import { collectAreas, splitId } from './tree';
import type { DockFrame, LayoutNode, SplitNode } from './types';

export type ArrangeStyle = 'even' | 'tabs' | 'columns' | 'rows' | 'grid';

export const ARRANGE_STYLES: readonly ArrangeStyle[] = ['even', 'tabs', 'columns', 'rows', 'grid'];

/** Every split's children get equal shares. Same node back when nothing changes. */
function evenSizes(node: LayoutNode): LayoutNode {
  if (node.kind === 'area') return node;
  const children = node.children.map(evenSizes);
  const share = 1 / children.length;
  const sizesEven = node.sizes.every((s) => Math.abs(s - share) < 1e-9);
  if (sizesEven && children.every((c, i) => c === node.children[i])) return node;
  return { ...node, children, sizes: children.map(() => share) };
}

/**
 * Rebuild the centre.
 *
 * - `even`: keep the tree, give every split equal shares.
 * - `tabs`: every centre pane as tabs of one group (the focused centre group).
 * - `columns` / `rows`: one group per centre pane, side by side / stacked.
 * - `grid`: one group per pane, in rows of ⌈√n⌉.
 *
 * Each existing group's id is kept by its first pane, so a group the user (or an
 * agent) was referring to by id is still there afterwards where it can be. Returns
 * the same frame when the arrangement is already in place or there is nothing to
 * arrange.
 */
export function arrangeCenter(frame: DockFrame, style: ArrangeStyle): DockFrame {
  if (style === 'even') {
    const center = evenSizes(frame.center);
    return center === frame.center ? frame : { ...frame, center };
  }

  const areas = collectAreas(frame.center);
  const panes = areas.flatMap((a) => a.tabs);
  if (panes.length === 0) return frame;

  if (style === 'tabs') {
    if (areas.length === 1) return frame;
    const home = targetCenterArea(frame);
    // The pane showing stays the pane showing.
    const showing =
      panes.find((p) => p.instanceId === frame.focusedInstanceId) ?? home.tabs[home.activeTab];
    const area: AreaNode = {
      kind: 'area',
      id: home.id,
      tabs: panes,
      activeTab: Math.max(0, panes.indexOf(showing ?? panes[0])),
    };
    return sanitize({ ...frame, center: area, maximizedAreaId: null });
  }

  // One group per pane. The first pane of each existing group keeps that group's id.
  let seq = frame.paneSeq;
  const keeps = new Map<PaneState, string>(
    areas.filter((a) => a.tabs.length > 0).map((a) => [a.tabs[0], a.id]),
  );
  const groups: AreaNode[] = panes.map((pane) => ({
    kind: 'area',
    id: keeps.get(pane) ?? areaId(seq++),
    tabs: [pane],
    activeTab: 0,
  }));

  const line = (orientation: SplitNode['orientation'], children: LayoutNode[]): LayoutNode =>
    children.length === 1
      ? children[0]
      : {
          kind: 'split',
          id: splitId(seq++),
          orientation,
          children,
          sizes: children.map(() => 1 / children.length),
        };

  let center: LayoutNode;
  if (style === 'columns') center = line('row', groups);
  else if (style === 'rows') center = line('column', groups);
  else {
    const perRow = Math.ceil(Math.sqrt(groups.length));
    const rows: LayoutNode[] = [];
    for (let i = 0; i < groups.length; i += perRow) {
      rows.push(line('row', groups.slice(i, i + perRow)));
    }
    center = line('column', rows);
  }

  if (sameShape(frame.center, center)) return frame;
  return sanitize({ ...frame, center, paneSeq: seq, maximizedAreaId: null });
}

/** Same tree up to ids: an arrangement asked for twice is a no-op the second time. */
function sameShape(a: LayoutNode, b: LayoutNode): boolean {
  if (a.kind === 'area' || b.kind === 'area') {
    return (
      a.kind === 'area' &&
      b.kind === 'area' &&
      a.tabs.length === b.tabs.length &&
      a.tabs.every((t, i) => t.instanceId === b.tabs[i].instanceId)
    );
  }
  return (
    a.orientation === b.orientation &&
    a.children.length === b.children.length &&
    a.sizes.every((s, i) => Math.abs(s - b.sizes[i]) < 1e-9) &&
    a.children.every((c, i) => sameShape(c, b.children[i]))
  );
}
