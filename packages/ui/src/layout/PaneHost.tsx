/**
 * Single host for every pane instance in the frame engine — the port of the legacy
 * engine's PanelHost with the module-facing contract unchanged: the component
 * resolves from the registry, `PaneInstanceContext` exposes the live instance id
 * (agent context is keyed by it), `PaneParamsContext` exposes the open params,
 * and focusing the pane makes its view id the active keybinding scope.
 */
import { useEffect, useMemo } from 'react';
import {
  clearActiveScope,
  layoutStore,
  PaneInstanceContext,
  PaneParamsContext,
  resolveView,
  setActiveScope,
  type PaneState,
} from '@horrible/core';

export function PaneHost({ pane, areaId }: { pane: PaneState; areaId?: string }) {
  const { viewId, instanceId, params } = pane;

  // Drop this view as the active keybinding scope when the pane unmounts.
  useEffect(() => () => clearActiveScope(viewId), [viewId]);

  // Legacy panes read `panelId` off their params (the old engine always set it).
  const paramsValue = useMemo(() => ({ panelId: viewId, ...(params ?? {}) }), [viewId, params]);

  const decl = resolveView(viewId);
  if (!decl) {
    return <div className="frame-pane-missing">Unknown pane: {viewId}</div>;
  }
  const Component = decl.component;

  // Focus (any descendant) and pointerdown both count — a click on non-focusable
  // pane content still selects the pane and its area.
  const markActive = () => {
    setActiveScope(viewId);
    if (areaId) layoutStore.dispatch({ type: 'FOCUS_AREA', areaId });
  };

  return (
    <PaneInstanceContext.Provider value={instanceId}>
      <PaneParamsContext.Provider value={paramsValue}>
        <div
          className="frame-pane-host"
          onFocusCapture={markActive}
          onPointerDownCapture={markActive}
        >
          <Component />
        </div>
      </PaneParamsContext.Provider>
    </PaneInstanceContext.Provider>
  );
}
