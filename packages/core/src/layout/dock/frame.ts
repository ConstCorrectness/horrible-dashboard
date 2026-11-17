/**
 * Frame-level verbs for the docked workbench: lookups and edits that address a
 * pane or a group wherever it lives (centre, an edge dock, a popped-out window).
 *
 * Pure and registry-free. Role routing — which target a view should open at — is
 * the controller's job; these take a `PlaceTarget` and do exactly that.
 */
import { areaId, createArea, reorderTab, setActiveTab } from '../model';
import { DEFAULT_BACKDROP } from '../types';
import type { AreaNode, PaneState } from '../types';
import {
  areaOfInstance,
  collectAreas,
  findArea,
  firstArea,
  removePane as removePaneFromTree,
  replaceNode,
  setSplitSizes as setTreeSplitSizes,
  splitArea,
  withTab,
  withoutTab,
} from './tree';
import { DOCK_SIDES } from './types';
import type {
  AreaHost,
  DockFrame,
  DockLocation,
  DockSide,
  DockState,
  PlaceTarget,
  PopoutBounds,
  PopoutState,
} from './types';

export const DEFAULT_DOCK_SIZES: Record<DockSide, number> = {
  left: 280,
  right: 320,
  bottom: 240,
};

/** A dock narrower than this cannot show a tab strip and a usable body. */
export const MIN_DOCK_SIZE = 160;

export function popoutId(seq: number): string {
  return `p${seq}`;
}

export interface LocatedDockPane {
  pane: PaneState;
  location: DockLocation;
}

/**
 * An empty workbench: one empty centre group and three hidden docks.
 *
 * Ids 0–3 are spent on the four groups, so `paneSeq` starts at 4.
 */
export function createEmptyDockFrame(): DockFrame {
  const dock = (side: DockSide, id: string): DockState => ({
    visible: false,
    size: DEFAULT_DOCK_SIZES[side],
    area: createArea(id),
  });
  return {
    center: createArea(areaId(0)),
    docks: {
      left: dock('left', areaId(1)),
      right: dock('right', areaId(2)),
      bottom: dock('bottom', areaId(3)),
    },
    popouts: [],
    backdrop: { id: DEFAULT_BACKDROP },
    maximizedAreaId: null,
    presentedInstanceId: null,
    focusedInstanceId: null,
    focusedAreaId: areaId(0),
    paneSeq: 4,
  };
}

// ---------------------------------------------------------------------------
// Lookups
// ---------------------------------------------------------------------------

/** Every group with what hosts it: centre first, then docks, then popouts. */
export function listAreas(frame: DockFrame): { area: AreaNode; host: AreaHost }[] {
  const out: { area: AreaNode; host: AreaHost }[] = [];
  for (const area of collectAreas(frame.center)) out.push({ area, host: { kind: 'center' } });
  for (const side of DOCK_SIDES) {
    out.push({ area: frame.docks[side].area, host: { kind: 'dock', side } });
  }
  for (const p of frame.popouts) {
    out.push({ area: p.area, host: { kind: 'popout', popoutId: p.id } });
  }
  return out;
}

export function findAreaAnywhere(
  frame: DockFrame,
  id: string,
): { area: AreaNode; host: AreaHost } | null {
  return listAreas(frame).find((entry) => entry.area.id === id) ?? null;
}

export function findPane(frame: DockFrame, instanceId: string): LocatedDockPane | null {
  for (const { area, host } of listAreas(frame)) {
    const pane = area.tabs.find((t) => t.instanceId === instanceId);
    if (pane) return { pane, location: { ...host, areaId: area.id } };
  }
  return null;
}

export function listPanes(frame: DockFrame): LocatedDockPane[] {
  return listAreas(frame).flatMap(({ area, host }) =>
    area.tabs.map((pane) => ({ pane, location: { ...host, areaId: area.id } })),
  );
}

/**
 * The panes the user can see: each group's active tab, where the group is on
 * screen. A hidden dock shows nothing, and a maximized group hides every other
 * group in the main window. Popouts are their own windows and are always showing.
 */
