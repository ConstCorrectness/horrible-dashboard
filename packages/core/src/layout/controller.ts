/**
 * The frame controller: role-aware pane routing, region reveal/toggle, and the
 * `LayoutController` implementation the agent relay drives — all as dispatches
 * into the layout store, so user gestures, keybindings, and agent tools converge
 * on the same verbs and every mutation rides the store's autosave. Installed by
 * the Frame component on mount (`installFrameController`).
 */
import { hasAgentContext } from '../agent-context';
import {
  registry,
  type LayoutController,
  type OpenPaneOptions,
  type PanelDecl,
  type SplitDirection,
  type WidgetDecl,
} from '../registry';
import { isPaneDirty, runCloseGuard } from './close-guards';
import { setRegionCommandHandler } from './region-bus';
import {
  areaOfInstance,
  collectAreas,
  findArea,
  findPaneAnywhere,
  instanceId as makeInstanceId,
  listPanes,
  neighborAreaId,
} from './model';
import * as persistence from './persistence';
import { layoutStore } from './store';
import type {
  AreaNode,
  DockSide,
  LocatedPane,
  NavDirection,
  PaneRole,
  PaneState,
  RegionPosition,
  RegionState,
} from './types';

type ViewDecl = PanelDecl | WidgetDecl;

const DEFAULT_REGION_SIZE: Record<RegionPosition, number> = {
  left: 300,
  right: 300,
  bottom: 220,
};

/** A pane hosts either a panel or a widget — both resolve from the registry. */
export function resolveView(viewId: string): ViewDecl | undefined {
  return (
    registry.panels.find((p) => p.id === viewId) ?? registry.widgets.find((w) => w.id === viewId)
  );
}

/** A view's layout role; unannotated panels/widgets fall back per their kind. */
export function roleOf(viewId: string): PaneRole {
  const panel = registry.panels.find((p) => p.id === viewId);
  if (panel) return panel.role ?? 'document';
  const widget = registry.widgets.find((w) => w.id === viewId);
  return widget?.role ?? 'widget';
}

/**
 * The docks a view may be toggled into, preferred side first.
 *
 * Derived rather than stored so `role: 'tool'` keeps implying "dockable on
 * `defaultDock`" — every existing tool declaration stays correct untouched, and
 * only a view that wants a *second* home (a widget earning a rail glyph) has to
 * say `dockable`. An empty result means the view is center-only.
 */
export function dockSidesOf(viewId: string): DockSide[] {
  const decl = resolveView(viewId);
  if (!decl) return [];
  if (decl.dockable) return [decl.dockable].flat();
  return roleOf(viewId) === 'tool' ? [decl.defaultDock ?? 'left'] : [];
}

export function isDockable(viewId: string): boolean {
  return dockSidesOf(viewId).length > 0;
}

/**
 * Initial region-strip state for a view, from its `regions` declarations —
 * grouped by position, opened where any view declares `defaultOpen`.
 */
export function regionsFor(viewId: string): PaneState['regions'] | undefined {
  const decls = resolveView(viewId)?.regions;
  if (!decls?.length) return undefined;
  const out: Partial<Record<RegionPosition, RegionState>> = {};
  for (const position of ['left', 'right', 'bottom'] as const) {
    const here = decls.filter((d) => (d.position ?? 'right') === position);
    if (here.length === 0) continue;
    const openDecl = here.find((d) => d.defaultOpen);
    out[position] = {
      open: Boolean(openDecl),
      size: here.find((d) => d.defaultSize)?.defaultSize ?? DEFAULT_REGION_SIZE[position],
      collapsed: false,
      views: here.map((d) => d.id),
      activeView: (openDecl ?? here[0]).id,
    };
  }
  return Object.keys(out).length ? out : undefined;
}

/** The area's hosted role: that of its first tab, or null when empty. */
function areaRole(area: AreaNode): PaneRole | null {
  return area.tabs.length ? roleOf(area.tabs[0].viewId) : null;
}

function frame() {
  return layoutStore.getSnapshot().frame;
}

