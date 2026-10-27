/**
 * The desktop controller: pane opening, window verbs, region reveal/toggle, and
 * the `LayoutController` implementation the agent relay drives — all as
 * dispatches into the layout store, so user gestures, keybindings, and agent
 * tools converge on the same verbs and every mutation rides the store's
 * autosave. Installed by the shell (`installFrameController`, from
 * packages/ui/src/layout/install.ts).
 */
import { hasAgentContext, readAgentContext } from '../agent-context';
import { isAppFullscreen, setAppFullscreen } from '../fullscreen';
import { currentThemeId, THEMES } from '../theme';
import { windowControl } from '../window';
import {
  registry,
  type LayoutController,
  type OpenPaneOptions,
  type PanelDecl,
  type SectionDecl,
  type WidgetDecl,
} from '../registry';
import { isPaneDirty, runCloseGuard } from './close-guards';
import { setFrameCommandHandler } from './frame-bus';
import { resolveShowTarget, type ShowCandidates, type ShowTarget } from './show';
import {
  findAreaAnywhere,
  findPaneAnywhere,
  instanceId as makeInstanceId,
  listPanes,
  visiblePanes,
} from './model';
import { runPaneExit } from './pane-exit';
import { closePaneSession, paneSessionKey } from './pane-lifetime';
import { taskbarEntries } from './taskbar';
import { lastPlacement, rememberPlacement } from './window-placement';
import { arrangeWindows, NOMINAL_VIEWPORT, type ArrangeStyle } from './snap';
import { layoutStore } from './store';
import type {
  BackdropRef,
  LocatedPane,
  NavDirection,
  PaneRole,
  PaneState,
  RegionPosition,
  RegionState,
  SnapZone,
  WindowMode,
  WindowRect,
  WindowState,
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

/**
 * A pane's display name: its own `title` param before the view's declared title.
 *
 * The one rule for every surface that labels a pane — document tabs, floating
 * window titlebars, taskbar buttons. A view title names a *kind* of pane, so
 * without the param six open notebooks are six buttons reading "Notebook
 * Editor"; the param is how an editor buffer, a terminal or a notebook says
 * which file or shell it holds. Shared rather than reimplemented per surface,
 * because it had drifted: the taskbar honoured it and the floating titlebar did
 * not, so the same pane was labelled two different ways at once.
 */
export function paneDisplayTitle(pane: {
  viewId: string;
  params?: Record<string, unknown>;
}): string {
  const t = pane.params?.title ?? pane.params?.name ?? pane.params?.path;
  if (typeof t === 'string' && t.trim()) return t;
  return resolveView(pane.viewId)?.title ?? pane.viewId;
}

/** A view's layout role; unannotated panels/widgets fall back per their kind. */
export function roleOf(viewId: string): PaneRole {
  const panel = registry.panels.find((p) => p.id === viewId);
  if (panel) return panel.role ?? 'document';
  const widget = registry.widgets.find((w) => w.id === viewId);
  return widget?.role ?? 'widget';
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

/** Bring an already-open pane forward — raise its window, show its tab — and focus it. */
export function focusInstance(located: LocatedPane): void {
  const { location, pane } = located;
  layoutStore.dispatch({ type: 'BRING_WINDOW_FRONT', windowId: location.windowId });
  layoutStore.dispatch({ type: 'FOCUS_WINDOW', windowId: location.windowId });
  const index = findAreaAnywhere(frame(), location.areaId)?.area.tabs.findIndex(
    (t) => t.instanceId === pane.instanceId,
  );
  if (index !== undefined && index >= 0) {
    layoutStore.dispatch({ type: 'SET_ACTIVE_TAB', areaId: location.areaId, index });
  }
  // Un-minimize: focusing a pane the user cannot see would silently do nothing.
  const win = frame().windows.find((w) => w.id === location.windowId);
  if (win?.mode === 'minimized') {
    layoutStore.dispatch({ type: 'SET_WINDOW_MODE', windowId: win.id, mode: 'normal' });
  }
  layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: pane.instanceId });
  // After React has committed the tab/dock change, so the container exists.
  queueMicrotask(() => focusPaneDom(pane.instanceId));
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
  // Its exit, now that the guard has agreed to the close (capped; see pane-exit).
  await runPaneExit(instanceId);
  if (!findPaneAnywhere(frame(), instanceId)) return false;
  layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId });
  // The one place a pane genuinely goes away, so the one place its long-lived
  // resources are torn down. Unmount does not do this — see `pane-lifetime`.
  closePaneSession(paneSessionKey(layoutStore.getSnapshot().workspaceId, instanceId));
  return true;
}

/**
 * Open a view in a window, or focus it if it is already open — the
 * `registry.openPanel` target. Returns the instance id, or null.
 *
 * Identity: a caller-supplied `instanceId` is focus-or-create. A singleton (every
 * widget, and any panel that says so) uses its view id as its instance id and is
 * found by view id, so a second open focuses the first rather than adding a twin.
 * Anything else opens a fresh window each time — a second terminal is a second
 * shell.
 *
 * A new window opens where that view was last left: a pane snapped to the right
 * edge was put there on purpose, and a placement cannot live in the frame because
 * a closed pane has no window to hold one (see `window-placement`). Absent a
 * memory it lands on the cascade.
 */
