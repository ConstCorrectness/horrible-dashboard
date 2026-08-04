/**
 * Single host for every pane instance in the frame engine — the port of the legacy
 * engine's PanelHost with the module-facing contract unchanged: the component
 * resolves from the registry, `PaneInstanceContext` exposes the live instance id
 * (agent context is keyed by it), `PaneParamsContext` exposes the open params,
 * and focusing the pane makes it the frame's focused pane — which is what the
 * keybinding service resolves `paneFocus` / `paneInstance` against.
 */
import { useMemo } from 'react';
import {
  activeSectionOf,
  layoutStore,
  PaneInstanceContext,
  PaneParamsContext,
  requestCapture,
  resolveView,
  setPaneSection,
  type PaneCaptureDecl,
  type PaneState,
  type SectionDecl,
} from '@horrible/core';

import { SectionTabs } from './SectionTabs';

/**
 * A view's declared capture, with the deprecated `editor: true` mapped onto it —
 * "needs the plain letters" is exactly `keyboard` capture, so the two concepts
 * are one now rather than two flags the resolver had to consult separately.
 */
function declaredCapture(decl: {
  capture?: PaneCaptureDecl;
  editor?: boolean;
}): PaneCaptureDecl | null {
  if (decl.capture) return decl.capture;
  return decl.editor ? { mode: 'keyboard', escape: 'passthrough' } : null;
}

/** The component of a registered view named by `SectionDecl.view`. */
function bodyOfView(viewId: string | undefined) {
  return viewId ? resolveView(viewId)?.component : undefined;
}

export function PaneHost({ pane, areaId }: { pane: PaneState; areaId?: string }) {
  const { viewId, instanceId, params } = pane;

  // Legacy panes read `panelId` off their params (the old engine always set it).
  const paramsValue = useMemo(() => ({ panelId: viewId, ...(params ?? {}) }), [viewId, params]);

  const decl = resolveView(viewId);
  if (!decl) {
    return <div className="frame-pane-missing">Unknown pane: {viewId}</div>;
  }

  // Sections: the strip is host chrome, but the *body* is the module's choice.
  // A section that declares its own `component`/`view` gets rendered directly; one
  // that declares neither falls through to the host's own component, which reads
  // `usePaneSection()` and switches internally. Both styles exist because a pane
  // being merged usually already switches internally, and a new one usually
  // doesn't want to.
  const sections: SectionDecl[] = decl.sections ?? [];
  const activeSection = sections.length ? activeSectionOf(pane) : undefined;
  const activeDecl = sections.find((s) => s.id === activeSection);
  const sectionBody = activeDecl?.component ?? bodyOfView(activeDecl?.view);
  const Component = sectionBody ?? decl.component;

  // Focus (any descendant) and pointerdown both count — a click on non-focusable
  // pane content still selects the pane. FOCUS_PANE resolves the owning area
  // itself, so a docked or floating pane no longer leaves the focused area
  // pointing at whatever center pane happened to be clicked last.
  const markActive = () => {
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId });
    if (areaId) layoutStore.dispatch({ type: 'FOCUS_AREA', areaId });
    // Declarative capture follows focus. Panes that capture conditionally (a
    // game, once pointer-locked) declare nothing and request it themselves; the
    // store releases either kind as soon as focus moves on.
    const wanted = declaredCapture(decl);
    if (wanted) {
      requestCapture({
        mode: wanted.mode,
        escape: wanted.escape ?? 'release',
        instanceId,
        viewId,
      });
    }
  };

  return (
    <PaneInstanceContext.Provider value={instanceId}>
      <PaneParamsContext.Provider value={paramsValue}>
        {/* `data-pane-instance` + tabIndex=-1 let `focusPaneDom` move the real
            caret here on area navigation, without adding the container to the
            tab order. A pane can nominate a better target with `data-autofocus`. */}
        <div
          className="frame-pane-host"
          data-pane-instance={instanceId}
          tabIndex={-1}
          onFocusCapture={markActive}
          onPointerDownCapture={markActive}
        >
          <SectionTabs
            sections={sections}
            active={activeSection}
            onPick={(id) => setPaneSection(instanceId, id)}
          />
          <Component />
        </div>
      </PaneParamsContext.Provider>
    </PaneInstanceContext.Provider>
  );
}