/** Bring an already-open pane forward wherever it lives. */
export function focusInstance(located: LocatedPane): void {
  const { location, pane } = located;
  if (location.kind === 'area') {
    const area = findArea(frame().center, location.areaId);
    const index = area?.tabs.findIndex((t) => t.instanceId === pane.instanceId) ?? -1;
    if (index >= 0) {
      layoutStore.dispatch({ type: 'SET_ACTIVE_TAB', areaId: location.areaId, index });
    }
    layoutStore.dispatch({ type: 'FOCUS_AREA', areaId: location.areaId });
  } else if (location.kind === 'dock') {
    layoutStore.dispatch({
      type: 'SET_ACTIVE_TOOL',
      side: location.dock,
      instanceId: pane.instanceId,
    });
  } else {
    layoutStore.dispatch({ type: 'BRING_FLOATING_FRONT', instanceId: pane.instanceId });
  }
}

/**
 * Open (or focus) a dockable view in a dock. Returns the instance id, or null
 * when the view isn't dockable at all, or not on the side asked for.
 */
export function openToolInDock(viewId: string, dock?: DockSide): string | null {
  const decl = resolveView(viewId);
  const sides = dockSidesOf(viewId);
  if (!decl || sides.length === 0) return null;
  if (dock && !sides.includes(dock)) return null;
  const existing = listPanes(frame()).find(
    (p) => p.pane.viewId === viewId && p.location.kind === 'dock',
  );
  if (existing) {
    focusInstance(existing);
    return existing.pane.instanceId;
  }
  const side = dock ?? sides[0];
  const pane: PaneState = {
    instanceId: makeInstanceId(viewId, frame().paneSeq),
    viewId,
    regions: regionsFor(viewId),
  };
  // Only a declared starting width is seeded here; without one the pane stays
  // `dockSize`-less and renders at the dock's own (last-used) size.
  if (decl.defaultDockSize !== undefined) pane.dockSize = decl.defaultDockSize;
  layoutStore.dispatch({ type: 'INSERT_TOOL', side, pane });
  return pane.instanceId;
}

export function toggleDock(side: DockSide, visible?: boolean): boolean {
  const dock = frame().docks[side];
  const next = visible ?? !dock.visible;
  if (next && dock.tools.length === 0) return false;
  layoutStore.dispatch({ type: 'SET_DOCK', side, patch: { visible: next } });
  return true;
}

/**
 * Close a pane, first running its close guard (if any) so a pane with unsaved
 * work can prompt and veto. The single close path — the UI close buttons and
 * `LayoutController.closePane` both go through here. Accepts an instanceId or a
 * (singleton) view id. Resolves true when the pane was actually removed.
 */
export async function closePaneGuarded(idOrInstance: string): Promise<boolean> {
  const located =
    findPaneAnywhere(frame(), idOrInstance) ??
    listPanes(frame()).find((p) => p.pane.viewId === idOrInstance) ??
    null;
  if (!located) return false;
  const instanceId = located.pane.instanceId;
  if (!(await runCloseGuard(instanceId))) return false;
  // Re-check existence: the guard's dialog is async and the pane could have gone.
  if (!findPaneAnywhere(frame(), instanceId)) return false;
  layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId });
  return true;
}

/** The area new documents/widgets land in: focused if compatible, else a match. */
function targetAreaFor(): string | null {
  const f = frame();
  const areas = collectAreas(f.center);
  const focused = areas.find((a) => a.id === f.focusedAreaId);
  const compatible = (a: AreaNode): boolean => {
    return a.tabs.length === 0;
  };
  if (focused && compatible(focused)) return focused.id;
  const match = areas.find(compatible);
  return match?.id ?? null;
}

/**
 * Role-routed open — the frame engine's `registry.openPanel` target. Documents
 * and widgets open in an empty area or split the active/focused area to take
 * their own space (no overlapping tabs). Tools go to their dock.
 * Returns the instance id, or null.
 */