export function visiblePanes(frame: DockFrame): LocatedDockPane[] {
  return listAreas(frame).flatMap(({ area, host }) => {
    const pane = area.tabs[area.activeTab];
    if (!pane) return [];
    if (host.kind === 'dock' && !frame.docks[host.side].visible) return [];
    if (host.kind !== 'popout' && frame.maximizedAreaId && frame.maximizedAreaId !== area.id) {
      return [];
    }
    return [{ pane, location: { ...host, areaId: area.id } }];
  });
}

/**
 * Whether a pane is on screen, by the frame alone: it is its group's showing tab,
 * the group is not behind a maximized one or in a hidden dock, and no other pane
 * is presented over everything.
 *
 * The workbench hides covered panes with `visibility`, which no DOM observer
 * reports (an `IntersectionObserver` still sees a hidden element intersecting).
 * A surface composited above the page, like the native browser view, needs this
 * answer from the layout instead.
 */
export function isPaneShowing(frame: DockFrame, instanceId: string): boolean {
  if (frame.presentedInstanceId) return frame.presentedInstanceId === instanceId;
  return visiblePanes(frame).some((p) => p.pane.instanceId === instanceId);
}

// ---------------------------------------------------------------------------
// Edits
// ---------------------------------------------------------------------------

/** Apply `fn` to a group wherever it lives. Null if unknown. */
export function updateAreaAnywhere(
  frame: DockFrame,
  id: string,
  fn: (area: AreaNode) => AreaNode,
): DockFrame | null {
  const found = findAreaAnywhere(frame, id);
  if (!found) return null;
  const { host } = found;
  if (host.kind === 'center') {
    const center = replaceNode(frame.center, id, (node) => fn(node as AreaNode));
    return center ? { ...frame, center } : null;
  }
  if (host.kind === 'dock') {
    const dock = frame.docks[host.side];
    return { ...frame, docks: { ...frame.docks, [host.side]: { ...dock, area: fn(dock.area) } } };
  }
  return {
    ...frame,
    popouts: frame.popouts.map((p) => (p.id === host.popoutId ? { ...p, area: fn(p.area) } : p)),
  };
}

/** Apply `fn` to a pane instance wherever it lives. Null if unknown. */
export function updatePane(
  frame: DockFrame,
  instanceId: string,
  fn: (pane: PaneState) => PaneState,
): DockFrame | null {
  const located = findPane(frame, instanceId);
  if (!located) return null;
  return updateAreaAnywhere(frame, located.location.areaId, (area) => ({
    ...area,
    tabs: area.tabs.map((t) => (t.instanceId === instanceId ? fn(t) : t)),
  }));
}

/**
 * Repair the references that an edit can leave dangling.
 *
 * Focus falls back along what the user would expect to be looking at next: the
 * tab that took the closed one's place, then any centre tab, then nothing. Run
 * after every structural edit rather than trusted to each one.
 */
export function sanitize(frame: DockFrame): DockFrame {
  const areas = listAreas(frame);
  const hasArea = (id: string | null) => !!id && areas.some((a) => a.area.id === id);
  const hasPane = (id: string | null) => !!id && findPane(frame, id) !== null;

  const maximizedHost = frame.maximizedAreaId
    ? findAreaAnywhere(frame, frame.maximizedAreaId)
    : null;
  // A popout is already its own window, and an emptied group has nothing to show.
  const maximizedAreaId =
    maximizedHost && maximizedHost.host.kind !== 'popout' && maximizedHost.area.tabs.length > 0
      ? frame.maximizedAreaId
      : null;

  let focusedInstanceId = hasPane(frame.focusedInstanceId) ? frame.focusedInstanceId : null;
  let focusedAreaId = hasArea(frame.focusedAreaId) ? frame.focusedAreaId : null;

  if (focusedInstanceId) {
    focusedAreaId = findPane(frame, focusedInstanceId)!.location.areaId;
  } else {
    const home = focusedAreaId ? areas.find((a) => a.area.id === focusedAreaId)!.area : null;
    const fallback =
      home?.tabs[home.activeTab] ??
      collectAreas(frame.center)
        .map((a) => a.tabs[a.activeTab])
        .find(Boolean) ??
      null;
    focusedInstanceId = fallback?.instanceId ?? null;
    focusedAreaId = fallback
      ? findPane(frame, fallback.instanceId)!.location.areaId
      : (focusedAreaId ?? firstArea(frame.center).id);
  }

  const presentedInstanceId = hasPane(frame.presentedInstanceId) ? frame.presentedInstanceId : null;

  if (
    maximizedAreaId === frame.maximizedAreaId &&
    focusedInstanceId === frame.focusedInstanceId &&
    focusedAreaId === frame.focusedAreaId &&
    presentedInstanceId === frame.presentedInstanceId
  ) {
    return frame;
  }
  return { ...frame, maximizedAreaId, focusedInstanceId, focusedAreaId, presentedInstanceId };
}

