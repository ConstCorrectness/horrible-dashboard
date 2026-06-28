import { useEffect, useRef, useState } from 'react';
import {
  DockviewReact,
  themeAbyss,
  type DockviewApi,
  type DockviewReadyEvent,
  type IDockviewPanelProps,
} from 'dockview';
import 'dockview/dist/styles/dockview.css';
import {
  createWorkspace,
  deleteWorkspace,
  getWorkspaces,
  hasAgentContext,
  PaneInstanceContext,
  PaneParamsContext,
  registry,
  saveWorkspace,
  setActiveWorkspace,
  workspaceStore,
  type LayoutPreset,
  type OpenPaneOptions,
  type SerializedLayout,
  type SplitDirection,
  type Workspace as WorkspaceModel,
} from '@horrible/core';

import { PaneTab } from './PaneTab';
import { SplitHandle } from './SplitHandle';

/**
 * The dockable workspace: a collection of named layouts (tabs), each a tree of
 * splits, tab groups, and floating windows. Panes are module **panels** *or*
 * **widgets** — both resolve from the registry, so widgets are first-class
 * resizable panes (no separate widget board). The "Dashboard" is just the
 * default seeded workspace. dockview is the engine, wrapped here so the registry
 * stays the public API. See docs/architecture/windowing.md.
 */

/** A pane hosts either a panel or a widget — both have id/title/component. */
function resolveContent(id: string) {
  return registry.panels.find((p) => p.id === id) ?? registry.widgets.find((w) => w.id === id);
}

/** Single host for every pane — reads which registry entry to render from params.
 * `panelId` is reactive: the tab's type-switcher calls `updateParameters`, and we
 * re-render the new content in place (same instance id). */
function PanelHost(props: IDockviewPanelProps<{ panelId: string }>) {
  const [panelId, setPanelId] = useState(props.params.panelId);
  useEffect(() => {
    const sub = props.api.onDidParametersChange((params) => {
      const next = (params as { panelId?: string }).panelId;
      if (next) setPanelId(next);
    });
    return () => sub.dispose();
  }, [props.api]);

  const decl = resolveContent(panelId);
  if (!decl) {
    return <div className="ws-panel ws-panel-missing">Unknown pane: {panelId}</div>;
  }
  const Component = decl.component;
  // The Blender-style split grip and the agent's split_pane tool both drive the
  // same LayoutController.splitPane — duplicating this pane's content into the
  // new region (the engine assigns the duplicate a fresh instance id).
  const onSplit = (direction: SplitDirection) => {
    registry.layoutController?.splitPane(props.api.id, direction, panelId);
  };
  // Expose this pane's live instance id (for useAgentContext, keyed by instance)
  // and the params it was opened with (for usePaneParams, e.g. a buffer source).
  return (
    <PaneInstanceContext.Provider value={props.api.id}>
      <PaneParamsContext.Provider value={props.params}>
        <div className="ws-pane-host">
          <div className="ws-panel">
            <Component />
          </div>
          <SplitHandle onSplit={onSplit} />
        </div>
      </PaneParamsContext.Provider>
    </PaneInstanceContext.Provider>
  );
}

const components = { panel: PanelHost };

/** The workflow-layout preset for a workspace id, if it is one of the predefined
 * layouts (vs a custom user workspace). */
function presetFor(id: string | null): LayoutPreset | undefined {
  return id ? registry.layouts.find((p) => p.id === id) : undefined;
}

type Direction = 'left' | 'right' | 'above' | 'below' | 'within';

/** A fresh, collision-free instance id for a new pane of type `paneId` (matches
 * the `${id}#${n}` scheme `openPane` uses for non-singleton instances). */
function freshInstanceId(api: DockviewApi, paneId: string): string {
  let n = api.panels.length + 1;
  let id = `${paneId}#${n}`;
  while (api.getPanel(id)) id = `${paneId}#${++n}`;
  return id;
}

/** Map a layout `PaneDirection` to dockview's drop `Position` (used by moveTo). */
function moveToPosition(d: Direction): 'left' | 'right' | 'top' | 'bottom' | 'center' {
  switch (d) {
    case 'above':
      return 'top';
    case 'below':
      return 'bottom';
    case 'within':
      return 'center';
    default:
      return d;
  }
}

function placementDirection(placement?: string): Direction | undefined {
  switch (placement) {
    case 'left':
      return 'left';
    case 'right':
      return 'right';
    case 'bottom':
      return 'below';
    default:
      return undefined; // center → into the active group
  }
}

/** Add a pane for a panel/widget id at an optional position. */
function addPane(
  api: DockviewApi,
  id: string,
  position?: { referencePanel: string; direction: Direction },
): void {
  const decl = resolveContent(id);
  if (!decl) return;
  api.addPanel({
    id,
    component: 'panel',
    title: decl.title,
    params: { panelId: id },
    ...(position ? { position } : {}),
  });
}