export function openPane(viewId: string, opts?: OpenPaneOptions): string | null {
  const decl = resolveView(viewId);
  if (!decl) return null;
  const role = roleOf(viewId);
  if (role === 'tool') return openToolInDock(viewId);

  const f = frame();
  const isPanel = registry.panels.some((p) => p.id === viewId);
  const singleton = isPanel ? Boolean((decl as PanelDecl).singleton) : true;
  // A caller-supplied instance id is the identity (focus-or-create); a singleton
  // uses its view id as instance id, matching the legacy engine's scheme.
  const wantedId = opts?.instanceId ?? (singleton ? viewId : undefined);
  if (wantedId) {
    const existing = findPaneAnywhere(f, wantedId);
    if (existing) {
      focusInstance(existing);
      return wantedId;
    }
  }

  const pane: PaneState = {
    instanceId: wantedId ?? makeInstanceId(viewId, f.paneSeq),
    viewId,
    params: opts?.params,
    regions: regionsFor(viewId),
  };

  const target = targetAreaFor();
  if (target) {
    layoutStore.dispatch({ type: 'INSERT_PANE', areaId: target, pane });
    return pane.instanceId;
  }
  // No empty area: split the focused (or first) area and insert into
  // the new half — two dispatches so the id allocates from the advanced seq.
  const areaId = f.focusedAreaId ?? collectAreas(f.center)[0].id;
  const before = layoutStore.getSnapshot();
  const after = layoutStore.dispatch({ type: 'SPLIT_AREA', areaId, direction: 'right' });
  if (after === before) return null;
  const fresh: PaneState = {
    ...pane,
    instanceId: wantedId ?? makeInstanceId(viewId, after.frame.paneSeq),
  };
  layoutStore.dispatch({ type: 'INSERT_PANE', areaId: after.frame.focusedAreaId!, pane: fresh });
  return fresh.instanceId;
}

/**
 * Open a view in one specific center area, bypassing role routing. The explicit
 * counterpart to `openPane`: the caller has already chosen the destination (the
 * empty-area view picker, and later a drag dropped onto an area), so a `tool`
 * must land there rather than being routed off to its dock.
 * Returns the instance id, or null when the area or view is unknown.
 */
export function openPaneInArea(
  viewId: string,
  areaId: string,
  params?: Record<string, unknown>,
): string | null {
  const f = frame();
  if (!resolveView(viewId) || !findArea(f.center, areaId)) return null;
  // Same identity rule as openPane: a singleton focuses instead of duplicating.
  const isPanel = registry.panels.some((p) => p.id === viewId);
  const singleton = isPanel
    ? Boolean(registry.panels.find((p) => p.id === viewId)?.singleton)
    : true;
  if (singleton) {
    // By VIEW id, not instance id: a preset-seeded singleton carries a `#n`
    // suffix, so an instance-id lookup would miss it and open a duplicate.
    const existing =
      findPaneAnywhere(f, viewId) ?? listPanes(f).find((p) => p.pane.viewId === viewId);
    if (existing) {
      focusInstance(existing);
      return existing.pane.instanceId;
    }
  }
  const pane: PaneState = {
    instanceId: singleton ? viewId : makeInstanceId(viewId, f.paneSeq),
    viewId,
    params,
    regions: regionsFor(viewId),
  };
  layoutStore.dispatch({ type: 'INSERT_PANE', areaId, pane });
  return pane.instanceId;
}

/**
 * Re-point an open pane at a new instance id + params, in place — the "reuse this
 * pane" counterpart to `openPane`. A caller that knows the pane holds nothing
 * worth keeping (an empty editor buffer) retargets it rather than splitting off a
 * second pane, so the area, tab position, and region strips all survive. Returns
 * the new instance id, or null when the pane is gone or the id is already taken.
 */
export function retargetPane(
  instanceId: string,
  newInstanceId: string,
  params?: Record<string, unknown>,
): string | null {
  const f = frame();
  if (!findPaneAnywhere(f, instanceId) || findPaneAnywhere(f, newInstanceId)) return null;
  const after = layoutStore.dispatch({ type: 'RETARGET_PANE', instanceId, newInstanceId, params });
  return findPaneAnywhere(after.frame, newInstanceId) ? newInstanceId : null;
}

/**
 * Open one *thing* (a notebook, a repo, a URL) in a non-singleton document pane,
 * without accumulating panes. Three steps, in order:
 *
 *  1. That exact thing is already open (`instanceId` derived from its identity —
 *     a path, an id) → focus it.
 *  2. A pane of the same view is open and holds nothing worth keeping → retarget
 *     it in place, so the area, tab position and region strips survive.
 *  3. Otherwise open a fresh pane the usual (role-routed) way.
 *
 * "Nothing worth keeping" is a pane that isn't dirty (`setPaneDirty`) and that
 * the caller's `canReuse` accepts — the module decides, since only it knows what
 * its params mean. Omit `canReuse` to get identity-only behaviour (step 1 + 3).
 * Returns the live instance id, or null.
 */
