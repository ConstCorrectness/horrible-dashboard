/**
 * The frame controller: role-aware pane routing, region reveal/toggle, and the
 * `LayoutController` implementation the agent relay drives — all as dispatches
 * into the layout store, so user gestures, keybindings, and agent tools converge
 * on the same verbs and every mutation rides the store's autosave. Installed by
 * the Frame component on mount (`installFrameController`).
 */
import { hasAgentContext, readAgentContext } from '../agent-context';
import {
  registry,
  type LayoutController,
  type OpenPaneOptions,
  type PanelDecl,
  type SectionDecl,
  type SplitDirection,
  type WidgetDecl,
} from '../registry';
import { isPaneDirty, runCloseGuard } from './close-guards';
import { getRailPrefs } from './rail-prefs';
import { setFrameCommandHandler } from './frame-bus';
import { resolveShowTarget, type ShowCandidates, type ShowTarget } from './show';
import {
  areaOfInstance,
  collectAreas,
  findArea,
  findPaneAnywhere,
  instanceId as makeInstanceId,
  listPanes,
  neighborAreaId,
  visiblePanes,
} from './model';
import * as persistence from './persistence';
import { closePaneSession, paneSessionKey } from './pane-lifetime';
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
 *
 * A user side override (rail customization — a glyph dragged to another rail)
 * moves that side to the front, so `openToolInDock` and role routing follow the
 * user's placement. It never *makes* a view dockable: declarations decide that.
 */