export function openPane(viewId: string, opts?: OpenPaneOptions): string | null {
  const decl = resolveView(viewId);
  if (!decl) {
    // A retired view id. `VIEW_ALIASES` already maps every one of these onto the
    // pane and section that replaced it — it was just consulted only by the
    // agent's `show`, so "open my profile" worked when the model said it and did
    // nothing when the user clicked the button next to the same words. Three
    // buttons in the games module were inert for exactly this reason.
    const alias = VIEW_ALIASES[viewId];
    if (alias?.kind === 'view') {
      if (!alias.section) return openPane(alias.viewId, opts);
      // Params first, through the real open: a deep link (`{turnId}`) into a pane
      // that was merged into a section must still reach the pane that now reads it.
      if (opts?.params) openPane(alias.viewId, opts);
      return revealSection(alias.section, alias.viewId);
    }
    // Nothing resolved: say so. `openPanel` returning null in silence is why the
    // three dead buttons above survived a pane consolidation and two releases —
    // there was no signal anywhere that a click had gone nowhere.
    console.warn(`[layout] openPane: no view or alias named "${viewId}"`);
    return null;
  }
  // An embedded view opens where it lives — its host's strip or section — not
  // bare in a window of its own, where it would sit stranded from the host that
  // feeds it. The same resolution `show` already makes; buttons that say "Radar"
  // must land where the agent's `show("Radar")` does.
  const embeddedHost = hostOfEmbedded(viewId);
  if (embeddedHost) {
    if (embeddedHost.kind === 'section') {
      return revealSection(embeddedHost.section, embeddedHost.hostViewId);
    }
    revealRegionView(viewId);
    return hostInstanceOf(embeddedHost.hostViewId)?.pane.instanceId ?? null;
  }
  // Embedded with nothing hosting it is a declaration mistake. Opening it in a
  // window anyway would be the flag lying about the view; refusing keeps the
  // mistake visible until the declaration is fixed.
  if (decl.embedded) {
    console.warn(`[layout] openPane: "${viewId}" is embedded but nothing hosts it`);
    return null;
  }

  const f = frame();
  const panel = registry.panels.find((p) => p.id === viewId);
  const singleton = panel ? Boolean(panel.singleton) : true;
  const wantedId = opts?.instanceId ?? (singleton ? viewId : undefined);
  if (wantedId) {
    // By instance id first, then — for singletons — by VIEW id: a singleton
    // restored from an older layout can carry a `#n` suffix, so the instance-id
    // lookup alone would miss it and open a duplicate.
    const existing =
      findPaneAnywhere(f, wantedId) ??
      (singleton ? listPanes(f).find((p) => p.pane.viewId === viewId) : undefined);
    if (existing) {
      // Apply the params before focusing. Without this a deep link into a pane
      // that is *already open* focuses it and changes nothing.
      //
      // Only when params were actually supplied: a plain "open the LocalTrack
      // pane" must not wipe a selection a previous deep link established.
      if (opts?.params) {
        layoutStore.dispatch({
          type: 'SET_PANE_PARAMS',
          instanceId: existing.pane.instanceId,
          params: opts.params,
        });
      }
      focusInstance(existing);
      return existing.pane.instanceId;
    }
  }

  const pane: PaneState = {
    instanceId: wantedId ?? makeInstanceId(viewId, f.paneSeq),
    viewId,
    regions: regionsFor(viewId),
    ...(opts?.params ? { params: opts.params } : {}),
  };
  const placement = lastPlacement(viewId);
  const before = layoutStore.getSnapshot();
  if (
    layoutStore.dispatch({
      type: 'OPEN_WINDOW',
      pane,
      ...(placement ? { rect: placement.rect } : {}),
    }) === before
  ) {
    return null;
  }
  // Re-snap rather than only restoring the geometry: a snapped window keeps a
  // `restoreRect` to un-snap back to and re-derives its rect when the desktop
  // resizes. Replaying the pixels alone would look identical until the first
  // time either of those mattered.
  if (placement?.snap) snapWindow(pane.instanceId, placement.snap);
  layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: pane.instanceId });
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
 *     it in place, so the window, tab position and region strips survive.
 *  3. Otherwise open a fresh pane the usual way.
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
      (p) => p.pane.viewId === viewId && !isPaneDirty(p.pane.instanceId) && canReuse(p.pane),
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

/**
 * Current-or-declared state of one region strip on a pane instance.
 *
 * The declaration fallback is not belt-and-braces: `regions` is written onto a
 * pane when it is *opened*, so a pane persisted before its view declared a
 * region has none, and a region added to an existing view would be invisible on
 * every pane already in the saved layout.
 */