export function openDocument(
  viewId: string,
  instanceId: string,
  params?: Record<string, unknown>,
  canReuse?: (pane: PaneState) => boolean,
): string | null {
  const existing = findPaneAnywhere(frame(), instanceId);
  if (existing) {
    focusInstance(existing);
    return instanceId;
  }
  if (canReuse) {
    const reusable = listPanes(frame()).find(
      (p) =>
        p.pane.viewId === viewId &&
        p.location.kind === 'area' &&
        !isPaneDirty(p.pane.instanceId) &&
        canReuse(p.pane),
    );
    if (reusable && retargetPane(reusable.pane.instanceId, instanceId, params)) {
      const moved = findPaneAnywhere(frame(), instanceId);
      if (moved) focusInstance(moved);
      return instanceId;
    }
  }
  return openPane(viewId, { instanceId, params });
}

// ---------------------------------------------------------------------------
// Regions
// ---------------------------------------------------------------------------

/** Current-or-declared state of one region strip on a pane instance. */
function regionOf(pane: PaneState, position: RegionPosition): RegionState | null {
  return pane.regions?.[position] ?? regionsFor(pane.viewId)?.[position] ?? null;
}

export function toggleRegion(
  instanceId: string,
  position: RegionPosition,
  open?: boolean,
): boolean {
  const located = findPaneAnywhere(frame(), instanceId);
  if (!located) return false;
  const region = regionOf(located.pane, position);
  if (!region) return false;
  const next = open ?? !(region.open && !region.collapsed);
  layoutStore.dispatch({
    type: 'SET_REGION',
    instanceId,
    position,
    region: { ...region, open: next, collapsed: false },
  });
  return true;
}

export function collapseRegion(instanceId: string, position: RegionPosition): boolean {
  const located = findPaneAnywhere(frame(), instanceId);
  if (!located) return false;
  const region = located.pane.regions?.[position];
  if (!region?.open) return false;
  layoutStore.dispatch({
    type: 'SET_REGION',
    instanceId,
    position,
    region: { ...region, collapsed: !region.collapsed },
  });
  return true;
}

/** Open `viewId`'s strip on a pane instance and make it the active region view. */
export function setRegionView(instanceId: string, viewId: string): boolean {
  const located = findPaneAnywhere(frame(), instanceId);
  if (!located) return false;
  const declared = resolveView(located.pane.viewId)?.regions?.find((r) => r.id === viewId);
  if (!declared) return false;
  const position = declared.position ?? 'right';
  const region = regionOf(located.pane, position);
  if (!region || !region.views.includes(viewId)) return false;
  layoutStore.dispatch({
    type: 'SET_REGION',
    instanceId,
    position,
    region: { ...region, open: true, collapsed: false, activeView: viewId },
  });
  return true;
}

/** The view that hosts `regionViewId` in its declared regions, if any. */
export function regionHostOf(regionViewId: string): ViewDecl | undefined {
  return [...registry.panels, ...registry.widgets].find((v) =>
    v.regions?.some((r) => r.id === regionViewId),
  );
}

/** The host pane instance region commands should act on: the focused area's
 * active tab when it matches, else any open instance of the host view. */
function hostInstanceOf(hostViewId: string): LocatedPane | null {
  const f = frame();
  if (f.focusedAreaId) {
    const area = findArea(f.center, f.focusedAreaId);
    const active = area?.tabs[area.activeTab];
    if (active?.viewId === hostViewId) {
      return { pane: active, location: { kind: 'area', areaId: area!.id } };
    }
  }
  return listPanes(f).find((p) => p.pane.viewId === hostViewId) ?? null;
}

/**
 * Reveal a region view inside its host pane (opening the host first if needed) —
 * the successor of `registry.revealCompanion`, e.g. the Game Board popping when
 * a match starts. Falls back to opening the view standalone if nothing hosts it.
 */