export function dockSidesOf(viewId: string): DockSide[] {
  const decl = resolveView(viewId);
  if (!decl) return [];
  // Embedded ⇒ never dockable. A rail glyph is exactly the "second home" an
  // embedded view is declaring it does not want, and it would be reachable by
  // glyph while absent from every other opener — the most confusing half-state.
  if (decl.embedded) return [];
  const declared: DockSide[] = decl.dockable
    ? [decl.dockable].flat()
    : roleOf(viewId) === 'tool'
      ? [decl.defaultDock ?? 'left']
      : [];
  if (declared.length === 0) return declared;
  const override = getRailPrefs().side[viewId];
  if (!override) return declared;
  return [override, ...declared.filter((s) => s !== override)];
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

// ---------------------------------------------------------------------------
// Sections (in-pane tabs)
// ---------------------------------------------------------------------------

/** The sections a view declares, in declaration order. */
export function sectionsOf(viewId: string): SectionDecl[] {
  return resolveView(viewId)?.sections ?? [];
}

/** The section a freshly opened pane of `viewId` starts on, if it has any. */
export function defaultSectionFor(viewId: string): string | undefined {
  const decls = sectionsOf(viewId);
  if (!decls.length) return undefined;
  return (decls.find((s) => s.default) ?? decls[0]).id;
}

/**
 * The section a pane is actually showing.
 *
 * Resolves rather than reads, because a stored `activeSection` can outlive the
 * declaration it names — a saved layout is loaded against whatever the module
 * declares *today*, and a plugin can be updated or uninstalled under an open
 * pane. Falling back to the default keeps the strip coherent instead of
 * rendering with no tab selected.
 */
export function activeSectionOf(pane: PaneState): string | undefined {
  const decls = sectionsOf(pane.viewId);
  if (!decls.length) return undefined;
  if (pane.activeSection && decls.some((s) => s.id === pane.activeSection)) {
    return pane.activeSection;
  }
  return defaultSectionFor(pane.viewId);
}

/** Switch one pane instance to a section. False when the view has no such section. */
export function setPaneSection(instanceId: string, section: string): boolean {
  const located = findPaneAnywhere(frame(), instanceId);
  if (!located) return false;
  if (!sectionsOf(located.pane.viewId).some((s) => s.id === section)) return false;
  layoutStore.dispatch({ type: 'SET_SECTION', instanceId, section });
  return true;
}

/**
 * Show a section by name, opening its host pane first if it isn't open — the
 * section-level twin of `revealRegionView`, and what `show("friends")` lands on.
 *
 * `hostViewId` is optional so a caller who only has a section id (a synthesized
 * command, the agent) doesn't have to know which pane owns it. When several
 * views declare the same section id, an explicit host wins.
 */
export function revealSection(section: string, hostViewId?: string): string | null {
  const host = hostViewId
    ? resolveView(hostViewId)
    : [...registry.panels, ...registry.widgets].find((v) =>
        v.sections?.some((s) => s.id === section),
      );
  if (!host) return null;
  let instance = hostInstanceOf(host.id);
  if (!instance) {
    const id = openPane(host.id);
    instance = id ? findPaneAnywhere(frame(), id) : null;
  }
  if (!instance) return null;
  focusInstance(instance);
  setPaneSection(instance.pane.instanceId, section);
  return instance.pane.instanceId;
}

/** The area's hosted role: that of its first tab, or null when empty. */
function areaRole(area: AreaNode): PaneRole | null {
  return area.tabs.length ? roleOf(area.tabs[0].viewId) : null;
}

function frame() {
  return layoutStore.getSnapshot().frame;
}

/** The focused pane instance, wherever it lives, or null. */
export function focusedPane(): LocatedPane | null {
  const f = frame();
  return f.focusedInstanceId ? findPaneAnywhere(f, f.focusedInstanceId) : null;
}

/** View id of the focused pane — the keybinding service's `paneFocus`. */
export function focusedViewId(): string | null {
  return focusedPane()?.pane.viewId ?? null;
}

/**
 * Move the browser's real focus to a pane instance's container.
 *
 * Logical focus (the store) and DOM focus used to drift: `alt+arrow` moved the
 * accent border while the caret stayed in whatever input the user had clicked,
 * so the next keystroke landed in the pane they had just navigated away from.
 * `PaneHost` tags each container with `data-pane-instance`; a pane can nominate a
 * better target (its editor, its search box) with `data-autofocus`.
 */
export function focusPaneDom(instanceId: string): void {
  if (typeof document === 'undefined') return;
  const host = document.querySelector<HTMLElement>(
    `[data-pane-instance="${CSS.escape(instanceId)}"]`,
  );
  if (!host) return;
  const target = host.querySelector<HTMLElement>('[data-autofocus]') ?? host;
  target.focus({ preventScroll: true });
}

/** Bring an already-open pane forward wherever it lives, and focus it. */
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
  layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: pane.instanceId });
  // After React has committed the tab/dock change, so the container exists.
  queueMicrotask(() => focusPaneDom(pane.instanceId));
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
  // The one place a pane genuinely goes away, so the one place its long-lived
  // resources are torn down. Unmount does not do this — see `pane-lifetime`.
  closePaneSession(paneSessionKey(layoutStore.getSnapshot().workspaceId, instanceId));
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
    // By instance id first, then — for singletons — by VIEW id: a preset-seeded
    // singleton carries a `#n` suffix, so the instance-id lookup alone would
    // miss it and split off a duplicate (same rule as openPaneInArea).
    const existing =
      findPaneAnywhere(f, wantedId) ??
      (singleton ? listPanes(f).find((p) => p.pane.viewId === viewId) : undefined);
    if (existing) {
      focusInstance(existing);
      return existing.pane.instanceId;
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
  instanceId?: string,
): string | null {
  const f = frame();
  if (!resolveView(viewId) || !findArea(f.center, areaId)) return null;
  // A caller-supplied instance id is the identity: focus it if it's already open,
  // the same focus-or-create rule `openPane` applies.
  if (instanceId) {
    const existing = findPaneAnywhere(f, instanceId);
    if (existing) {
      focusInstance(existing);
      return instanceId;
    }
  }
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
    instanceId: instanceId ?? (singleton ? viewId : makeInstanceId(viewId, f.paneSeq)),
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

/**
 * The host pane instance region commands should act on: the **focused pane**
 * when it is one, else any open instance of the host view.
 *
 * The focused-pane check has to come first and has to be by instance. With two
 * editor buffers open side by side, the old area-based lookup fell through to
 * "first instance found", so `n` toggled the left buffer's outline no matter
 * which one you were working in.
 */
function hostInstanceOf(hostViewId: string): LocatedPane | null {
  const f = frame();
  const focused = focusedPane();
  if (focused?.pane.viewId === hostViewId) return focused;
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

/**
 * The center area already hosting a pane of `viewId`, preferring the focused one.
 * Lets a module that opens many documents of one kind (editor buffers) stack them
 * as tabs in the area it already owns, instead of splitting off a new area each
 * time — `openPane`'s default, which is right for a *first* document but wrong for
 * the second file opened from the tree.
 */
export function areaHostingView(viewId: string): string | null {
  const f = frame();
  const hosts = listPanes(f).filter((p) => p.pane.viewId === viewId && p.location.kind === 'area');
  if (hosts.length === 0) return null;
  const focused = hosts.find(
    (p) => p.location.kind === 'area' && p.location.areaId === f.focusedAreaId,
  );
  const pick = focused ?? hosts[0];
  return pick.location.kind === 'area' ? pick.location.areaId : null;
}

/**
 * Retired view ids/titles → where their content lives now.
 *
 * The counterpart to `serialize.ts`'s `RENAMED_VIEWS`, and deliberately separate from
 * it. That map repairs saved **layouts**; this one keeps the **agent's vocabulary**
 * working. The two have different lifetimes on purpose: a stored arrangement is
 * disposable (it reseeds from its preset), but a name the agent — or the user talking
 * to it — has ever used should never stop resolving.
 *
 * Add an entry whenever a pane is merged away.
 */
export const VIEW_ALIASES: Readonly<Record<string, ShowTarget>> = {
  // Games: six panes merged into `games.lobby`'s sections. Each rendered a
  // component the lobby already renders, so they were a second home for the same
  // content — but "open the ladder" must keep working, for the user and the agent.
  'games.ladder': { kind: 'view', viewId: 'games.lobby', section: 'career' },
  Ladder: { kind: 'view', viewId: 'games.lobby', section: 'career' },
  'games.challenges': { kind: 'view', viewId: 'games.lobby', section: 'career' },
  Challenges: { kind: 'view', viewId: 'games.lobby', section: 'career' },
  'games.profile': { kind: 'view', viewId: 'games.lobby', section: 'career' },
  Profile: { kind: 'view', viewId: 'games.lobby', section: 'career' },
  'games.replays': { kind: 'view', viewId: 'games.lobby', section: 'replays' },
  'games.players': { kind: 'view', viewId: 'games.lobby', section: 'social' },
  Players: { kind: 'view', viewId: 'games.lobby', section: 'social' },
  'games.plaza': { kind: 'view', viewId: 'games.lobby', section: 'social' },
  'The Plaza': { kind: 'view', viewId: 'games.lobby', section: 'social' },

  // People: nine panes across three modules became one. The first five moved
  // whole; the last four were infrastructure readouts, and their names now land
  // on the nearest thing a person actually wanted — "who is around" is Friends,
  // and the raw diagnostics fold away under Me.
  'social.friends': { kind: 'view', viewId: 'people.home', section: 'friends' },
  Friends: { kind: 'view', viewId: 'people.home', section: 'friends' },
  'network.chat': { kind: 'view', viewId: 'people.home', section: 'messages' },
  'Peer Chat': { kind: 'view', viewId: 'people.home', section: 'messages' },
  'commons.directory': { kind: 'view', viewId: 'people.home', section: 'discover' },
  Commons: { kind: 'view', viewId: 'people.home', section: 'discover' },
  'commons.requests': { kind: 'view', viewId: 'people.home', section: 'requests' },
  'Commons Requests': { kind: 'view', viewId: 'people.home', section: 'requests' },
  'commons.profile': { kind: 'view', viewId: 'people.home', section: 'me' },
  'Commons Profile': { kind: 'view', viewId: 'people.home', section: 'me' },
  'network.peers': { kind: 'view', viewId: 'people.home', section: 'friends' },
  Peers: { kind: 'view', viewId: 'people.home', section: 'friends' },
  'network.monitor': { kind: 'view', viewId: 'people.home', section: 'me' },
  'Peer Monitor': { kind: 'view', viewId: 'people.home', section: 'me' },
  'network.relay': { kind: 'view', viewId: 'people.home', section: 'me' },
  'Agent Relay': { kind: 'view', viewId: 'people.home', section: 'me' },
  // Lobby has no successor surface: rendezvous is a service, and rooms are the
  // hassault server browser's job. It resolves to Friends rather than nothing so
  // the name still lands somewhere sensible instead of a did-you-mean.
  'network.lobby': { kind: 'view', viewId: 'people.home', section: 'friends' },

  // Explorer: five left-dock browsers became five sections. Unlike the games and
  // People merges, all five views still exist and still render — they are
  // `embedded`, so `show` would reach them through `hostOfEmbedded` anyway. These
  // entries are what make the *titles* resolve, and they keep resolution to one
  // lookup instead of a scan.
  'files.tree': { kind: 'view', viewId: 'explorer.home', section: 'files' },
  'notebook.browser': { kind: 'view', viewId: 'explorer.home', section: 'notebooks' },
  Notebooks: { kind: 'view', viewId: 'explorer.home', section: 'notebooks' },
  'flow.library': { kind: 'view', viewId: 'explorer.home', section: 'flows' },
  Flows: { kind: 'view', viewId: 'explorer.home', section: 'flows' },
  'records.list': { kind: 'view', viewId: 'explorer.home', section: 'tables' },
  Tables: { kind: 'view', viewId: 'explorer.home', section: 'tables' },
  'training.projects': { kind: 'view', viewId: 'explorer.home', section: 'projects' },
  'Training Projects': { kind: 'view', viewId: 'explorer.home', section: 'projects' },
};

/** The candidate set `show` matches against, gathered from the live registry. */
function showCandidates(): ShowCandidates {
  const views = [...registry.panels, ...registry.widgets].map((v) => ({
    id: v.id,
    title: v.title,
    sections: v.sections?.map((s) => ({ id: s.id, label: s.label })),
    regions: v.regions?.map((r) => ({ id: r.id, label: r.label })),
  }));
  const workspaces = registry.framePresets.map((p) => ({ id: p.id, name: p.name }));
  return { views, workspaces, aliases: VIEW_ALIASES };
}

/** What `show` did, handed straight back to the model. */
export interface ShowResult {
  ok: boolean;
  /** What happened, in the model's own vocabulary. */
  action?: 'focused' | 'opened' | 'revealed' | 'switched-workspace';
  viewId?: string;
  instanceId?: string;
  title?: string;
  workspaceId?: string;
  /** The in-pane section left showing, when the pane has any. */
  section?: string;
  /** The pane's agent-readable snapshot, so no `get_pane_context` round is needed. */
  context?: unknown;
  error?: string;
  didYouMean?: string[];
}

/**
 * Where an `embedded` view actually lives: a region strip, or a section body.
 *
 * Returns null for a view that isn't embedded, and also for an embedded one that
 * nothing hosts — a declaration mistake, and one worth leaving visible rather
 * than papering over: the caller falls through to opening it standalone, which
 * is at least reachable while the declaration gets fixed.
 */
function hostOfEmbedded(
  viewId: string,
):
  | { kind: 'region'; hostViewId: string }
  | { kind: 'section'; hostViewId: string; section: string }
  | null {
  if (!resolveView(viewId)?.embedded) return null;
  const region = regionHostOf(viewId);
  if (region) return { kind: 'region', hostViewId: region.id };
  for (const host of [...registry.panels, ...registry.widgets]) {
    const section = host.sections?.find((s) => s.view === viewId);
    if (section) return { kind: 'section', hostViewId: host.id, section: section.id };
  }
  return null;
}

/** `{ section }` for a pane that has sections, or nothing — read after any switch. */
function sectionResult(instanceId: string): { section?: string } {
  const pane = findPaneAnywhere(frame(), instanceId)?.pane;
  const section = pane ? activeSectionOf(pane) : undefined;
  return section ? { section } : {};
}

/**
 * A pane's agent snapshot with its active section stamped on.
 *
 * The section is contributed here rather than left to each pane's provider so it
 * can't be forgotten: a merged pane's snapshot is ambiguous without it (the same
 * `people.home` instance means something different on Friends than on Requests),
 * and a module that omitted it would produce a plausible-looking snapshot the
 * agent then reasons about wrongly.
 *
 * Providers are keyed by pane instance id, and a section body renders **inside**
 * its host's `PaneInstanceContext` — so unlike a region strip (which gets a
 * synthetic id no enumeration descends into), a section's provider registers
 * under the real, listable instance id. The rule that follows: exactly one live
 * provider per pane, i.e. the host component or its section bodies, never both.
 */
export function readPaneAgentContext(instanceId: string): Record<string, unknown> | null {
  const snapshot = readAgentContext(instanceId);
  const pane = findPaneAnywhere(frame(), instanceId)?.pane;
  const section = pane ? activeSectionOf(pane) : undefined;
  if (!section) return snapshot;
  // `sections` is contributed by the shell, not by any provider — most sections
  // have none (only the mounted body can provide, and the one-provider rule means
  // at most one of them ever does). Without this, `show("notebooks")` came back as
  // a bare `{section}` and the model, having nothing to answer from, answered from
  // whatever *else* was in its context — confidently and wrongly. Naming the
  // siblings also makes the next hop discoverable without `list_available_panes`.
  const sections = sectionsOf(pane!.viewId).map((s) => s.id);
  return { section, sections, ...(snapshot ?? {}) };
}

/**
 * Reveal whatever `target` names — the agent's one high-level "put this in front of
 * me" verb, replacing a `list_available_panes` → `open_pane` → `get_pane_context`
 * sequence with a single call.
 *
 * It open-or-focuses (never opening a second copy of something already visible),
 * reveals a region inside its host, or switches workspace, and returns the resulting
 * pane's context snapshot inline.
 */
export function showTarget(target: string, where?: 'here' | 'beside' | 'dock'): ShowResult {
  const resolved = resolveShowTarget(target, showCandidates());
  if (!resolved) {
    const titles = [...registry.panels, ...registry.widgets].map((v) => v.title);
    return {
      ok: false,
      error: `nothing matches ${JSON.stringify(target)}`,
      didYouMean: titles.slice(0, 3),
    };
  }

  if (resolved.kind === 'workspace') {
    registry.switchWorkspace(resolved.workspaceId);
    return { ok: true, action: 'switched-workspace', workspaceId: resolved.workspaceId };
  }

  if (resolved.kind === 'region') {
    revealRegionView(resolved.regionViewId);
    const host = regionHostOf(resolved.regionViewId);
    return {
      ok: true,
      action: 'revealed',
      viewId: resolved.regionViewId,
      title: resolveView(resolved.regionViewId)?.title,
      ...(host ? { instanceId: hostInstanceOf(host.id)?.pane.instanceId } : {}),
    };
  }

  // An embedded view has no standalone home, so "show it" means "show it where it
  // lives". Without this the reachability invariant would fail exactly where it
  // matters most: a name that used to open a pane would resolve, then fail to open.
  const embeddedHost = hostOfEmbedded(resolved.viewId);
  if (embeddedHost) {
    if (embeddedHost.kind === 'region') {
      revealRegionView(resolved.viewId);
      const instanceId = hostInstanceOf(embeddedHost.hostViewId)?.pane.instanceId;
      return {
        ok: true,
        action: 'revealed',
        viewId: resolved.viewId,
        title: resolveView(resolved.viewId)?.title,
        ...(instanceId ? { instanceId } : {}),
      };
    }
    const instanceId = revealSection(embeddedHost.section, embeddedHost.hostViewId);
    return {
      ok: Boolean(instanceId),
      action: 'revealed',
      viewId: resolved.viewId,
      title: resolveView(resolved.viewId)?.title,
      section: embeddedHost.section,
      ...(instanceId
        ? { instanceId, context: readPaneAgentContext(instanceId) }
        : { error: `could not open ${embeddedHost.hostViewId}` }),
    };
  }

  const decl = resolveView(resolved.viewId);
  // Already open anywhere? Focus it — never open a second copy of something the
  // user can already see.
  const existing = listPanes(frame()).find((p) => p.pane.viewId === resolved.viewId);
  if (existing) {
    focusInstance(existing);
    // Switch the section *before* reading context: the pane's one provider
    // reports its active section, so reading first would describe the tab the
    // user was on rather than the one just asked for.
    if (resolved.section) setPaneSection(existing.pane.instanceId, resolved.section);
    return {
      ok: true,
      action: 'focused',
      viewId: resolved.viewId,
      instanceId: existing.pane.instanceId,
      title: decl?.title,
      ...sectionResult(existing.pane.instanceId),
      context: readPaneAgentContext(existing.pane.instanceId),
    };
  }

  const instanceId =
    where === 'dock' && isDockable(resolved.viewId)
      ? openToolInDock(resolved.viewId)
      : openPane(resolved.viewId);
  if (instanceId && resolved.section) setPaneSection(instanceId, resolved.section);
  return {
    ok: Boolean(instanceId),
    action: 'opened',
    ...(instanceId ? sectionResult(instanceId) : {}),
    viewId: resolved.viewId,
    title: decl?.title,
    ...(instanceId
      ? { instanceId, context: readPaneAgentContext(instanceId) }
      : { error: `could not open ${resolved.viewId}` }),
  };
}

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
  // Carry the pane focus (and the caret) along — navigating areas that left the
  // keyboard behind in the old area is what made `alt+arrow` feel broken.
  const pane = findArea(frame().center, neighbor)?.tabs[
    findArea(frame().center, neighbor)?.activeTab ?? 0
  ];
  if (pane) {
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: pane.instanceId });
    queueMicrotask(() => focusPaneDom(pane.instanceId));
  }
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
  const sections = sectionsOf(pane.viewId);
  if (sections.length) {
    out.activeSection = activeSectionOf(pane);
    out.sections = sections.map((s) => s.id);
  }
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