export function regionAt(pane: PaneState, position: RegionPosition): RegionState | null {
  return pane.regions?.[position] ?? regionsFor(pane.viewId)?.[position] ?? null;
}

/** How a region strip should be drawn: not at all, as a rail, or in full. */
export type RegionDisplay = 'none' | 'rail' | 'strip';

/**
 * What the frame draws for one region position.
 *
 * The rule that changed: a region that is **closed** draws the *rail*, not
 * nothing. A view's `regions` are declared without `defaultOpen` far more often
 * than with it — the training notebook declares six that way — and a closed
 * region used to render `null`, so a pane could declare Metrics, Architecture
 * and Rollout and show no sign that any of them existed. They were reachable
 * only from the command palette or a keychord you had to already know, which is
 * indistinguishable from a feature that was never built.
 *
 * So `closed` and `collapsed` now look the same on screen — a thin rail of
 * icons — and differ only in which flag a caller set. That is deliberate: the
 * pane always shows what it *can* show, and putting a strip away never removes
 * the way back.
 */
export function regionDisplay(pane: PaneState, position: RegionPosition): RegionDisplay {
  const region = regionAt(pane, position);
  if (!region || region.views.length === 0) return 'none';
  return !region.open || region.collapsed ? 'rail' : 'strip';
}

export function toggleRegion(
  instanceId: string,
  position: RegionPosition,
  open?: boolean,
): boolean {
  const located = findPaneAnywhere(frame(), instanceId);
  if (!located) return false;
  const region = regionAt(located.pane, position);
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
  // Through `regionAt`, for the same reason `toggleRegion` does: a pane that was
  // persisted before its view declared this region carries no `regions` of its
  // own, and reading the instance alone made the control a silent no-op there.
  const region = regionAt(located.pane, position);
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
  const region = regionAt(located.pane, position);
  if (!region || !region.views.includes(viewId)) return false;
  layoutStore.dispatch({
    type: 'SET_REGION',
    instanceId,
    position,
    region: { ...region, open: true, collapsed: false, activeView: viewId },
  });
  return true;
}

/**
 * The view that hosts `regionViewId` in its declared regions, if any.
 *
 * More than one view may host the same region — Outline and Provenance are
 * satellites of `editor.buffer` *and* of the IDE workbench, since both are
 * surfaces you read code in. Registration order decides nothing useful there, so
 * the host on screen wins: the focused pane first, then any host with an open
 * instance, and only then the first declaration. Without that, `code.openOutline`
 * from inside the workbench would open a second pane behind it.
 */