/** Open a registry pane (panel or widget) — focuses an existing singleton, or an
 * explicit instance id (so reopening the same buffer focuses it). `opts.params`
 * are passed to the pane instance (read via `usePaneParams`). */
function openPane(
  api: DockviewApi,
  id: string,
  opts?: OpenPaneOptions & { floating?: boolean },
): void {
  const panel = registry.panels.find((p) => p.id === id);
  const widget = panel ? undefined : registry.widgets.find((w) => w.id === id);
  const decl = panel ?? widget;
  if (!decl) return;

  // Widgets are singleton-by-id (one "Data flow" pane); panels honor their flag.
  const singleton = panel ? Boolean(panel.singleton) : true;
  // A caller-supplied instance id is the identity (focus-or-create); otherwise a
  // singleton uses its type id and a multi-instance pane gets a fresh suffix.
  const instanceId = opts?.instanceId ?? (singleton ? id : `${id}#${api.panels.length + 1}`);
  if (singleton || opts?.instanceId) {
    const existing = api.getPanel(instanceId);
    if (existing) {
      existing.api.setActive();
      return;
    }
  }
  const direction = placementDirection(panel?.defaultPlacement ?? widget?.defaultPlacement);
  api.addPanel({
    id: instanceId,
    component: 'panel',
    title: decl.title,
    params: { panelId: id, ...(opts?.params ?? {}) },
    ...(opts?.floating ? { floating: true } : direction ? { position: { direction } } : {}),
  });
}

/** Lay out a workflow layout from its preset (replacing the current contents).
 * Each placement replays through `addPane`; unknown pane ids are skipped. */
function seedPreset(api: DockviewApi, preset: LayoutPreset): void {
  api.clear();
  for (const pane of preset.panes) addPane(api, pane.id, pane.position);
}

