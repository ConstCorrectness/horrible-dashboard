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
  registry,
  saveWorkspace,
  setActiveWorkspace,
  type SerializedLayout,
  type Workspace as WorkspaceModel,
} from '@horrible/core';

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

/** Single host for every pane — reads which registry entry to render from params. */
function PanelHost(props: IDockviewPanelProps<{ panelId: string }>) {
  const decl = resolveContent(props.params.panelId);
  if (!decl) {
    return <div className="ws-panel ws-panel-missing">Unknown pane: {props.params.panelId}</div>;
  }
  const Component = decl.component;
  return (
    <div className="ws-panel">
      <Component />
    </div>
  );
}

const components = { panel: PanelHost };

const DASHBOARD_PRESET = ['dashboard.welcome', 'dashboard.backendStatus', 'observability.io'];

type Direction = 'left' | 'right' | 'above' | 'below' | 'within';

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

/** Open a registry pane (panel or widget) — focuses an existing singleton. */
function openPane(api: DockviewApi, id: string, floating = false): void {
  const panel = registry.panels.find((p) => p.id === id);
  const widget = panel ? undefined : registry.widgets.find((w) => w.id === id);
  const decl = panel ?? widget;
  if (!decl) return;

  // Widgets are singleton-by-id (one "Data flow" pane); panels honor their flag.
  const singleton = panel ? Boolean(panel.singleton) : true;
  if (singleton) {
    const existing = api.getPanel(id);
    if (existing) {
      existing.api.setActive();
      return;
    }
  }
  const instanceId = singleton ? id : `${id}#${api.panels.length + 1}`;
  const direction = placementDirection(panel?.defaultPlacement ?? widget?.defaultPlacement);
  api.addPanel({
    id: instanceId,
    component: 'panel',
    title: decl.title,
    params: { panelId: id },
    ...(floating ? { floating: true } : direction ? { position: { direction } } : {}),
  });
}

/** Build the default Dashboard arrangement (a 2-column grid of common widgets). */
function seedDashboard(api: DockviewApi): void {
  api.clear();
  const [first, ...rest] = DASHBOARD_PRESET;
  addPane(api, first);
  if (rest[0]) addPane(api, rest[0], { referencePanel: first, direction: 'right' });
  if (rest[1]) addPane(api, rest[1], { referencePanel: first, direction: 'below' });
  for (const extra of rest.slice(2)) addPane(api, extra);
}

interface WorkspaceTabsProps {
  workspaces: WorkspaceModel[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
  onAddWidget: (id: string) => void;
}

/** The tab strip above the dock: one tab per workspace + create + add-widget. */
function WorkspaceTabs(props: WorkspaceTabsProps) {
  const addable = registry.widgets;
  return (
    <div className="ws-tabs">
      {props.workspaces.map((ws) => (
        <div
          key={ws.id}
          className={`ws-tab ${ws.id === props.activeId ? 'active' : ''}`}
          onClick={() => props.onSelect(ws.id)}
        >
          <span>{ws.name}</span>
          {props.workspaces.length > 1 && (
            <button
              className="ws-tab-close"
              title={`Delete ${ws.name}`}
              onClick={(e) => {
                e.stopPropagation();
                props.onDelete(ws.id);
              }}
            >
              ×
            </button>
          )}
        </div>
      ))}
      <button className="ws-tab-add" title="New workspace" onClick={props.onCreate}>
        ＋
      </button>
      <div className="ws-tabs-spacer" />
      {addable.length > 0 && (
        <select
          className="ws-widget-picker"
          value=""
          onChange={(e) => {
            if (e.target.value) props.onAddWidget(e.target.value);
            e.target.value = '';
          }}
        >
          <option value="">Add widget…</option>
          {addable.map((w) => (
            <option key={w.id} value={w.id}>
              {w.title}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

export function Workspace({
  pendingOpen,
  pendingWorkspace,
}: {
  pendingOpen?: { panelId: string; nonce: number };
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

  const [workspaces, setWorkspaces] = useState<WorkspaceModel[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);

  const applyState = (list: WorkspaceModel[], active: string | null) => {
    workspacesRef.current = list;
    activeIdRef.current = active;
    setWorkspaces(list);
    setActiveId(active);
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
    if (ws.layout && Object.keys(ws.layout).length > 0) {
      api.fromJSON(ws.layout as unknown as Parameters<typeof api.fromJSON>[0]);
    } else if (ws.id === 'dashboard') {
      seedDashboard(api);
    } else {
      api.clear();
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
    const state = await getWorkspaces();
    if (apiRef.current !== api) return;
    const target = state.workspaces.find((w) => w.id === id);
    if (!target) return;
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

  const createWs = async () => {
    const name = window.prompt('New workspace name', 'Workspace');
    if (name) await createNamedWorkspace(name);
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
    if (state.workspaces.length === 0) {
      swappingRef.current = true;
      seedDashboard(api);
      swappingRef.current = false;
      await saveWorkspace('dashboard', {
        name: 'Dashboard',
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
      // Dashboard was just seeded into the live api; only reload non-active swaps.
      if (!(ws.id === 'dashboard' && state.workspaces.length === 1)) loadInto(api, ws);
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
      listOpenPanes: () =>
        api.panels.map((p) => ({
          id: (p.params as { panelId?: string })?.panelId ?? p.id,
          title: p.title ?? p.id,
        })),
      createWorkspace: (name) => createNamedWorkspace(name),
      listWorkspaces: async () => {
        const s = await getWorkspaces();
        return {
          active: s.active,
          workspaces: s.workspaces.map((w) => ({ id: w.id, name: w.name })),
        };
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
      openPane(api, pendingOpen.panelId);
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
      <WorkspaceTabs
        workspaces={workspaces}
        activeId={activeId}
        onSelect={(id) => void switchTo(id)}
        onCreate={() => void createWs()}
        onDelete={(id) => void removeWs(id)}
        onAddWidget={(id) => {
          const api = apiRef.current;
          if (api) openPane(api, id);
        }}
      />
      <DockviewReact
        className="ws-dockview"
        theme={themeAbyss}
        components={components}
        onReady={onReady}
      />
    </div>
  );
}

/** Imperative helper for commands (e.g. "open terminal in a floating window"). */
export { openPane as openWorkspacePanel };