export function regionHostOf(regionViewId: string): ViewDecl | undefined {
  const hosts = [...registry.panels, ...registry.widgets].filter((v) =>
    v.regions?.some((r) => r.id === regionViewId),
  );
  if (hosts.length <= 1) return hosts[0];
  const focusedView = focusedPane()?.pane.viewId;
  const focusedHost = hosts.find((v) => v.id === focusedView);
  if (focusedHost) return focusedHost;
  const open = listPanes(frame());
  return hosts.find((v) => open.some((p) => p.pane.viewId === v.id)) ?? hosts[0];
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
 * a match starts. Falls back to opening the view in a window if nothing hosts it.
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

/**
 * Retired view ids/titles → where their content lives now.
 *
 * The counterpart to `serialize.ts`'s `RENAMED_VIEWS`, and deliberately separate from
 * it. That map repairs saved **layouts**; this one keeps the **agent's vocabulary**
 * working. The two have different lifetimes on purpose: a stored arrangement is
 * disposable, but a name the agent — or the user talking to it — has ever used
 * should never stop resolving.
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

  // Explorer: five left-dock browsers became sections. Unlike the games and
  // People merges, all five views still exist and still render — they are
  // `embedded`, so `show` would reach them through `hostOfEmbedded` anyway. These
  // entries are what make the *titles* resolve, and they keep resolution to one
  // lookup instead of a scan.
  'files.tree': { kind: 'view', viewId: 'explorer.home', section: 'files' },
  'notebook.browser': { kind: 'view', viewId: 'explorer.home', section: 'notebooks' },
  Notebooks: { kind: 'view', viewId: 'explorer.home', section: 'notebooks' },
  'training.projects': { kind: 'view', viewId: 'explorer.home', section: 'projects' },
  'Training Projects': { kind: 'view', viewId: 'explorer.home', section: 'projects' },
  // Two of those five moved on again, out of Explorer and onto the document they
  // belong to, as region strips. The name still has to land — a pane that leaves
  // one home for another must never become unreachable, which is the same
  // invariant that put it here in the first place.
  'flow.library': { kind: 'region', regionViewId: 'flow.library' },
  Flows: { kind: 'region', regionViewId: 'flow.library' },
  'records.list': { kind: 'region', regionViewId: 'records.list' },
  Tables: { kind: 'region', regionViewId: 'records.list' },

  // Notebook: two panes answer to this one word — "Notebook Editor" and
  // "Training Notebook" — so the bare name resolves through nothing on its own.
  // This pins it to the pane a person means by it.
  Notebook: { kind: 'view', viewId: 'notebook.editor' },

  // hAssault: ten windows became three (Game, Dev Tools, Server). The studio's
  // per-tab panes are the studio; the armory is a section of the game's main
  // menu; the four companions are strips on the game window.
  'hassault.modelViewer': { kind: 'view', viewId: 'hassault.studio' },
  'hAssault Model Viewer': { kind: 'view', viewId: 'hassault.studio' },
  'hassault.modelEditor': { kind: 'view', viewId: 'hassault.studio' },
  'hAssault Model Editor': { kind: 'view', viewId: 'hassault.studio' },
  'hassault.animEditor': { kind: 'view', viewId: 'hassault.studio' },
  'hAssault Animation Editor': { kind: 'view', viewId: 'hassault.studio' },
  'hAssault Model Studio': { kind: 'view', viewId: 'hassault.studio' },
  'hassault.armory': { kind: 'view', viewId: 'hassault.play' },
  'hAssault Armory & Skins': { kind: 'view', viewId: 'hassault.play' },

  // Records: the table designer became a section of the grid it configures.
  'records.schema': { kind: 'view', viewId: 'records.grid', section: 'setup' },
  'Table setup': { kind: 'view', viewId: 'records.grid', section: 'setup' },

  // Games: the replay viewer is a lobby section; AgentTown a Social tab.
  'games.replay': { kind: 'view', viewId: 'games.lobby', section: 'replay' },
  Replay: { kind: 'view', viewId: 'games.lobby', section: 'replay' },
  'games.town': { kind: 'view', viewId: 'games.lobby', section: 'social' },
  AgentTown: { kind: 'view', viewId: 'games.lobby', section: 'social' },

  // Research: one hub, two sections. The console was titled "Deep Research".
  'Deep Research': { kind: 'view', viewId: 'research.console', section: 'deep' },

  // Interpretability: six panes became one window with a rail. The section bodies
  // are embedded views, so their own titles resolve through `hostOfEmbedded`;
  // these keep the retired ids and the names the old panes and tabs carried.
  'interpretability.context': {
    kind: 'view',
    viewId: 'interpretability.suite',
    section: 'context',
  },
  'interpretability.budget': { kind: 'view', viewId: 'interpretability.suite', section: 'context' },
  'interpretability.architecture': {
    kind: 'view',
    viewId: 'interpretability.suite',
    section: 'model',
  },
  'agentpedia.hub': { kind: 'view', viewId: 'interpretability.suite', section: 'runs' },
  Agentpedia: { kind: 'view', viewId: 'interpretability.suite', section: 'runs' },
  'webml.lens': { kind: 'view', viewId: 'interpretability.suite', section: 'lens' },
  'Logit Lens': { kind: 'view', viewId: 'interpretability.suite', section: 'lens' },
  Traces: { kind: 'view', viewId: 'interpretability.suite', section: 'traces' },
  Stepper: { kind: 'view', viewId: 'interpretability.suite', section: 'stepper' },
  Lens: { kind: 'view', viewId: 'interpretability.suite', section: 'lens' },
};

/** The candidate set `show` matches against, gathered from the live registry. */
function showCandidates(): ShowCandidates {
  const views = [...registry.panels, ...registry.widgets].map((v) => ({
    id: v.id,
    title: v.title,
    sections: v.sections?.map((s) => ({ id: s.id, label: s.label })),
    regions: v.regions?.map((r) => ({ id: r.id, label: r.label })),
  }));
  return { views, aliases: VIEW_ALIASES };
}

/** What `show` did, handed straight back to the model. */
export interface ShowResult {
  ok: boolean;
  /** What happened, in the model's own vocabulary. */
  action?: 'focused' | 'opened' | 'revealed';
  viewId?: string;
  instanceId?: string;
  title?: string;
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
 * Providers are keyed by pane instance **and section**, and a section body renders
 * **inside** its host's `PaneInstanceContext` — so unlike a region strip (which
 * gets a synthetic id no enumeration descends into), a section's provider
 * registers under the real, listable instance id. The pane-level and section-level
 * snapshots merge (section last); reading is pinned to the *active* section so a
 * provider that outlived its switch cannot answer for the tab that replaced it.
 */
export function readPaneAgentContext(instanceId: string): Record<string, unknown> | null {
  const pane = findPaneAnywhere(frame(), instanceId)?.pane;
  const section = pane ? activeSectionOf(pane) : undefined;
  const snapshot = readAgentContext(instanceId, section);
  if (!section) return snapshot;
  // `sections` is contributed by the shell, not by any provider — many sections
  // have none (only the mounted body can provide at all). Without this,
  // `show("notebooks")` came back as a bare `{section}` and the model, having
  // nothing to answer from, answered from whatever *else* was in its context —
  // confidently and wrongly. Naming the siblings also makes the next hop
  // discoverable without `list_available_panes`.
  //
  // `hasPayload: false` says the same thing out loud for the section itself: an
  // empty snapshot does not read as "nothing to report", it reads as silence, and
  // silence is what the model fills in from elsewhere.
  const sections = sectionsOf(pane!.viewId).map((s) => s.id);
  return { section, sections, hasPayload: snapshot !== null, ...(snapshot ?? {}) };
}

/**
 * Reveal whatever `target` names — the agent's one high-level "put this in front of
 * me" verb, replacing a `list_available_panes` → `open_pane` → `get_pane_context`
 * sequence with a single call.
 *
 * It open-or-focuses (never opening a second copy of something already visible) or
 * reveals a region inside its host, and returns the resulting pane's context
 * snapshot inline.
 */
export function showTarget(target: string): ShowResult {
  const resolved = resolveShowTarget(target, showCandidates());
  if (!resolved) {
    const titles = [...registry.panels, ...registry.widgets].map((v) => v.title);
    return {
      ok: false,
      error: `nothing matches ${JSON.stringify(target)}`,
      didYouMean: titles.slice(0, 3),
    };
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

  const instanceId = openPane(resolved.viewId);
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

// ---------------------------------------------------------------------------
// Window verbs
//
// One set of verbs, three callers: pointer gestures, keybindings, and agent tools.
// A gesture that reached into the store directly would be the one path whose rules
// (clamping, un-snapping, raising) drifted from the other two.
// ---------------------------------------------------------------------------

/** The window a verb should act on: the named one, else the focused one. */
function targetWindowId(id?: string): string | null {
  const f = frame();
  if (id) {
    // Accept a pane instance id too — every caller that has a pane has one, and
    // making them look up the window first is a lookup they can get wrong.
    if (f.windows.some((w) => w.id === id)) return id;
    return findPaneAnywhere(f, id)?.location.windowId ?? null;
  }
  return f.focusedWindowId;
}

/** The live desktop surface size, measured by the window layer. */
let desktopMeasurer: (() => { w: number; h: number } | null) | null = null;

/** Installed by the window layer so pure verbs can resolve pixel geometry. */
export function setDesktopMeasurer(fn: (() => { w: number; h: number } | null) | null): void {
  desktopMeasurer = fn;
}

export function desktopViewport(): { w: number; h: number } {
  return desktopMeasurer?.() ?? frame().windowViewport ?? NOMINAL_VIEWPORT;
}

/**
 * `fill` asks for snap-assist: the window covering the other half is snapped into
 * it, so the result is a split rather than one window over another. Deliberately a
 * parameter — a user snapping is arranging a screen, while the `window_state` agent
 * tool is moving one named window and must not shuffle a second.
 */
export function snapWindow(
  idOrInstance: string | undefined,
  zone: SnapZone | null,
  fill = false,
): boolean {
  const windowId = targetWindowId(idOrInstance);
  if (!windowId) return false;
  const before = layoutStore.getSnapshot();
  return (
    layoutStore.dispatch({
      type: 'SET_WINDOW_MODE',
      windowId,
      mode: zone === 'max' ? 'maximized' : 'normal',
      ...(zone && zone !== 'max' ? { snap: zone } : {}),
      viewport: desktopViewport(),
      ...(fill ? { fill: true } : {}),
    }) !== before
  );
}

/**
 * Snap whatever the user is looking at: the focused window, else the window of the
 * focused pane. Shared by the chords, the snap palette and the `window_state` agent
 * tool so all three behave identically.
 */
export function snapFocused(zone: SnapZone | null, fill = false): boolean {
  const f = frame();
  return snapWindow(f.focusedWindowId ?? f.focusedInstanceId ?? undefined, zone, fill);
}

export function setWindowMode(idOrInstance: string | undefined, mode: WindowMode): boolean {
  const windowId = targetWindowId(idOrInstance);
  if (!windowId) return false;
  const before = layoutStore.getSnapshot();
  return (
    layoutStore.dispatch({
      type: 'SET_WINDOW_MODE',
      windowId,
      mode,
      viewport: desktopViewport(),
    }) !== before
  );
}

/**
 * What clicking a taskbar button does, resolved from the pane's current state.
 *
 * The universal convention, and the reason this is one verb rather than the
 * caller branching: clicking the thing you are already looking at **hides** it,
 * and clicking anything else brings it forward. A taskbar where the focused
 * button does nothing is one that silently swallows every second click.
 */
/**
 * Flag a pane as wanting attention — its background work finished while it was
 * out of sight. Its taskbar button flashes until the user looks at it.
 *
 * The point of minimizing being non-destructive: a pane can be put away and keep
 * working (a research run, a training job, an ingest), so it needs a way to say it
 * is done. No-op when the pane is already the one on screen and focused — flashing
 * a button for something the user is looking at is noise.
 */
export function requestPaneAttention(instanceId: string, attention = true): boolean {
  const f = frame();
  const located = findPaneAnywhere(f, instanceId);
  if (!located) return false;
  if (attention) {
    const entry = taskbarEntries(f).find((e) => e.instanceId === instanceId);
    if (entry?.state === 'focused') return false;
  }
  const before = layoutStore.getSnapshot();
  return layoutStore.dispatch({ type: 'SET_PANE_ATTENTION', instanceId, attention }) !== before;
}

export function activateTaskbarEntry(instanceId: string): boolean {
  const f = frame();
  const located = findPaneAnywhere(f, instanceId);
  if (!located) return false;
  const { pane, location } = located;
  // Looking at it is what acknowledges it, wherever it lives and whatever the
  // click then does. Clearing it anywhere else (on focus, on a timer) would let a
  // button keep flashing after the user had plainly dealt with it.
  if (pane.attention) {
    layoutStore.dispatch({ type: 'SET_PANE_ATTENTION', instanceId, attention: false });
  }
  const win = f.windows.find((w) => w.id === location.windowId);
  const showing =
    win &&
    win.mode !== 'minimized' &&
    f.focusedWindowId === win.id &&
    win.area.tabs[win.area.activeTab]?.instanceId === instanceId;
  if (showing) return setWindowMode(win.id, 'minimized');
  focusInstance(located);
  return true;
}

/**
 * Minimize a pane's window — the taskbar's double-click gesture and its context
 * menu both land here.
 *
 * Unlike `activateTaskbarEntry` this never toggles and never focuses: "minimize"
 * asked for directly should hide the pane whether or not it happened to be the
 * focused one, so a double-click doesn't depend on what the first click of it
 * did.
 */
export function minimizePane(instanceId: string): boolean {
  const located = findPaneAnywhere(frame(), instanceId);
  return located ? setWindowMode(located.location.windowId, 'minimized') : false;
}

/** Minimize ⇄ restore, the taskbar-button behaviour. */
export function toggleWindowMinimized(idOrInstance?: string): boolean {
  const windowId = targetWindowId(idOrInstance);
  const win = windowId ? frame().windows.find((w) => w.id === windowId) : null;
  if (!win) return false;
  return setWindowMode(win.id, win.mode === 'minimized' ? 'normal' : 'minimized');
}

/** Maximize ⇄ restore, the titlebar double-click behaviour. */
export function toggleWindowMaximized(idOrInstance?: string): boolean {
  const windowId = targetWindowId(idOrInstance);
  const win = windowId ? frame().windows.find((w) => w.id === windowId) : null;
  if (!win) return false;
  return setWindowMode(win.id, win.mode === 'maximized' ? 'normal' : 'maximized');
}

export function focusWindow(idOrInstance?: string): boolean {
  const windowId = targetWindowId(idOrInstance);
  if (!windowId) return false;
  layoutStore.dispatch({ type: 'BRING_WINDOW_FRONT', windowId });
  layoutStore.dispatch({ type: 'FOCUS_WINDOW', windowId });
  const win = frame().windows.find((w) => w.id === windowId);
  if (win?.mode === 'minimized') setWindowMode(windowId, 'normal');
  const pane = win?.area.tabs[win.area.activeTab];
  if (pane) {
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: pane.instanceId });
    queueMicrotask(() => focusPaneDom(pane.instanceId));
  }
  return true;
}

/**
 * Cycle window focus in z-order. Minimized windows are included: they are exactly
 * what a window switcher exists to bring back, and skipping them would make a
 * minimized window reachable only with the mouse.
 */
export function cycleWindows(direction: 1 | -1 = 1): boolean {
  const f = frame();
  const ordered = [...f.windows].sort((a, b) => b.z - a.z);
  if (ordered.length === 0) return false;
  const current = ordered.findIndex((w) => w.id === f.focusedWindowId);
  const next = ordered[(current + direction + ordered.length) % ordered.length];
  return focusWindow(next.id);
}

/** Move focus to the window nearest in a viewport direction (the i3 gesture). */
export function focusWindowDirection(direction: NavDirection): boolean {
  const f = frame();
  const from = f.windows.find((w) => w.id === f.focusedWindowId) ?? f.windows[0];
  if (!from) return false;
  const axis = direction === 'left' || direction === 'right' ? 'x' : 'y';
  const sign = direction === 'left' || direction === 'up' ? -1 : 1;
  const centerOf = (r: { x: number; y: number; w: number; h: number }) => ({
    x: r.x + r.w / 2,
    y: r.y + r.h / 2,
  });
  const origin = centerOf(from.rect);
  const best = f.windows
    .filter((w) => w.id !== from.id && w.mode !== 'minimized')
    .map((w) => ({ w, c: centerOf(w.rect) }))
    // Only windows genuinely on that side, then the nearest of them — measuring
    // by raw distance alone would jump to a window that is mostly perpendicular.
    .filter(({ c }) => (c[axis] - origin[axis]) * sign > 1)
    .sort(
      (a, b) =>
        Math.abs(a.c[axis] - origin[axis]) - Math.abs(b.c[axis] - origin[axis]) ||
        Math.abs(a.c[axis === 'x' ? 'y' : 'x'] - origin[axis === 'x' ? 'y' : 'x']) -
          Math.abs(b.c[axis === 'x' ? 'y' : 'x'] - origin[axis === 'x' ? 'y' : 'x']),
    )[0];
  return best ? focusWindow(best.w.id) : false;
}

/** Lay every window out at once (the desktop context menu and `desktop.arrange`). */
export function arrangeDesktop(style: ArrangeStyle): boolean {
  const f = frame();
  const open = f.windows.filter((w) => w.mode !== 'minimized');
  if (open.length === 0) return false;
  const viewport = desktopViewport();
  const rects = arrangeWindows(open, viewport, style);
  open.forEach((w, i) => {
    layoutStore.dispatch({ type: 'SET_WINDOW_RECT', windowId: w.id, rect: rects[i] });
  });
  return true;
}

export function setBackdrop(backdrop: BackdropRef): boolean {
  const before = layoutStore.getSnapshot();
  return layoutStore.dispatch({ type: 'SET_BACKDROP', backdrop }) !== before;
}

/**
 * Present a pane over everything — including the taskbar, which a maximized
 * window stops short of by design.
 *
 * On a native shell, presenting a pane whose view declares `fullscreen` also
 * takes the OS window fullscreen — the point of the opt-in. A pane that has not
 * asked for the screen does not get it: promoting every presented pane to OS
 * fullscreen would make an ordinary "show me this bigger" gesture swallow the
 * user's whole display.
 */
export function presentPane(instanceId: string | null): boolean {
  const f = frame();
  if (instanceId === null) {
    if (f.presentedInstanceId) {
      layoutStore.dispatch({ type: 'SET_PRESENTED', instanceId: null });
      void restoreAppFullscreen();
    }
    return true;
  }
  const located = findPaneAnywhere(f, instanceId);
  if (!located) return false;
  layoutStore.dispatch({ type: 'SET_PRESENTED', instanceId });
  void escalateAppFullscreen(located.pane.viewId);
  return true;
}

/**
 * Whether the app was already OS-fullscreen when a presentation escalated it.
 *
 * Null means "no escalation is in flight". Without this, leaving a presented
 * game would drop an app the user had put in fullscreen themselves back into a
 * window — undoing a state this code never set.
 */
let fullscreenBeforePresent: boolean | null = null;

async function escalateAppFullscreen(viewId: string): Promise<void> {
  if (!resolveView(viewId)?.fullscreen) return;
  if (!windowControl()) return;
  fullscreenBeforePresent = await isAppFullscreen();
  if (!fullscreenBeforePresent) await setAppFullscreen(true);
}

async function restoreAppFullscreen(): Promise<void> {
  const before = fullscreenBeforePresent;
  fullscreenBeforePresent = null;
  if (before === false) await setAppFullscreen(false);
}

/** Is a pane presented? */
export function isPresenting(): boolean {
  return frame().presentedInstanceId !== null;
}

/** Toggle presentation on the focused window's showing pane. */
export function presentFocusedPane(): void {
  const f = frame();
  if (isPresenting()) {
    presentPane(null);
    return;
  }
  const win = f.focusedWindowId ? f.windows.find((w) => w.id === f.focusedWindowId) : null;
  const activeTab = win?.area.tabs[win.area.activeTab];
  if (activeTab) presentPane(activeTab.instanceId);
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

/** A compact JSON description of the desktop, for the agent's `get_layout`. */
export function describeLayout(): Record<string, unknown> {
  const f = frame();
  return {
    presentedInstanceId: f.presentedInstanceId,
    focusedWindowId: f.focusedWindowId,
    windows: f.windows.map((w) => ({
      id: w.id,
      rect: w.rect,
      mode: w.mode,
      z: w.z,
      ...(w.snap ? { snap: w.snap } : {}),
      tabs: w.area.tabs.map(describePane),
      activeTab: w.area.activeTab,
    })),
    // The appearance verbs need ids they cannot guess, and this is the read tool
    // they already call — so the catalogs ride here rather than costing two more
    // always-on `list_*` schemas.
    desktop: {
      backdrop: f.backdrop,
      backdrops: registry.backdrops.map((b) => b.id),
      themes: THEMES.map((t) => t.id),
      theme: currentThemeId(),
    },
  };
}

/**
 * The desktop's ambient agent context: a snapshot of every pane the user can
 * actually see that exposes one, so the agent knows which row the Review pane has
 * open, and which document is on screen beside it, without spending a
 * `list_open_panes` + `get_pane_context` round-trip on it.
 *
 * Budgeted by the caller, not here: `limit` caps how many panes are attached (the
 * topmost windows first — the ones the user reached for most recently) and
 * `maxChars` truncates each serialized snapshot. Unbudgeted, this would hand back the token
 * savings the gateable `layout` group just won.
 */
export function readVisibleAgentContexts(
  limit: number,
  maxChars: number,
  skipInstanceId?: string,
): Array<Record<string, unknown>> {
  const f = frame();
  const z = new Map(f.windows.map((w) => [w.id, w.z]));
  const ranked = visiblePanes(f).sort(
    (a, b) => (z.get(b.location.windowId) ?? 0) - (z.get(a.location.windowId) ?? 0),
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
      windowId: location.windowId,
      snapshot: truncateSnapshot(snapshot, maxChars),
    });
  }
  return out;
}

