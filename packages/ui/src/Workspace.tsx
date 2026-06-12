import { useEffect, useRef } from 'react';
import {
  DockviewReact,
  themeAbyss,
  type DockviewApi,
  type DockviewReadyEvent,
  type IDockviewPanelProps,
} from 'dockview';
import 'dockview/dist/styles/dockview.css';
import {
  getWorkspaceLayout,
  registry,
  saveWorkspaceLayout,
  type SerializedLayout,
} from '@horrible/core';

/**
 * The dockable workspace: splits, tab groups, and floating windows, all hosting
 * module panels from the registry. dockview is the interaction engine, wrapped
 * here so the registry stays the public API. See docs/architecture/windowing.md.
 */

/** Single host component for every panel — reads which registry panel to render from params. */
function PanelHost(props: IDockviewPanelProps<{ panelId: string }>) {
  const panel = registry.panels.find((p) => p.id === props.params.panelId);
  if (!panel) {
    return <div className="ws-panel ws-panel-missing">Unknown panel: {props.params.panelId}</div>;
  }
  const Component = panel.component;
  return (
    <div className="ws-panel">
      <Component />
    </div>
  );
}

const components = { panel: PanelHost };

/** Open a registry panel as a window — focuses the existing one if it's a singleton. */
function openPanel(api: DockviewApi, panelId: string, floating = false): void {
  const decl = registry.panels.find((p) => p.id === panelId);
  if (!decl) return;

  if (decl.singleton) {
    const existing = api.getPanel(panelId);
    if (existing) {
      existing.api.setActive();
      return;
    }
  }
  // Unique id per instance for non-singletons (terminal#1, terminal#2, …).
  const id = decl.singleton ? panelId : `${panelId}#${api.panels.length + 1}`;
  api.addPanel({
    id,
    component: 'panel',
    title: decl.title,
    params: { panelId },
    ...(floating ? { floating: true } : {}),
  });
}

export function Workspace({ pendingOpen }: { pendingOpen?: { panelId: string; nonce: number } }) {
  const apiRef = useRef<DockviewApi | null>(null);
  // Panels must not open before the async layout restore: api.fromJSON replaces
  // the whole layout and would wipe them (the restore wins the race).
  const restoredRef = useRef(false);
  // Track the last applied open request so we don't reopen on every render.
  const lastNonce = useRef<number>(-1);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const applyPending = () => {
    const api = apiRef.current;
    if (!api || !restoredRef.current || !pendingOpen || pendingOpen.nonce === lastNonce.current) {
      return;
    }
    lastNonce.current = pendingOpen.nonce;
    openPanel(api, pendingOpen.panelId);
  };
  // onReady's restore callback needs the latest closure, not the mount-time one.
  const applyPendingRef = useRef(applyPending);
  applyPendingRef.current = applyPending;

  useEffect(applyPending, [pendingOpen]);

  const onReady = (event: DockviewReadyEvent) => {
    const api = event.api;
    apiRef.current = api;
    // A fresh dockview instance (StrictMode re-mounts the engine) starts
    // unrestored, and any open request consumed by a discarded instance must
    // be re-applied to this one.
    restoredRef.current = false;
    lastNonce.current = -1;

    // Dev convenience: reach the live layout API from the console / preview eval.
    // The literal `import.meta.env.DEV` is what Vite statically replaces.
    if (import.meta.env.DEV) {
      (window as Window & { __horribleWorkspace?: DockviewApi }).__horribleWorkspace = api;
    }

    // Restores race across instances (StrictMode mounts the engine twice, and
    // both async restores are in flight together): only the instance that is
    // still the live one may apply its restore and mark the workspace ready.
    void getWorkspaceLayout()
      .then((layout) => {
        if (apiRef.current !== api) return;
        if (layout && Object.keys(layout).length > 0) {
          // Opaque blob from core → dockview's own shape (see SerializedLayout).
          api.fromJSON(layout as unknown as Parameters<typeof api.fromJSON>[0]);
        } else {
          // Default workspace: open the dashboard so it's never empty.
          openPanel(api, 'dashboard.home');
        }
      })
      .catch(() => {
        if (apiRef.current === api) openPanel(api, 'dashboard.home');
      })
      .finally(() => {
        if (apiRef.current !== api) return;
        restoredRef.current = true;
        applyPendingRef.current();
      });

    // Persist layout changes (debounced) so it restores next session.
    api.onDidLayoutChange(() => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        void saveWorkspaceLayout(api.toJSON() as unknown as SerializedLayout);
      }, 600);
    });
  };

  useEffect(() => {
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, []);

  return (
    <DockviewReact
      className="ws-dockview"
      theme={themeAbyss}
      components={components}
      onReady={onReady}
    />
  );
}

/** Imperative helper for commands (e.g. "open terminal in a floating window"). */
export { openPanel as openWorkspacePanel };