export function revealRegionView(regionViewId: string): void {
  const host = regionHostOf(regionViewId);
  if (!host) {
    openPane(regionViewId);
    return;
  }
  let instance = hostInstanceOf(host.id);
  if (!instance) {
    const id = openPane(host.id);
    instance = id ? findPaneAnywhere(frame(), id) : null;
  }
  if (!instance) return;
  focusInstance(instance);
  setRegionView(instance.pane.instanceId, regionViewId);
}

/**
 * Keyboard pick: reveal the region view, or close its strip when it is already
 * the open+active one (press again to dismiss — matches the old companions).
 */
export function toggleRegionView(regionViewId: string): void {
  const host = regionHostOf(regionViewId);
  if (!host) return;
  const instance = hostInstanceOf(host.id);
  if (instance) {
    const declared = host.regions!.find((r) => r.id === regionViewId)!;
    const position = declared.position ?? 'right';
    const region = instance.pane.regions?.[position];
    if (region?.open && !region.collapsed && region.activeView === regionViewId) {
      toggleRegion(instance.pane.instanceId, position, false);
      return;
    }
  }
  revealRegionView(regionViewId);
}

// ---------------------------------------------------------------------------
// Areas
// ---------------------------------------------------------------------------

/** The area containing a pane instance, or the area itself when given its id. */
function areaIdFor(areaOrInstanceId: string): string | null {
  const f = frame();
  if (findArea(f.center, areaOrInstanceId)) return areaOrInstanceId;
  return areaOfInstance(f.center, areaOrInstanceId)?.id ?? null;
}

/**
 * Split the area holding `areaOrInstanceId`; the new area gets `viewId` (or a
 * duplicate of the source's active view). Returns the new pane's instance id.
 */
export function splitAreaBy(
  areaOrInstanceId: string,
  direction: SplitDirection,
  viewId?: string,
): string | null {
  const f = frame();
  const areaId = areaIdFor(areaOrInstanceId);
  if (!areaId) return null;
  const area = findArea(f.center, areaId)!;
  const sourceView = viewId ?? area.tabs[area.activeTab]?.viewId;
  const decl = sourceView ? resolveView(sourceView) : undefined;
  const paneView = decl ? sourceView! : undefined;
  // Two steps (split, then insert from the fresh snapshot) so the pane's
  // instance id allocates from a seq the split has already advanced past.
  const before = layoutStore.getSnapshot();
  const after = layoutStore.dispatch({ type: 'SPLIT_AREA', areaId, direction });
  if (after === before) return null;
  const newAreaId = after.frame.focusedAreaId;
  if (!paneView || !newAreaId) return newAreaId;
  const pane: PaneState = {
    instanceId: makeInstanceId(paneView, after.frame.paneSeq),
    viewId: paneView,
    regions: regionsFor(paneView),
  };
  layoutStore.dispatch({ type: 'INSERT_PANE', areaId: newAreaId, pane });
  return pane.instanceId;
}

export function joinAreaDirection(areaOrInstanceId: string, direction: NavDirection): boolean {
  const areaId = areaIdFor(areaOrInstanceId);
  if (!areaId) return false;
  const f = frame();
  const area = findArea(f.center, areaId)!;
  const neighborId = neighborAreaId(f.center, areaId, direction);
  const neighbor = neighborId ? findArea(f.center, neighborId) : null;
  // Adopt the neighbor's tabs only when both sides hold documents.
  const adoptTabs = Boolean(
    neighbor &&
    areaRole(area) !== 'widget' &&
    neighbor.tabs.length > 0 &&
    areaRole(neighbor) === 'document' &&
    (areaRole(area) === 'document' || area.tabs.length === 0),
  );
  const before = layoutStore.getSnapshot();
  return layoutStore.dispatch({ type: 'JOIN_AREA', areaId, direction, adoptTabs }) !== before;
}

export function focusAreaDirection(direction: NavDirection): boolean {
  const f = frame();
  if (!f.focusedAreaId) return false;
  const neighbor = neighborAreaId(f.center, f.focusedAreaId, direction);
  if (!neighbor) return false;
  layoutStore.dispatch({ type: 'FOCUS_AREA', areaId: neighbor });
  return true;
}