/**
 * Remove a pane instance from wherever it lives.
 *
 * An emptied centre group is removed (unless it is the last), an emptied dock
 * hides, and an emptied popout closes: none of them leaves a frame around nothing.
 */
export function removePane(
  frame: DockFrame,
  instanceId: string,
): { frame: DockFrame; removed: PaneState } | null {
  const located = findPane(frame, instanceId);
  if (!located) return null;
  const { location, pane } = located;

  if (location.kind === 'center') {
    const res = removePaneFromTree(frame.center, instanceId, frame.paneSeq);
    if (!res) return null;
    return {
      frame: sanitize({ ...frame, center: res.root, paneSeq: res.seq }),
      removed: res.removed,
    };
  }
  if (location.kind === 'dock') {
    const dock = frame.docks[location.side];
    const area = withoutTab(dock.area, instanceId);
    return {
      frame: sanitize({
        ...frame,
        docks: {
          ...frame.docks,
          [location.side]: { ...dock, area, visible: area.tabs.length > 0 && dock.visible },
        },
      }),
      removed: pane,
    };
  }
  const popouts = frame.popouts.flatMap((p): PopoutState[] => {
    if (p.id !== location.popoutId) return [p];
    const area = withoutTab(p.area, instanceId);
    return area.tabs.length > 0 ? [{ ...p, area }] : [];
  });
  return { frame: sanitize({ ...frame, popouts }), removed: pane };
}

/** The centre group new documents open in: the focused one if it is in the centre. */
export function targetCenterArea(frame: DockFrame): AreaNode {
  return (
    (frame.focusedAreaId ? findArea(frame.center, frame.focusedAreaId) : null) ??
    firstArea(frame.center)
  );
}

/**
 * Put a pane at `target`. Returns null when the target does not exist; a `split`
 * of a group that is not in the centre falls back to the centre, because docks
 * and popouts hold one group each and have nothing to split.
 */
export function insertPane(
  frame: DockFrame,
  pane: PaneState,
  target: PlaceTarget,
  opts: { activate?: boolean; focus?: boolean } = {},
): DockFrame | null {
  const focus = opts.focus !== false && opts.activate !== false;
  const landed = (next: DockFrame | null, landedAreaId: string): DockFrame | null => {
    if (!next) return null;
    return sanitize(
      focus ? { ...next, focusedInstanceId: pane.instanceId, focusedAreaId: landedAreaId } : next,
    );
  };

  switch (target.kind) {
    case 'center':
      return insertPane(frame, pane, { kind: 'area', areaId: targetCenterArea(frame).id }, opts);

    case 'dock':
      return insertPane(
        frame,
        pane,
        { kind: 'area', areaId: frame.docks[target.side].area.id },
        opts,
      );

    case 'area': {
      const found = findAreaAnywhere(frame, target.areaId);
      if (!found) return null;
      let next = updateAreaAnywhere(frame, target.areaId, (area) =>
        withTab(area, pane, { index: target.index, activate: opts.activate }),
      );
      // Opening into a hidden dock shows it: a pane that opened where the user
      // cannot see it reads as a button that did nothing.
      if (next && found.host.kind === 'dock' && opts.activate !== false) {
        const side = found.host.side;
        next = {
          ...next,
          docks: { ...next.docks, [side]: { ...next.docks[side], visible: true } },
        };
      }
      return landed(next, target.areaId);
    }

    case 'split': {
      if (!findArea(frame.center, target.areaId)) {
        return insertPane(frame, pane, { kind: 'center' }, opts);
      }
      const res = splitArea(frame.center, target.areaId, target.side, frame.paneSeq);
      if (!res) return null;
      const center = replaceNode(res.root, res.newAreaId, (node) =>
        withTab(node as AreaNode, pane),
      );
      return center ? landed({ ...frame, center, paneSeq: res.seq }, res.newAreaId) : null;
    }

    case 'popout': {
      const id = popoutId(frame.paneSeq);
      const area = withTab(createArea(areaId(frame.paneSeq + 1)), pane);
      const popout: PopoutState = { id, area, bounds: target.bounds ?? null, maximized: false };
      return landed(
        { ...frame, popouts: [...frame.popouts, popout], paneSeq: frame.paneSeq + 2 },
        area.id,
      );
    }
  }
}

