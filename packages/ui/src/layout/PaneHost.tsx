/**
 * Single host for every pane instance in the frame engine — the port of the legacy
 * engine's PanelHost with the module-facing contract unchanged: the component
 * resolves from the registry, `PaneInstanceContext` exposes the live instance id
 * (agent context is keyed by it), `PaneParamsContext` exposes the open params,
 * and focusing the pane makes it the frame's focused pane — which is what the
 * keybinding service resolves `paneFocus` / `paneInstance` against.
 */
import { Suspense, useMemo, useRef, type ComponentType } from 'react';
import {
  activeSectionOf,
  layoutStore,
  PaneInstanceContext,
  PaneParamsContext,
  requestCapture,
  resolveView,
  SectionInstanceContext,
  setPaneSection,
  type PaneCaptureDecl,
  type PaneState,
  type SectionDecl,
} from '@horrible/core';

import { SectionRail } from './SectionRail';
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

function PaneLoading() {
  return (
    <div
      className="frame-pane-loading"
      style={{
        padding: '1.25rem',
        color: 'var(--text-dim)',
        fontSize: '0.85rem',
      }}
    >
      Loading…
    </div>
  );
}

/** A section body, or undefined when the section falls through to the pane's own. */
function bodyOfSection(s: SectionDecl | undefined): ComponentType | undefined {
  return s?.component ?? bodyOfView(s?.view);
}

export function PaneHost({ pane }: { pane: PaneState }) {
  const { viewId, instanceId, params } = pane;

  // Legacy panes read `panelId` off their params (the old engine always set it).
  const paramsValue = useMemo(() => ({ panelId: viewId, ...(params ?? {}) }), [viewId, params]);
  // Sections a keep-alive pane has shown since it mounted. Mounting lazily is the
  // point: opening the pane must not boot every section's canvases at once.
  const visited = useRef(new Set<string>());

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
  const sectionBody = bodyOfSection(activeDecl);
  const Component = sectionBody ?? decl.component;
  // Keep-alive only covers sections with a body of their own: one that falls
  // through to the pane's component has nothing separate to keep.
  const keepAlive = Boolean(decl.sectionsKeepAlive) && Boolean(sectionBody);
  if (keepAlive && activeSection) visited.current.add(activeSection);
  const rail = decl.sectionsLayout === 'rail' && sections.length > 1;

  // Focus (any descendant) and pointerdown both count — a click on non-focusable
  // pane content still selects the pane.
  const markActive = () => {
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId });
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

  function body() {
    if (keepAlive) {
      // Every visited section stays mounted; only the active one is displayed.
      // `display: contents` keeps the active body a direct flex child of the host,
      // exactly where a non-kept body would sit.
      return sections
        .filter((s) => visited.current.has(s.id))
        .map((s) => {
          const Body = bodyOfSection(s);
          if (!Body) return null;
          return (
            <div
              key={s.id}
              className="frame-section-keep"
              style={{ display: s.id === activeSection ? 'contents' : 'none' }}
            >
              <SectionInstanceContext.Provider value={s.id}>
                <Suspense fallback={<PaneLoading />}>
                  <Body />
                </Suspense>
              </SectionInstanceContext.Provider>
            </div>
          );
        });
    }
    return (
      // The section slot is supplied only when *we* render the section body.
      // Falling through to the pane's own component leaves it null: that component
      // is pane-level, and stamping the active section on it would put it and any
      // body it renders internally back on one key.
      <SectionInstanceContext.Provider value={sectionBody ? (activeSection ?? null) : null}>
        <Suspense fallback={<PaneLoading />}>
          <Component />
        </Suspense>
      </SectionInstanceContext.Provider>
    );
  }

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
          {rail ? (
            <div className="frame-pane-railed">
              <SectionRail
                viewId={viewId}
                sections={sections}
                active={activeSection}
                onPick={(id) => setPaneSection(instanceId, id)}
              />
              <div className="frame-pane-railed-body">{body()}</div>
            </div>
          ) : (
            <>
              <SectionTabs
                sections={sections}
                active={activeSection}
                onPick={(id) => setPaneSection(instanceId, id)}
              />
              {body()}
            </>
          )}
        </div>
      </PaneParamsContext.Provider>
    </PaneInstanceContext.Provider>
  );
}
