/**
 * Single host for every pane instance in the frame engine — the port of the legacy
 * engine's PanelHost with the module-facing contract unchanged: the component
 * resolves from the registry, `PaneInstanceContext` exposes the live instance id
 * (agent context is keyed by it), `PaneParamsContext` exposes the open params,
 * and focusing the pane makes it the frame's focused pane — which is what the
 * keybinding service resolves `paneFocus` / `paneInstance` against.
 */
import {
  Suspense,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
} from 'react';
import {
  activeSectionOf,
  clampSize,
  getSplitSize,
  layoutStore,
  moduleOwningView,
  setModuleEnabled,
  setSplitSize,
  useModulePackages,
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

const SPLIT_MIN = 260;
const SPLIT_INITIAL = 520;

/**
 * Draggable divider between two side-by-side kept-alive sections. Writes the live
 * width straight to `--stage-left-px` on `.frame-pane-railed-body` during drag and
 * commits once on pointerup, matching `SplitPane.tsx` so 60 Hz pointer moves never
 * re-render heavy React Flow or WebGL children.
 */
function StageSplitHandle({
  splitId,
  stageRef,
}: {
  splitId: string;
  stageRef: React.RefObject<HTMLDivElement | null>;
}) {
  const [size, setSize] = useState(() => getSplitSize(splitId) ?? SPLIT_INITIAL);
  const sizeRef = useRef(size);
  sizeRef.current = size;

  const paint = useCallback(
    (px: number) => {
      stageRef.current?.style.setProperty('--stage-left-px', `${px}px`);
    },
    [stageRef],
  );

  useLayoutEffect(() => paint(size), [paint, size]);
  useEffect(() => {
    setSize(getSplitSize(splitId) ?? SPLIT_INITIAL);
  }, [splitId]);

  const commit = useCallback(
    (next: number) => {
      sizeRef.current = next;
      setSize(next);
      setSplitSize(splitId, next);
    },
    [splitId],
  );

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const span = stageRef.current?.getBoundingClientRect().width ?? 0;
    if (span <= 0) return;
    const handle = event.currentTarget;
    handle.setPointerCapture(event.pointerId);
    const origin = event.clientX;
    const from = sizeRef.current;
    let latest = from;

    const onMove = (move: PointerEvent) => {
      latest = clampSize(from + (move.clientX - origin), span, SPLIT_MIN, SPLIT_MIN);
      paint(latest);
    };
    const onUp = () => {
      handle.releasePointerCapture(event.pointerId);
      handle.removeEventListener('pointermove', onMove);
      handle.removeEventListener('pointerup', onUp);
      handle.removeEventListener('pointercancel', onUp);
      commit(latest);
    };
    handle.addEventListener('pointermove', onMove);
    handle.addEventListener('pointerup', onUp);
    handle.addEventListener('pointercancel', onUp);
  };

  return (
    <div
      className="frame-stage-split-handle"
      role="separator"
      tabIndex={0}
      aria-orientation="vertical"
      aria-label="Split stage width"
      style={{ order: 2 }}
      onPointerDown={onPointerDown}
      onDoubleClick={() => {
        const span = stageRef.current?.getBoundingClientRect().width ?? SPLIT_INITIAL * 2;
        commit(clampSize(Math.round(span / 2), span, SPLIT_MIN, SPLIT_MIN));
      }}
      title="Drag to resize split · double-click to 50/50"
    />
  );
}

/**
 * A saved pane whose view is not registered. Usually because its module is
 * disabled (or still loading, or failed to): then it says which module and offers
 * to enable it — the pane comes back in place, keeping its window and params,
 * because the registry change re-renders the desktop. Removing the node instead
 * would lose the user's arrangement over a toggle they may flip straight back.
 */