/**
 * The workspace's ambient agent context: a snapshot of every pane the user can
 * actually see that exposes one. This is what makes a workspace a *role* rather
 * than furniture — the agent in the CRM workspace knows which record is open
 * without spending a `list_open_panes` + `get_pane_context` round-trip on it.
 *
 * Budgeted by the caller, not here: `limit` caps how many panes are attached (the
 * ones nearest the user first — floating, then docks, then center) and `maxChars`
 * truncates each serialized snapshot. Unbudgeted, this would hand back the token
 * savings the gateable `layout` group just won.
 */
export function readVisibleAgentContexts(
  limit: number,
  maxChars: number,
  skipInstanceId?: string,
): Array<Record<string, unknown>> {
  const ranked = visiblePanes(frame()).sort(
    (a, b) => LOCATION_PRIORITY[b.location.kind] - LOCATION_PRIORITY[a.location.kind],
  );
  const out: Array<Record<string, unknown>> = [];
  for (const { pane, location } of ranked) {
    if (out.length >= limit) break;
    if (pane.instanceId === skipInstanceId) continue;
    const snapshot = readPaneAgentContext(pane.instanceId);
    if (!snapshot) continue;
    out.push({
      instanceId: pane.instanceId,
      viewId: pane.viewId,
      title: resolveView(pane.viewId)?.title ?? pane.viewId,
      location: location.kind,
      snapshot: truncateSnapshot(snapshot, maxChars),
    });
  }
  return out;
}