/**
 * Tear a tab out of its window into a window of its own. Returns false when the
 * pane is unknown or already alone in its window.
 */
export function tearOffPane(instanceId: string, rect?: WindowRect): boolean {
  const before = layoutStore.getSnapshot();
  return (
    layoutStore.dispatch({ type: 'WINDOW_FROM_PANE', instanceId, ...(rect ? { rect } : {}) }) !==
    before
  );
}

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
// LayoutController installation (the registry seam the agent relay drives)
// ---------------------------------------------------------------------------

/**
 * Keep {@link lastPlacement} in step with where windows actually are.
 *
 * A subscriber rather than a hook in the reducer or the drag handler: a window
 * gets its geometry from six places (drag, resize, snap, un-snap, maximize, an
 * agent tool), and remembering from each of them means the seventh silently does
 * not. Reading the result catches them all by construction.
 *
 * Only **single-pane** windows are recorded. A window holding three merged tabs
 * has one rect and three views, and filing that rect under each of them would
 * teach every pane in the group a placement that belonged to the group.
 * `rememberPlacement` no-ops when nothing changed, so this is a cheap walk.
 *
 * A change of the **surface** is not a placement. Resizing the app rescales every
 * window's rect once per frame; recording those wrote a setting per window per
 * frame (each one a `PUT` and an app-wide settings notification) and taught each
 * view a size the user never chose. The rect the user last gave it stays on file,
 * and opening clamps it to whatever the surface is then.
 */