function MissingPane({ viewId }: { viewId: string }) {
  useModulePackages(); // re-render as module state changes
  const owner = moduleOwningView(viewId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!owner) return <div className="frame-pane-missing">Unknown pane: {viewId}</div>;
  const enable = async () => {
    setBusy(true);
    setError(null);
    try {
      await setModuleEnabled(owner.id, true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  // A module another process on the module bus holds is not a failure: say where it is.
  const reason = owner.error
    ? / is running in /.test(owner.error)
      ? owner.error.replace(/^\S+ is running in /, 'is running in ')
      : `failed to load: ${owner.error}`
    : owner.enabled
      ? 'is loading'
      : 'is disabled';
  return (
    <div className="frame-pane-missing frame-pane-disabled">
      <span className="frame-pane-disabled-title">{owner.name}</span>
      <span className="frame-pane-disabled-meta">
        module {owner.id} {reason}
      </span>
      {!owner.enabled && (
        <button disabled={busy} onClick={() => void enable()}>
          Enable {owner.name}
        </button>
      )}
      {error && <span className="frame-pane-disabled-meta">{error}</span>}
    </div>
  );
}

export function PaneHost({ pane }: { pane: PaneState }) {
  const { viewId, instanceId, params } = pane;

  // Legacy panes read `panelId` off their params (the old engine always set it).
  const paramsValue = useMemo(() => ({ panelId: viewId, ...(params ?? {}) }), [viewId, params]);
  // Sections a keep-alive pane has shown since it mounted. Mounting lazily is the
  // point: opening the pane must not boot every section's canvases at once.
  const visited = useRef(new Set<string>());
  const stageRef = useRef<HTMLDivElement>(null);

  const decl = resolveView(viewId);
  if (!decl) {
    return <MissingPane viewId={viewId} />;
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

  // Side-by-side companion section (`allowSectionSplit`). Both slots stay mounted at
  // their flat `[s.id]` fiber keys and are positioned via CSS flex `order`, so
  // turning split on/off or clicking between left and right halves never unmounts
  // WebGL or graph canvases.
  const rawSplit =
    keepAlive && decl.allowSectionSplit && typeof params?.splitSection === 'string'
      ? params.splitSection
      : undefined;
  const splitSection =
    rawSplit && rawSplit !== activeSection && sections.some((s) => s.id === rawSplit)
      ? rawSplit
      : undefined;
  const primarySide: 'left' | 'right' = params?.splitPrimarySide === 'right' ? 'right' : 'left';

  if (keepAlive && activeSection) visited.current.add(activeSection);
  if (keepAlive && splitSection) visited.current.add(splitSection);
  const rail = decl.sectionsLayout === 'rail' && sections.length > 1;
  const StageHeader = decl.stageHeader;

  // Focusing the secondary split slot makes it `activeSection` (so `paneSection`
  // keybindings and agent context follow focus immediately) while flipping
  // `splitPrimarySide` so neither pane jumps across the divider.
  const focusSplitSlot = (targetSection: string) => {
    if (!splitSection || !activeSection || targetSection === activeSection) return;
    const nextSide: 'left' | 'right' = primarySide === 'left' ? 'right' : 'left';
    layoutStore.dispatch({
      type: 'SET_PANE_PARAMS',
      instanceId,
      params: {
        ...(params ?? {}),
        splitSection: activeSection,
        splitPrimarySide: nextSide,
      },
    });
    setPaneSection(instanceId, targetSection);
  };

  const pickSection = (id: string) => {
    if (splitSection && id === splitSection) {
      focusSplitSlot(id);
      return;
    }
    setPaneSection(instanceId, id);
  };

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
      const isSplit = Boolean(splitSection && activeSection);
      const leftId = isSplit ? (primarySide === 'left' ? activeSection : splitSection) : undefined;
      const rightId = isSplit ? (primarySide === 'left' ? splitSection : activeSection) : undefined;

      // Every visited section stays mounted; non-split active uses `display: contents`
      // while split mode places the two active slots side-by-side via flex order.
      return (
        <>
          {sections
            .filter((s) => visited.current.has(s.id))
            .map((s) => {
              const Body = bodyOfSection(s);
              if (!Body) return null;
              const isPrimary = s.id === activeSection;
              const isSecondary = isSplit && s.id === splitSection;
              const shown = isPrimary || isSecondary;
              const isLeft = isSplit && s.id === leftId;
              const isRight = isSplit && s.id === rightId;
              return (
                <div
                  key={s.id}
                  className={
                    `frame-section-keep` +
                    (isLeft ? ' is-split-left' : '') +
                    (isRight ? ' is-split-right' : '') +
                    (isSplit && isPrimary ? ' is-split-focused' : '')
                  }
                  style={{
                    display: !shown ? 'none' : isSplit ? 'flex' : 'contents',
                    order: isLeft ? 1 : isRight ? 3 : 0,
                  }}
                  onPointerDownCapture={isSecondary ? () => focusSplitSlot(s.id) : undefined}
                  onFocusCapture={isSecondary ? () => focusSplitSlot(s.id) : undefined}
                >
                  <SectionInstanceContext.Provider value={s.id}>
                    <Suspense fallback={<PaneLoading />}>
                      <Body />
                    </Suspense>
                  </SectionInstanceContext.Provider>
                </div>
              );
            })}
          {isSplit ? (
            <StageSplitHandle splitId={`${viewId}:stage-split`} stageRef={stageRef} />
          ) : null}
        </>
      );
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

  const isSplitStage = Boolean(keepAlive && splitSection && activeSection);

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
                secondaryActive={splitSection}
                onPick={pickSection}
              />
              <div className="frame-pane-railed-main">
                {StageHeader ? <StageHeader /> : null}
                <div
                  ref={stageRef}
                  className={`frame-pane-railed-body${isSplitStage ? ' frame-pane-railed-body--split' : ''}`}
                >
                  {body()}
                </div>
              </div>
            </div>
          ) : (
            <>
              <SectionTabs sections={sections} active={activeSection} onPick={pickSection} />
              {body()}
            </>
          )}
        </div>
      </PaneParamsContext.Provider>
    </PaneInstanceContext.Provider>
  );
}