/** Move a center pane into a specific area, or toward a viewport direction. */
export function movePaneTo(
  instanceId: string,
  target: { areaId?: string; direction?: NavDirection },
): boolean {
  const f = frame();
  const sourceArea = areaOfInstance(f.center, instanceId);
  const pane = sourceArea?.tabs.find((t) => t.instanceId === instanceId);
  if (!sourceArea || !pane) return false;
  const targetId = target.areaId
    ? (findArea(f.center, target.areaId)?.id ?? null)
    : target.direction
      ? neighborAreaId(f.center, sourceArea.id, target.direction)
      : null;
  if (!targetId) return false;
  const neighbor = findArea(f.center, targetId)!;
  // Areas are single-occupancy: forbidden to move a pane into an already occupied area.
  if (neighbor.tabs.length > 0) {
    return false;
  }
  const before = layoutStore.getSnapshot();
  return layoutStore.dispatch({ type: 'MOVE_PANE', instanceId, targetAreaId: targetId }) !== before;
}

/** Move the focused area's active pane to the neighboring area in `direction`. */
export function movePaneDirection(direction: NavDirection): boolean {
  const f = frame();
  const area = f.focusedAreaId ? findArea(f.center, f.focusedAreaId) : null;
  const pane = area?.tabs[area.activeTab];
  if (!pane) return false;
  return movePaneTo(pane.instanceId, { direction });
}

export function fullscreenArea(areaOrInstanceId: string | null, on?: boolean): boolean {
  const f = frame();
  if (areaOrInstanceId === null) {
    layoutStore.dispatch({ type: 'SET_FULLSCREEN', areaId: null });
    return true;
  }
  const areaId = areaIdFor(areaOrInstanceId);
  if (!areaId) return false;
  const enable = on ?? f.fullscreenAreaId !== areaId;
  layoutStore.dispatch({ type: 'SET_FULLSCREEN', areaId: enable ? areaId : null });
  return true;
}

/** Toggle fullscreen on the focused area (the `ctrl+space` command). */
export function fullscreenFocusedArea(): void {
  const f = frame();
  if (f.fullscreenAreaId) fullscreenArea(null);
  else if (f.focusedAreaId) fullscreenArea(f.focusedAreaId, true);
}

// ---------------------------------------------------------------------------
// Reads for the agent (get_layout / list_open_panes)
// ---------------------------------------------------------------------------

function describePane(pane: PaneState): Record<string, unknown> {
  const out: Record<string, unknown> = {
    instanceId: pane.instanceId,
    viewId: pane.viewId,
    title: resolveView(pane.viewId)?.title ?? pane.viewId,
  };
  if (pane.regions) {
    out.regions = Object.fromEntries(
      Object.entries(pane.regions).map(([position, r]) => [
        position,
        { open: r.open, collapsed: r.collapsed, activeView: r.activeView, views: r.views },
      ]),
    );
  }
  return out;
}

/** A compact JSON description of the whole frame, for the agent's `get_layout`. */
export function describeLayout(): Record<string, unknown> {
  const snap = layoutStore.getSnapshot();
  const f = snap.frame;
  const describeNode = (node: typeof f.center): Record<string, unknown> =>
    node.kind === 'area'
      ? {
          area: node.id,
          focused: node.id === f.focusedAreaId,
          activeTab: node.activeTab,
          tabs: node.tabs.map(describePane),
        }
      : {
          split: node.orientation,
          sizes: node.sizes.map((s) => Number(s.toFixed(3))),
          children: node.children.map(describeNode),
        };
  return {
    workspaceId: snap.workspaceId,
    fullscreenAreaId: f.fullscreenAreaId,
    center: describeNode(f.center),
    docks: Object.fromEntries(
      (['left', 'right', 'bottom'] as const).map((side) => {
        const dock = f.docks[side];
        return [
          side,
          {
            visible: dock.visible,
            size: dock.size,
            activeTool: dock.activeTool,
            tools: dock.tools.map(describePane),
          },
        ];
      }),
    ),
    floating: f.floating.map((fl) => ({ ...describePane(fl.pane), rect: fl.rect })),
  };
}

/** `listOpenPanes` plus where each pane lives — the agent's orientation read. */
export function listOpenPanesDetailed(): Array<Record<string, unknown>> {
  return listPanes(frame()).map(({ pane, location }) => ({
    id: pane.viewId,
    instanceId: pane.instanceId,
    title: resolveView(pane.viewId)?.title ?? pane.viewId,
    role: roleOf(pane.viewId),
    hasContext: hasAgentContext(pane.instanceId),
    location,
  }));
}