/**
 * Move an open pane to `target`. Returns the same frame when there is nothing to
 * do, and null when the pane or the target is unknown.
 */
export function movePane(
  frame: DockFrame,
  instanceId: string,
  target: PlaceTarget,
): DockFrame | null {
  const located = findPane(frame, instanceId);
  if (!located) return null;
  const home = findAreaAnywhere(frame, located.location.areaId)!.area;

  const resolved: PlaceTarget =
    target.kind === 'center'
      ? { kind: 'area', areaId: targetCenterArea(frame).id }
      : target.kind === 'dock'
        ? { kind: 'area', areaId: frame.docks[target.side].area.id }
        : target;

  if (resolved.kind === 'area' && resolved.areaId === home.id) {
    // Within its own group a move is a reorder, or nothing.
    if (resolved.index === undefined) return frame;
    const from = home.tabs.findIndex((t) => t.instanceId === instanceId);
    const to = Math.min(Math.max(resolved.index, 0), home.tabs.length - 1);
    if (from === to) return frame;
    return updateAreaAnywhere(frame, home.id, (area) => reorderTab(area, from, to) ?? area);
  }
  // Splitting a group to hold its only tab would remove the group and leave the
  // pane exactly where it was, in a group with a new id.
  if (resolved.kind === 'split' && resolved.areaId === home.id && home.tabs.length === 1) {
    return frame;
  }
  // The same for popping out a pane that is already alone in a popout.
  if (resolved.kind === 'popout' && located.location.kind === 'popout' && home.tabs.length === 1) {
    return frame;
  }

  const removed = removePane(frame, instanceId);
  if (!removed) return null;
  return insertPane(removed.frame, removed.removed, resolved);
}

/** Focus a pane: activate its tab, show its dock, and clear its attention flag. */
export function focusPane(frame: DockFrame, instanceId: string): DockFrame | null {
  const located = findPane(frame, instanceId);
  if (!located) return null;
  const { location } = located;
  let next = updateAreaAnywhere(frame, location.areaId, (area) => {
    const index = area.tabs.findIndex((t) => t.instanceId === instanceId);
    const tabs = area.tabs.map((t) =>
      t.instanceId === instanceId && t.attention ? { ...t, attention: undefined } : t,
    );
    return { ...(setActiveTab({ ...area, tabs }, index) ?? area), tabs };
  });
  if (!next) return null;
  if (location.kind === 'dock' && !next.docks[location.side].visible) {
    const dock = next.docks[location.side];
    next = { ...next, docks: { ...next.docks, [location.side]: { ...dock, visible: true } } };
  }
  return sanitize({ ...next, focusedInstanceId: instanceId, focusedAreaId: location.areaId });
}

/** Focus a group, including an empty one. */
export function focusArea(frame: DockFrame, id: string): DockFrame | null {
  const found = findAreaAnywhere(frame, id);
  if (!found) return null;
  const active = found.area.tabs[found.area.activeTab];
  if (active) return focusPane(frame, active.instanceId);
  return { ...frame, focusedAreaId: id, focusedInstanceId: null };
}

