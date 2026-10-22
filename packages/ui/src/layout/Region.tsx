/**
 * Region strips — the engine-native successor of PaneGroupShell's companion
 * docks (Blender N/T-panel style). A region's full state (open, size, collapsed,
 * stacked views, active view) lives on its host PaneState in the layout store,
 * so it persists with the workspace and each pane instance keeps its own.
 * `PaneWithRegions` wraps a pane's content with its three strips and is shared
 * by center areas and docks.
 */
import { memo } from 'react';
import {
  collapseRegion,
  findPaneAnywhere,
  layoutStore,
  regionAt,
  regionDisplay,
  resolveView,
  setRegionView,
  type PaneState,
  type RegionPosition,
  type RegionState,
  type RegionViewDecl,
} from '@horrible/core';

import { PaneHost } from './PaneHost';

const MIN_SIZE = 120;
const MAX_SIZE: Record<RegionPosition, number> = { left: 700, right: 700, bottom: 480 };

const COLLAPSE_ICON: Record<RegionPosition, string> = { right: '»', bottom: '⤓', left: '«' };
const POSITION_KEY: Record<RegionPosition, string> = { left: 't', right: 'n', bottom: 'b' };

/** The host view's region declarations at one position (labels, icons, keys). */
function declsAt(hostViewId: string, position: RegionPosition): RegionViewDecl[] {
  return (resolveView(hostViewId)?.regions ?? []).filter(
    (r) => (r.position ?? 'right') === position,
  );
}