// ---------------------------------------------------------------------------
// Center measurement (px → unit fractions for resize verbs)
// ---------------------------------------------------------------------------

let centerMeasurer: (() => { width: number; height: number } | null) | null = null;

/** The Frame registers how big the center grid currently is, in px. */
export function setCenterMeasurer(fn: typeof centerMeasurer): void {
  centerMeasurer = fn;
}

export function resizeAreaPx(
  areaOrInstanceId: string,
  size: { width?: number; height?: number },
): boolean {
  const areaId = areaIdFor(areaOrInstanceId);
  const px = centerMeasurer?.();
  if (!areaId || !px || px.width <= 0 || px.height <= 0) return false;
  const target: { w?: number; h?: number } = {};
  if (size.width !== undefined) target.w = size.width / px.width;
  if (size.height !== undefined) target.h = size.height / px.height;
  if (target.w === undefined && target.h === undefined) return false;
  const before = layoutStore.getSnapshot();
  return layoutStore.dispatch({ type: 'RESIZE_AREA', areaId, target }) !== before;
}

// ---------------------------------------------------------------------------
// LayoutController installation (the registry seam the agent relay drives)
// ---------------------------------------------------------------------------

/** Implements today's LayoutController verbs against the frame store, so the
 * agent's existing tools keep working while the engines coexist. Installed by
 * the Frame on mount; also wires the region command bus and the panel opener
 * target used by `registry.openPanel`. */
export function installFrameController(): void {
  setRegionCommandHandler({
    togglePosition: (hostViewId, position) => {
      const instance = hostInstanceOf(hostViewId);
      if (instance) toggleRegion(instance.pane.instanceId, position);
    },
    pickView: (regionViewId) => toggleRegionView(regionViewId),
  });

  const controller: LayoutController = {
    closePane: (id) => {
      const f = frame();
      const located =
        findPaneAnywhere(f, id) ?? listPanes(f).find((p) => p.pane.viewId === id) ?? null;
      if (!located) return false;
      // Kick off the guarded close (may await a save dialog); the boolean here
      // reports that the pane existed and a close was initiated.
      void closePaneGuarded(located.pane.instanceId);
      return true;
    },
    focusPane: (instanceId) => {
      const located = findPaneAnywhere(frame(), instanceId);
      if (!located) return false;
      focusInstance(located);
      return true;
    },
    listOpenPanes: () =>
      listPanes(frame()).map(({ pane }) => ({
        id: pane.viewId,
        instanceId: pane.instanceId,
        title: resolveView(pane.viewId)?.title ?? pane.viewId,
        hasContext: hasAgentContext(pane.instanceId),
      })),
    createWorkspace: (name) => persistence.createNamedWorkspace(name),
    listWorkspaces: () => persistence.listWorkspaces(),
    resetLayout: () => void persistence.resetLayout(),
    deleteActiveWorkspace: () => void persistence.deleteActiveWorkspace(),
    renameWorkspace: (id, name) => persistence.renameWorkspace(id, name),
    deleteWorkspace: (id) => persistence.removeWorkspace(id),
    setPaneFloating: (instanceId, floating) => {
      const located = findPaneAnywhere(frame(), instanceId);
      if (!located) return false;
      const isFloating = located.location.kind === 'floating';
      if (floating === isFloating) return false;
      const before = layoutStore.getSnapshot();
      return (
        layoutStore.dispatch(
          floating ? { type: 'FLOAT_PANE', instanceId } : { type: 'DOCK_FLOATING', instanceId },
        ) !== before
      );
    },
    changePaneType: (instanceId, viewId) => {
      const decl = resolveView(viewId);
      const located = findPaneAnywhere(frame(), instanceId);
      if (!decl || !located) return false;
      // A dock only accepts views that opted into docking; the center accepts
      // anything (a tool dragged out of a dock has to be able to live there).
      if (located.location.kind === 'dock' && !isDockable(viewId)) return false;
      layoutStore.dispatch({
        type: 'SET_PANE_VIEW',
        instanceId,
        viewId,
        regions: regionsFor(viewId),
      });
      return true;
    },
  };
  registry.setLayoutController(controller);
}