/**
 * Show or hide a dock. An empty dock cannot be shown: there is nothing to draw,
 * and a blank strip the user did not ask for is the docked equivalent of a window
 * with no content. Hiding a dock that holds focus hands focus back to the centre.
 */
export function setDockVisible(frame: DockFrame, side: DockSide, visible?: boolean): DockFrame {
  const dock = frame.docks[side];
  const want = (visible ?? !dock.visible) && dock.area.tabs.length > 0;
  if (want === dock.visible) return frame;
  const next: DockFrame = {
    ...frame,
    docks: { ...frame.docks, [side]: { ...dock, visible: want } },
  };
  if (!want && frame.focusedAreaId === dock.area.id) {
    const home = targetCenterArea({ ...next, focusedAreaId: null });
    return sanitize({
      ...next,
      focusedAreaId: home.id,
      focusedInstanceId: home.tabs[home.activeTab]?.instanceId ?? null,
      maximizedAreaId: next.maximizedAreaId === dock.area.id ? null : next.maximizedAreaId,
    });
  }
  return sanitize({
    ...next,
    maximizedAreaId: !want && next.maximizedAreaId === dock.area.id ? null : next.maximizedAreaId,
  });
}

export function setDockSize(frame: DockFrame, side: DockSide, size: number): DockFrame {
  const dock = frame.docks[side];
  const next = Math.max(MIN_DOCK_SIZE, Math.round(size));
  if (!Number.isFinite(next) || next === dock.size) return frame;
  return { ...frame, docks: { ...frame.docks, [side]: { ...dock, size: next } } };
}

export function setSplitSizes(frame: DockFrame, splitNodeId: string, sizes: number[]): DockFrame {
  const center = setTreeSplitSizes(frame.center, splitNodeId, sizes);
  return center ? { ...frame, center } : frame;
}

/** Maximize a group over the workbench, or restore it. Popouts are not eligible. */
export function toggleMaximized(frame: DockFrame, id: string): DockFrame {
  if (frame.maximizedAreaId === id) return { ...frame, maximizedAreaId: null };
  const found = findAreaAnywhere(frame, id);
  if (!found || found.host.kind === 'popout' || found.area.tabs.length === 0) return frame;
  return { ...frame, maximizedAreaId: id };
}

export function setPopoutBounds(
  frame: DockFrame,
  id: string,
  patch: { bounds?: PopoutBounds; displayId?: number; maximized?: boolean },
): DockFrame {
  if (!frame.popouts.some((p) => p.id === id)) return frame;
  return {
    ...frame,
    popouts: frame.popouts.map((p) => (p.id === id ? { ...p, ...patch } : p)),
  };
}

/**
 * Close a popped-out window by docking its panes back, never by closing them:
 * the OS close button on a popout means "put this back", and a pane with a live
 * terminal or an unsaved buffer must survive it.
 *
 * `placeOf` is the controller's role routing, so each pane returns to where a
 * pane of its role belongs. The popout's active tab ends up focused.
 */
export function dockPopout(
  frame: DockFrame,
  id: string,
  placeOf: (pane: PaneState) => PlaceTarget,
): DockFrame {
  const popout = frame.popouts.find((p) => p.id === id);
  if (!popout) return frame;
  const active = popout.area.tabs[popout.area.activeTab];
  let next: DockFrame = sanitize({ ...frame, popouts: frame.popouts.filter((p) => p.id !== id) });
  for (const pane of popout.area.tabs) {
    const target = placeOf(pane);
    next =
      insertPane(next, pane, target.kind === 'popout' ? { kind: 'center' } : target, {
        activate: pane === active,
      }) ?? next;
  }
  return active ? (focusPane(next, active.instanceId) ?? next) : next;
}

/** The group holding `instanceId` in the centre tree only, or null. */
export function centerAreaOf(frame: DockFrame, instanceId: string): AreaNode | null {
  return areaOfInstance(frame.center, instanceId);
}