export function Region({ pane, position }: { pane: PaneState; position: RegionPosition }) {
  // Resolved rather than read straight off the instance: a pane persisted before
  // its view declared this region has no `regions` of its own, and would show
  // nothing for a region added later. See `regionAt`.
  const region = regionAt(pane, position);
  const display = regionDisplay(pane, position);
  if (!region || display === 'none') return null;
  const vertical = position !== 'bottom';
  const decls = declsAt(pane.viewId, position);
  const declFor = (id: string): RegionViewDecl | undefined => decls.find((d) => d.id === id);

  // Closed *and* collapsed both draw the rail. A closed region used to draw
  // nothing at all, which is why a notebook pane declaring Metrics,
  // Architecture, Rollout, Manim and Peers showed no sign that any of them
  // existed: none of them declares `defaultOpen`, so every one started closed
  // and the only ways in were a command-palette entry and a keychord you had to
  // already know. Putting a strip away must never remove the way back.
  if (display === 'rail') {
    return (
      <div
        className={`frame-region-rail frame-region-rail--${position}`}
        role="tablist"
        aria-label={`${position} region`}
      >
        {region.views.map((id) => {
          const title = declFor(id)?.label ?? resolveView(id)?.title ?? id;
          const key = declFor(id)?.key;
          return (
            <button
              key={id}
              className="frame-region-rail-tab"
              // Each view gets its own button rather than the whole rail being
              // one: with five of them stacked, "expand to whichever was last
              // active" is a coin flip, and the icons are the only thing telling
              // you the pane has a Metrics chart at all.
              title={`Show ${title}${key ? ` (${key})` : ''}`}
              aria-label={`Show ${title}`}
              onClick={() => setRegionView(pane.instanceId, id)}
            >
              {declFor(id)?.icon ?? title[0]}
            </button>
          );
        })}
      </div>
    );
  }

  const activeDecl = declFor(region.activeView);
  const activeTitle =
    activeDecl?.label ?? resolveView(region.activeView)?.title ?? region.activeView;

  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startY = e.clientY;
    const startSize = region.size;
    let pendingEvent: PointerEvent | null = null;
    let rafId = 0;
    document.body.classList.add('is-layout-resizing');

    const processMove = () => {
      rafId = 0;
      const me = pendingEvent;
      if (!me) return;
      let next: number;
      if (position === 'right') next = startSize - (me.clientX - startX);
      else if (position === 'left') next = startSize + (me.clientX - startX);
      else next = startSize - (me.clientY - startY);
      next = Math.max(MIN_SIZE, Math.min(MAX_SIZE[position], next));
      const current = latestRegion(pane.instanceId, position);
      if (!current) return;
      layoutStore.dispatch({
        type: 'SET_REGION',
        instanceId: pane.instanceId,
        position,
        region: { ...current, size: next },
      });
    };

    const onMove = (me: PointerEvent) => {
      pendingEvent = me;
      if (!rafId) {
        rafId = requestAnimationFrame(processMove);
      }
    };
    const onUp = () => {
      document.body.classList.remove('is-layout-resizing');
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      if (rafId) {
        cancelAnimationFrame(rafId);
        rafId = 0;
      }
      if (pendingEvent) {
        processMove();
        pendingEvent = null;
      }
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const handle = (
    <div
      className={`frame-region-handle frame-region-handle--${vertical ? 'v' : 'h'}`}
      onPointerDown={startResize}
    />
  );

  // One renderer for all three positions. The right-hand strip used to have a
  // second one that stacked every view at once, and on the training notebook —
  // which declares five — that meant Metrics, Architecture, Rollout, Manim and
  // Peers each got a fifth of a 300px column, about 60px, and none of them could
  // draw anything legible. Stacking also quietly cost `activeView` its meaning.
  const content = (
    <div className="frame-region-content">
      <div className="frame-region-header">
        {region.views.length > 1 ? (
          <div className="frame-region-tabs">
            {region.views.map((id) => {
              const label = declFor(id)?.label ?? resolveView(id)?.title ?? id;
              const icon = declFor(id)?.icon;
              const active = id === region.activeView;
              return (
                <button
                  key={id}
                  className={`frame-region-tab${active ? ' active' : ''}`}
                  title={label}
                  aria-label={label}
                  onClick={() => setRegionView(pane.instanceId, id)}
                >
                  {icon ? <span>{icon}</span> : null}
                  {/* The label rides on the *active* tab only. Five icon+label
                      tabs are ~375px, which overflows the header into a scroller
                      you have to find before you can reach the last one; an
                      icon-only tab keeps its name in `title` and `aria-label`. A
                      view with no icon always shows its label — otherwise it
                      would be a blank button. */}
                  {(active || !icon) && <span>{label}</span>}
                </button>
              );
            })}
          </div>
        ) : (
          <>
            {activeDecl?.icon ? <span>{activeDecl.icon}</span> : null}
            <span className="frame-region-title">{activeTitle}</span>
          </>
        )}
        <button
          className="frame-region-btn"
          title={`Put away — reopen from the rail (${POSITION_KEY[position]})`}
          aria-label={`Put away the ${position} region`}
          onClick={() => collapseRegion(pane.instanceId, position)}
        >
          {COLLAPSE_ICON[position]}
        </button>
      </div>
      <div className="frame-region-body">
        <PaneHost
          pane={{
            // Region views get a synthetic per-host instance id, so e.g. each
            // buffer's outline keeps a distinct agent-context key.
            instanceId: `${pane.instanceId}:${position}:${region.activeView}`,
            viewId: region.activeView,
            // ...and the host's own params, because a companion strip is *about*
            // the document it rides on. Without this every strip beside the
            // training notebook was blind to which project it sat next to and
            // rendered whichever one last published on the channel — so two
            // projects open side by side overwrote each other's charts.
            params: pane.params,
          }}
        />
      </div>
    </div>
  );

  return (
    <div
      className={`frame-region frame-region--${position}`}
      style={vertical ? { width: region.size } : { height: region.size }}
    >
      {(position === 'right' || position === 'bottom') && handle}
      {content}
      {position === 'left' && handle}
    </div>
  );
}

/** Re-read the strip from the store (drag closures must not capture stale state). */
function latestRegion(instanceId: string, position: RegionPosition): RegionState | null {
  const { frame } = layoutStore.getSnapshot();
  return findPaneAnywhere(frame, instanceId)?.pane.regions?.[position] ?? null;
}

/**
 * A pane's content wrapped with its three region strips.
 *
 * Memoized because its parent re-renders on chrome changes the pane does not see:
 * a window's mode or rect. The reducers spread the window, so `pane` keeps its
 * identity through those, and without the memo every maximize re-rendered the
 * whole pane — 500ms+ for a large one (Settings).
 */
export const PaneWithRegions = memo(function PaneWithRegions({ pane }: { pane: PaneState }) {
  return (
    <div className="frame-pane-regions">
      <div className="frame-pane-regions-middle">
        <Region pane={pane} position="left" />
        <div className="frame-pane-regions-content">
          <PaneHost pane={pane} />
        </div>
        <Region pane={pane} position="right" />
      </div>
      <Region pane={pane} position="bottom" />
    </div>
  );
});