function installPlacementRecorder(): void {
  if (placementRecorderBound) return;
  placementRecorderBound = true;
  // What each window looked like at the last notification. Comparing against the
  // snapshot, not against the stored placement, is what keeps a rescale out: the
  // dispatch after a resize sees rescaled rects that differ from the stored ones
  // but not from the last snapshot.
  let seen = new Map<string, Placed>();
  let viewport = layoutStore.getSnapshot().frame.windowViewport;
  layoutStore.subscribe(() => {
    const f = layoutStore.getSnapshot().frame;
    const before = seen;
    seen = new Map();
    for (const win of f.windows) {
      const placed = placedOf(win);
      if (placed) seen.set(win.id, placed);
    }
    const next = f.windowViewport;
    if (next?.w !== viewport?.w || next?.h !== viewport?.h) {
      viewport = next;
      return;
    }
    for (const [id, placed] of seen) {
      if (samePlaced(before.get(id), placed)) continue;
      rememberPlacement(placed.viewId, placed.rect, placed.snap);
    }
  });
}

interface Placed {
  viewId: string;
  rect: WindowRect;
  snap?: SnapZone;
}

/** The placement a window would record, or null if it records none. */
function placedOf(win: WindowState): Placed | null {
  // A minimized window's rect is where it will come *back* to, so it stays
  // worth recording; its `mode` is not, since reopening minimized would be a
  // pane that answers a click by not appearing.
  if (win.area.tabs.length !== 1) return null;
  // A minimized window carries its zone in `minimizedFrom`, not `snap`. Read
  // only `snap` and a maximized-then-minimized window records a full-surface
  // rect with no zone — so the view reopens the size of the screen and un-
  // snapped, which is neither what it was nor anything the user asked for.
  const snap =
    win.mode === 'minimized' && win.minimizedFrom
      ? (win.minimizedFrom.snap ?? (win.minimizedFrom.mode === 'maximized' ? 'max' : undefined))
      : win.snap;
  return { viewId: win.area.tabs[0].viewId, rect: win.rect, snap };
}