/** Panes the user reached for most recently rank first when the budget is tight. */
const LOCATION_PRIORITY: Record<LocatedPane['location']['kind'], number> = {
  floating: 3,
  dock: 2,
  area: 1,
};

/** Serialize a snapshot, clipping any oversized string field rather than the whole
 * object — a truncated JSON blob is unparseable, a clipped field still reads. */
function truncateSnapshot(
  snapshot: Record<string, unknown>,
  maxChars: number,
): Record<string, unknown> {
  if (JSON.stringify(snapshot).length <= maxChars) return snapshot;
  const budget = Math.max(120, Math.floor(maxChars / Math.max(1, Object.keys(snapshot).length)));
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(snapshot)) {
    const text = typeof value === 'string' ? value : (JSON.stringify(value) ?? '');
    out[key] =
      text.length > budget
        ? `${text.slice(0, budget)}… (${text.length - budget} more chars — read the pane directly with get_pane_context)`
        : value;
  }
  return out;
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
  setFrameCommandHandler({
    togglePosition: (hostViewId, position) => {
      const instance = hostInstanceOf(hostViewId);
      if (instance) toggleRegion(instance.pane.instanceId, position);
    },
    pickView: (regionViewId) => toggleRegionView(regionViewId),
    revealSection: (section, hostViewId) => {
      revealSection(section, hostViewId);
    },
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
      // The instance survives, but whatever the *old* view was running in it does
      // not — switching a terminal pane to a browser must not leave a shell behind.
      closePaneSession(paneSessionKey(layoutStore.getSnapshot().workspaceId, instanceId));
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
