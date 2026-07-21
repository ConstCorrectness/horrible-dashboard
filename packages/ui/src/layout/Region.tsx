/**
 * Region strips — the engine-native successor of PaneGroupShell's companion
 * docks (Blender N/T-panel style). A region's full state (open, size, collapsed,
 * stacked views, active view) lives on its host PaneState in the layout store,
 * so it persists with the workspace and each pane instance keeps its own.
 * `PaneWithRegions` wraps a pane's content with its three strips and is shared
 * by center areas and docks.
 */
import {
  collapseRegion,
  layoutStore,
  paneDrag,
  resolveView,
  setRegionView,
  toggleRegion,
  type PaneState,
  type RegionPosition,
  type RegionState,
  type RegionViewDecl,
} from '@horrible/core';

import { PaneHost } from './PaneHost';

const MIN_SIZE = 120;
const MAX_SIZE: Record<RegionPosition, number> = { left: 700, right: 700, bottom: 480 };

const COLLAPSE_ICON: Record<RegionPosition, string> = { right: '»', bottom: '⤓', left: '«' };
const EXPAND_ICON: Record<RegionPosition, string> = { right: '«', bottom: '⤒', left: '»' };
const POSITION_KEY: Record<RegionPosition, string> = { left: 't', right: 'n', bottom: 'b' };

/** The host view's region declarations at one position (labels, icons, keys). */
function declsAt(hostViewId: string, position: RegionPosition): RegionViewDecl[] {
  return (resolveView(hostViewId)?.regions ?? []).filter(
    (r) => (r.position ?? 'right') === position,
  );
}

export function Region({ pane, position }: { pane: PaneState; position: RegionPosition }) {
  const region = pane.regions?.[position];
  if (!region?.open) return null;
  const vertical = position !== 'bottom';
  const decls = declsAt(pane.viewId, position);
  const declFor = (id: string): RegionViewDecl | undefined => decls.find((d) => d.id === id);

  if (region.collapsed) {
    return (
      <button
        className={`frame-region-rail frame-region-rail--${position}`}
        title={`Show ${position} region (${POSITION_KEY[position]})`}
        aria-label={`Show ${position} region`}
        onClick={() => collapseRegion(pane.instanceId, position)}
      >
        <span className="frame-region-rail-glyph">{EXPAND_ICON[position]}</span>
        {region.views.map((id) => (
          <span key={id} className="frame-region-rail-tab">
            {declFor(id)?.icon ?? (resolveView(id)?.title ?? id)[0]}
          </span>
        ))}
      </button>
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
    const onMove = (me: PointerEvent) => {
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
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
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

  const content =
    position === 'right' && region.views.length > 1 ? (
      <div
        className="frame-region-content"
        style={{
          display: 'flex',
          flexDirection: 'column',
          height: '100%',
          minWidth: 0,
          minHeight: 0,
        }}
      >
        {region.views.map((id, index) => {
          const decl = declFor(id);
          const title = decl?.label ?? resolveView(id)?.title ?? id;
          return (
            <div
              key={id}
              className="frame-region-section"
              style={{
                display: 'flex',
                flexDirection: 'column',
                flex: 1,
                minHeight: 0,
                borderBottom: index < region.views.length - 1 ? '1px solid var(--border)' : 'none',
              }}
            >
              <div
                className="frame-region-header"
                style={{ flex: 'none', background: 'var(--bg-raised)' }}
              >
                {decl?.icon ? <span>{decl.icon}</span> : null}
                <span className="frame-region-title" style={{ fontWeight: 800 }}>
                  {title}
                </span>
                <button
                  className="frame-region-btn"
                  title={`Collapse ${position} region (${POSITION_KEY[position]})`}
                  style={{ marginLeft: 'auto' }}
                  onClick={() => collapseRegion(pane.instanceId, position)}
                >
                  {COLLAPSE_ICON[position]}
                </button>
                <button
                  className="frame-region-btn"
                  title={`Close ${title}`}
                  onClick={() => toggleRegion(pane.instanceId, position, false)}
                >
                  ✕
                </button>
              </div>
              <div
                className="frame-region-body"
                style={{ flex: 1, minHeight: 0, overflow: 'auto' }}
              >
                <PaneHost
                  pane={{
                    instanceId: `${pane.instanceId}:${position}:${id}`,
                    viewId: id,
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    ) : (
      <div className="frame-region-content">
        <div
          className="frame-region-header"
          draggable
          title="Drag into the center to open in its own area"
          onDragStart={(e) => {
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', activeTitle);
            // A `view` payload, not `pane`: the strip's content is a synthetic
            // per-host instance, not a pane the layout owns — so it opens a real
            // one where it lands, and the strip stays put.
            paneDrag.begin({ kind: 'view', viewId: region.activeView, title: activeTitle });
          }}
          onDragEnd={() => paneDrag.end()}
        >
          {region.views.length > 1 ? (
            <div className="frame-region-tabs">
              {region.views.map((id) => (
                <button
                  key={id}
                  className={`frame-region-tab${id === region.activeView ? ' active' : ''}`}
                  title={declFor(id)?.label ?? id}
                  onClick={() => setRegionView(pane.instanceId, id)}
                >
                  {declFor(id)?.icon ? <span>{declFor(id)!.icon}</span> : null}
                  <span>{declFor(id)?.label ?? resolveView(id)?.title ?? id}</span>
                </button>
              ))}
            </div>
          ) : (
            <>
              {activeDecl?.icon ? <span>{activeDecl.icon}</span> : null}
              <span className="frame-region-title">{activeTitle}</span>
            </>
          )}
          <button
            className="frame-region-btn"
            title={`Collapse ${position} region (${POSITION_KEY[position]})`}
            aria-label={`Collapse ${position} region`}
            onClick={() => collapseRegion(pane.instanceId, position)}
          >
            {COLLAPSE_ICON[position]}
          </button>
          <button
            className="frame-region-btn"
            title={`Close ${activeTitle}`}
            onClick={() => toggleRegion(pane.instanceId, position, false)}
          >
            ✕
          </button>
        </div>
        <div className="frame-region-body">
          <PaneHost
            pane={{
              // Region views get a synthetic per-host instance id, so e.g. each
              // buffer's outline keeps a distinct agent-context key.
              instanceId: `${pane.instanceId}:${position}:${region.activeView}`,
              viewId: region.activeView,
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
  const walk = (tabs: PaneState[]): RegionState | null => {
    const pane = tabs.find((t) => t.instanceId === instanceId);
    return pane?.regions?.[position] ?? null;
  };
  const search = (node: typeof frame.center): RegionState | null => {
    if (node.kind === 'area') return walk(node.tabs);
    for (const child of node.children) {
      const hit = search(child);
      if (hit) return hit;
    }
    return null;
  };
  return (
    search(frame.center) ??
    walk(frame.docks.left.tools) ??
    walk(frame.docks.right.tools) ??
    walk(frame.docks.bottom.tools) ??
    walk(frame.floating.map((f) => f.pane))
  );
}

/** A pane's content wrapped with its three region strips (center areas + docks). */
export function PaneWithRegions({ pane, areaId }: { pane: PaneState; areaId?: string }) {
  return (
    <div className="frame-pane-regions">
      <div className="frame-pane-regions-middle">
        <Region pane={pane} position="left" />
        <div className="frame-pane-regions-content">
          <PaneHost pane={pane} areaId={areaId} />
        </div>
        <Region pane={pane} position="right" />
      </div>
      <Region pane={pane} position="bottom" />
    </div>
  );
}