function samePlaced(a: Placed | undefined, b: Placed): boolean {
  return (
    !!a &&
    a.viewId === b.viewId &&
    a.snap === b.snap &&
    a.rect.x === b.rect.x &&
    a.rect.y === b.rect.y &&
    a.rect.w === b.rect.w &&
    a.rect.h === b.rect.h
  );
}

let placementRecorderBound = false;

/** Implements the LayoutController verbs against the layout store. Installed by
 * the shell; also wires the region command bus and the window-placement
 * recorder. */
export function installFrameController(): void {
  installPlacementRecorder();
  setFrameCommandHandler({
    togglePosition: (hostViewId, position) => {
      const instance = hostInstanceOf(hostViewId);
      if (instance) toggleRegion(instance.pane.instanceId, position);
    },
    pickView: (regionViewId) => toggleRegionView(regionViewId),
    revealSection: (section, hostViewId) => {
      revealSection(section, hostViewId);
    },
    applyBackdrop: (backdropId) => {
      setBackdrop({ id: backdropId });
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
    tearOffPane: (instanceId) => tearOffPane(instanceId),
    changePaneType: (instanceId, viewId) => {
      const decl = resolveView(viewId);
      const located = findPaneAnywhere(frame(), instanceId);
      if (!decl || !located) return false;
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