export function Workspace({
  pendingOpen,
  pendingWorkspace,
}: {
  pendingOpen?: { panelId: string; opts?: OpenPaneOptions; nonce: number };
  pendingWorkspace?: { workspaceId: string; nonce: number };
}) {
  const apiRef = useRef<DockviewApi | null>(null);
  const restoredRef = useRef(false);
  // Guards autosave while we programmatically replace the layout (switch/seed),
  // so onDidLayoutChange doesn't persist a half-swapped tree to the wrong id.
  const swappingRef = useRef(false);
  const activeIdRef = useRef<string | null>(null);
  const workspacesRef = useRef<WorkspaceModel[]>([]);
  const lastOpenNonce = useRef(-1);
  const lastSwitchNonce = useRef(-1);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // The rail (AppShell) renders the workspace switcher off the shared store, so
  // the Workspace publishes its list/active selection rather than holding render
  // state of its own.
  const applyState = (list: WorkspaceModel[], active: string | null) => {
    workspacesRef.current = list;
    activeIdRef.current = active;
    workspaceStore.publish({
      workspaces: list.map((w) => ({ id: w.id, name: w.name })),
      activeId: active,
    });
  };

  // Persist the active workspace's current layout. Awaitable so a following
  // re-fetch sees the write. Cancels any pending debounced autosave first: it
  // captured the (soon-to-be-old) active id but reads toJSON() at fire time, so
  // letting it run after a swap would write the next layout under the old id.
  const flushSaveCurrent = async (api: DockviewApi): Promise<void> => {
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    if (activeIdRef.current) {
      await saveWorkspace(activeIdRef.current, {
        layout: api.toJSON() as unknown as SerializedLayout,
      });
    }
  };

  const loadInto = (api: DockviewApi, ws: WorkspaceModel) => {
    swappingRef.current = true;
    // A saved layout counts only if it actually holds panes — a structurally
    // present but empty layout (`panels: {}`) re-seeds from the preset rather than
    // restoring a blank dock.
    const savedPanes = ws.layout
      ? Object.keys((ws.layout as { panels?: Record<string, unknown> }).panels ?? {}).length
      : 0;
    if (savedPanes > 0) {
      api.fromJSON(ws.layout as unknown as Parameters<typeof api.fromJSON>[0]);
    } else {
      // No usable saved layout: seed from the matching workflow preset (first
      // activation), or leave a blank canvas for a custom workspace.
      const preset = presetFor(ws.id);
      if (preset) seedPreset(api, preset);
      else api.clear();
    }
    swappingRef.current = false;
  };

  const switchTo = async (id: string) => {
    const api = apiRef.current;
    if (!api || id === activeIdRef.current) return;
    await flushSaveCurrent(api);
    if (apiRef.current !== api) return;
    // Re-fetch fresh layouts: autosaves persist to the server but don't update
    // our in-memory copies, so the target's layout here would otherwise be stale.
    let state = await getWorkspaces();
    if (apiRef.current !== api) return;
    let target = state.workspaces.find((w) => w.id === id);
    if (!target) {
      // A predefined workflow layout that's never been opened: create it with its
      // stable id (no layout yet → loadInto seeds from the preset). Non-preset
      // ids that don't exist are ignored.
      const preset = presetFor(id);
      if (!preset) return;
      await saveWorkspace(preset.id, { name: preset.name });
      if (apiRef.current !== api) return;
      state = await getWorkspaces();
      if (apiRef.current !== api) return;
      target = state.workspaces.find((w) => w.id === id);
      if (!target) return;
    }
    applyState(state.workspaces, id);
    loadInto(api, target);
    void setActiveWorkspace(id);
  };
  const switchToRef = useRef(switchTo);
  switchToRef.current = switchTo;

  // Prompt-free core, reused by the tab "＋" button and the agent's
  // create_workspace tool.
  const createNamedWorkspace = async (name: string): Promise<{ id: string; name: string }> => {
    const api = apiRef.current;
    if (api) await flushSaveCurrent(api);
    const ws = await createWorkspace(name);
    applyState([...workspacesRef.current, ws], ws.id);
    if (api) {
      swappingRef.current = true;
      api.clear();
      swappingRef.current = false;
    }
    void setActiveWorkspace(ws.id);
    return { id: ws.id, name: ws.name };
  };

  const removeWs = async (id: string) => {
    const api = apiRef.current;
    const state = await deleteWorkspace(id);
    applyState(state.workspaces, state.active);
    if (api && activeIdRef.current === id) {
      const next = state.workspaces.find((w) => w.id === state.active);
      if (next) {
        loadInto(api, next);
      } else {
        swappingRef.current = true;
        api.clear();
        swappingRef.current = false;
      }
    }
  };

  const init = async (api: DockviewApi) => {
    let state = await getWorkspaces();
    if (apiRef.current !== api) return;
    // Empty slate: seed the default workflow layout (the first preset, normally
    // Dashboard) so the app always opens onto something.
    const defaultPreset = presetFor('dashboard') ?? registry.layouts[0];
    if (state.workspaces.length === 0 && defaultPreset) {
      swappingRef.current = true;
      seedPreset(api, defaultPreset);
      swappingRef.current = false;
      await saveWorkspace(defaultPreset.id, {
        name: defaultPreset.name,
        layout: api.toJSON() as unknown as SerializedLayout,
      });
      if (apiRef.current !== api) return;
      state = await getWorkspaces();
      if (apiRef.current !== api) return;
    }
    const active = state.active ?? state.workspaces[0]?.id ?? null;
    applyState(state.workspaces, active);
    const ws = state.workspaces.find((w) => w.id === active);
    if (ws) {
      // The default preset was just seeded into the live api; only reload other
      // (non-active) workspaces.
      const justSeeded = ws.id === defaultPreset?.id && state.workspaces.length === 1;
      if (!justSeeded) loadInto(api, ws);
      if (state.active !== active) void setActiveWorkspace(active!);
    }
  };

  const onReady = (event: DockviewReadyEvent) => {
    const api = event.api;
    apiRef.current = api;
    restoredRef.current = false;
    lastOpenNonce.current = -1;
    lastSwitchNonce.current = -1;

    if (import.meta.env.DEV) {
      (window as Window & { __horribleWorkspace?: DockviewApi }).__horribleWorkspace = api;
    }

    // Expose layout mutations the agent orchestrator drives (close/list/create).
    registry.setLayoutController({
      closePane: (id) => {
        const panel =
          api.getPanel(id) ??
          api.panels.find((p) => (p.params as { panelId?: string })?.panelId === id);
        if (panel) {
          panel.api.close();
          return true;
        }
        return false;
      },
      focusPane: (instanceId) => {
        const panel = api.getPanel(instanceId);
        if (panel) {
          panel.api.setActive();
          return true;
        }
        return false;
      },
      listOpenPanes: () =>
        api.panels.map((p) => ({
          id: (p.params as { panelId?: string })?.panelId ?? p.id,
          instanceId: p.id,
          title: p.title ?? p.id,
          hasContext: hasAgentContext(p.id),
        })),
      createWorkspace: (name) => createNamedWorkspace(name),
      listWorkspaces: async () => {
        const s = await getWorkspaces();
        return {
          active: s.active,
          workspaces: s.workspaces.map((w) => ({ id: w.id, name: w.name })),
        };
      },
      resetLayout: () => {
        const preset = presetFor(activeIdRef.current);
        if (!preset) return;
        swappingRef.current = true;
        seedPreset(api, preset);
        swappingRef.current = false;
        void saveWorkspace(preset.id, {
          layout: api.toJSON() as unknown as SerializedLayout,
        });
      },
      deleteActiveWorkspace: () => {
        const id = activeIdRef.current;
        // Predefined layouts reset rather than delete; only remove custom ones.
        if (!id || presetFor(id)) return;
        void removeWs(id);
      },
      renameWorkspace: async (id, name) => {
        await saveWorkspace(id, { name });
        const s = await getWorkspaces();
        applyState(s.workspaces, activeIdRef.current);
      },
      deleteWorkspace: async (id) => {
        if (presetFor(id)) return;
        await removeWs(id);
      },
      splitPane: (instanceId, direction, viewId) => {
        const ref = api.getPanel(instanceId);
        if (!ref) return null;
        // Default to duplicating the source pane's own view (matches the corner-grip
        // split) when no explicit viewId is given — the natural "split this pane".
        const targetViewId = viewId ?? (ref.params?.panelId as string | undefined);
        const decl = targetViewId ? resolveContent(targetViewId) : undefined;
        if (!targetViewId || !decl) return null;
        const newId = freshInstanceId(api, targetViewId);
        api.addPanel({
          id: newId,
          component: 'panel',
          title: decl.title,
          params: { panelId: targetViewId },
          position: { referencePanel: instanceId, direction },
        });
        return newId;
      },
      resizePane: (instanceId, size) => {
        const panel = api.getPanel(instanceId);
        if (!panel) return false;
        panel.api.setSize(size);
        return true;
      },
      movePane: (instanceId, referenceInstanceId, direction) => {
        const panel = api.getPanel(instanceId);
        const ref = api.getPanel(referenceInstanceId);
        if (!panel || !ref) return false;
        panel.api.moveTo({ group: ref.group, position: moveToPosition(direction) });
        return true;
      },
      setPaneFloating: (instanceId, floating) => {
        const panel = api.getPanel(instanceId);
        if (!panel) return false;
        const isFloating = panel.api.location.type === 'floating';
        if (floating === isFloating) return false;
        if (floating) {
          api.addFloatingGroup(panel);
        } else {
          // Dock back into an existing grid group, or a fresh one if none remain.
          const grid = api.panels.find(
            (p) => p.id !== instanceId && p.api.location.type === 'grid',
          );
          panel.api.moveTo(
            grid ? { group: grid.group, position: 'center' } : { group: api.addGroup() },
          );
        }
        return true;
      },
      maximizePane: (instanceId, maximized) => {
        const panel = api.getPanel(instanceId);
        if (!panel) return false;
        if (maximized) panel.api.maximize();
        else panel.api.exitMaximized();
        return true;
      },
      changePaneType: (instanceId, viewId) => {
        const panel = api.getPanel(instanceId);
        const decl = resolveContent(viewId);
        if (!panel || !decl) return false;
        // Same instance id, new content: PanelHost re-renders via onDidParametersChange.
        panel.api.updateParameters({ panelId: viewId });
        panel.api.setTitle(decl.title);
        return true;
      },
    });

    // Tab switching: clicks call switchTo directly; commands route via AppShell's
    // workspace switcher → pendingWorkspace → applyPending (so the shell also
    // enters the workspace view first).
    void init(api).finally(() => {
      if (apiRef.current !== api) return;
      restoredRef.current = true;
      applyPendingRef.current();
    });

    api.onDidLayoutChange(() => {
      if (swappingRef.current || !restoredRef.current) return;
      const id = activeIdRef.current;
      if (!id) return;
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        // If the active workspace changed before this fired, the swap path
        // already saved the correct layout — don't write stale data here.
        if (activeIdRef.current !== id) return;
        void saveWorkspace(id, { layout: api.toJSON() as unknown as SerializedLayout });
      }, 600);
    });
  };

  const applyPending = () => {
    const api = apiRef.current;
    if (!api || !restoredRef.current) return;
    if (pendingOpen && pendingOpen.nonce !== lastOpenNonce.current) {
      lastOpenNonce.current = pendingOpen.nonce;
      openPane(api, pendingOpen.panelId, pendingOpen.opts);
    }
    if (pendingWorkspace && pendingWorkspace.nonce !== lastSwitchNonce.current) {
      lastSwitchNonce.current = pendingWorkspace.nonce;
      void switchToRef.current(pendingWorkspace.workspaceId);
    }
  };
  const applyPendingRef = useRef(applyPending);
  applyPendingRef.current = applyPending;
  useEffect(applyPending, [pendingOpen, pendingWorkspace]);

  useEffect(() => {
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, []);

  return (
    <div className="ws-root">
      <DockviewReact
        className="ws-dockview"
        theme={themeAbyss}
        components={components}
        defaultTabComponent={PaneTab}
        onReady={onReady}
      />
    </div>
  );
}

/** Imperative helper for commands (e.g. "open terminal in a floating window"). */
export { openPane as openWorkspacePanel };
