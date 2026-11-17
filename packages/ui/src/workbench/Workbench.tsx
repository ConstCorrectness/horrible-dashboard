/**
 * The docked workbench: a centre split tree of tab groups, three edge docks,
 * and the dividers between them. Mounted over the desktop backdrop, which shows
 * through an empty centre — Home is what an empty workbench looks like.
 *
 * Four things here are load-bearing:
 *
 * 1. **Panes live in one flat layer, keyed by instance id**, absolutely placed
 *    over their group's body. They are not children of the group. React keeps a
 *    keyed child's DOM node when only its style changes, so moving a tab to
 *    another group, splitting, maximizing, presenting or hiding a dock moves a
 *    box on screen and never remounts the pane — an iframe keeps its page, a
 *    terminal its shell. The layer renders in **first-seen order**, never in
 *    layout order: React moves a keyed node to match a new order, and a moved
 *    `<iframe>` reloads.
 * 2. **Nothing is `display: none`.** A covered group (behind a maximized one) or
 *    a hidden dock keeps its pane mounted and sized, hidden with `visibility`.
 *    A canvas or xterm measured at 0×0 often never recovers.
 * 3. **Drags are local until pointer-up.** A sash drag lays live sizes over the
 *    frame and commits one `SET_SPLIT_SIZES` / `SET_DOCK_SIZE` at the end; a tab
 *    drag commits one `MOVE_PANE`. Dispatching per frame would bump `revision`
 *    a hundred times a drag and drive autosave into a write loop.
 * 4. **Panes render into host elements, not into their boxes.** Each mounted pane
 *    is portaled into a host element of its own (./pane-hosts.tsx), and the
 *    positioned box in the layer — or a pop-out window's body (PopoutHost.tsx) —
 *    holds that host. Popping a pane out moves one DOM node into another
 *    document; React's tree for the pane does not change, so nothing remounts.
 *
 * Geometry is pure and lives in core (`computeWorkbench`, `resolveDrop`) and in
 * ./layout.ts; this file measures, draws and handles pointers.
 */
import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { createPortal } from 'react-dom';
import {
  computeWorkbench,
  hasCapability,
  PaneWindowContext,
  dropToPlace,
  findPaneAnywhere,
  focusGroup,
  focusInstance,
  layoutStore,
  paneDisplayTitle,
  releaseCapture,
  resolveDrop,
  setWorkbenchMeasurer,
  suppressNativeOverlays,
  type AreaHost,
  type AreaNode,
  type DockFrame,
  type DropTarget,
  type LayoutNode,
  type PaneState,
  type PaneWindow,
  type Surface,
} from '@horrible/core';

import { PaneWithRegions } from '../layout/Region';
import { GroupHeader } from './GroupHeader';
import {
  bodyOf,
  dragDock,
  dragSplit,
  SASH_GRAB,
  sashesOf,
  shadowLayout,
  withLiveSizes,
  type LiveSizes,
  type Sash,
} from './layout';
import { syncMotionAttribute } from './motion';
import { paneHost, PaneSlot, prunePaneHosts } from './pane-hosts';
import { PopoutHost } from './PopoutHost';
import './workbench.css';

/** Pointer travel before a press on a tab becomes a drag. */
const DRAG_SLOP = 4;
/** The most an entrance stagger may delay one pane, however many arrive at once. */
const STAGGER_CAP_MS = 150;
const STAGGER_STEP_MS = 30;
/** How long an entering pane keeps its entrance class; above the 180ms animation. */
const ENTER_HOLD_MS = 400;
const GRID_LINE = 'color-mix(in srgb, var(--border) 45%, transparent)';
const GRID = `linear-gradient(${GRID_LINE} 1px, transparent 1px), linear-gradient(90deg, ${GRID_LINE} 1px, transparent 1px)`;

interface GroupEntry {
  area: AreaNode;
  host: AreaHost;
}

function groupsOf(frame: DockFrame): GroupEntry[] {
  const out: GroupEntry[] = [];
  const walk = (n: LayoutNode): void => {
    if (n.kind === 'area') out.push({ area: n, host: { kind: 'center' } });
    else n.children.forEach(walk);
  };
  walk(frame.center);
  for (const side of ['left', 'right', 'bottom'] as const) {
    if (frame.docks[side].area.tabs.length > 0) {
      out.push({ area: frame.docks[side].area, host: { kind: 'dock', side } });
    }
  }
  return out;
}

/** Every group, pop-outs included: where each pane is mounted. */
function allGroupsOf(frame: DockFrame): GroupEntry[] {
  return [
    ...groupsOf(frame),
    ...frame.popouts.map((p) => ({ area: p.area, host: { kind: 'popout', popoutId: p.id } as AreaHost })),
  ];
}

interface TabDrag {
  instanceId: string;
  fromAreaId: string;
  title: string;
  point: { x: number; y: number };
  target: DropTarget | null;
  /** The tab under the pointer in the target strip, for an exact insert index. */
  index: number | null;
}

export function Workbench({ appTitle = 'horrible-dashboard' }: { appTitle?: string } = {}) {
  const { frame, workspaceId, hydrated } = useSyncExternalStore(
    layoutStore.subscribe,
    layoutStore.getSnapshot,
  );
  const rootRef = useRef<HTMLDivElement>(null);
  const [surface, setSurface] = useState<Surface>({ w: 0, h: 0 });
  const [live, setLive] = useState<LiveSizes | null>(null);
  const [drag, setDrag] = useState<TabDrag | null>(null);
  // Which window each open pop-out is, as its PopoutHost reports it.
  const [popoutWindows, setPopoutWindows] = useState<ReadonlyMap<string, PaneWindow>>(
    () => new Map(),
  );
  const onPopoutWindow = useCallback((id: string, pw: PaneWindow | null) => {
    setPopoutWindows((m) => {
      if ((m.get(id) ?? null) === pw) return m;
      const next = new Map(m);
      if (pw) next.set(id, pw);
      else next.delete(id);
      return next;
    });
  }, []);
  const mainWindow = useMemo<PaneWindow>(() => ({ win: window, popoutId: null }), []);
  const multiWindow = hasCapability('window.multi');

  // Measure, and keep measuring.
  useLayoutEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const report = () => {
      const r = el.getBoundingClientRect();
      setSurface((s) =>
        Math.round(r.width) === s.w && Math.round(r.height) === s.h
          ? s
          : { w: Math.round(r.width), h: Math.round(r.height) },
      );
    };
    report();
    const observer = new ResizeObserver(report);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // The pure verbs (keyboard focus moves, the agent's layout tools, the share
  // mirror) resolve geometry through this rather than reaching into the DOM.
  useEffect(() => {
    setWorkbenchMeasurer(() => {
      const r = rootRef.current?.getBoundingClientRect();
      return r && r.width > 0 ? { w: Math.round(r.width), h: Math.round(r.height) } : null;
    });
    return () => setWorkbenchMeasurer(null);
  }, []);

  useEffect(syncMotionAttribute, []);

  const drawn = useMemo(() => withLiveSizes(frame, live), [frame, live]);
  const layout = useMemo(() => computeWorkbench(drawn, surface), [drawn, surface]);
  const shadow = useMemo(() => shadowLayout(drawn, surface), [drawn, surface]);
  const sashes = useMemo(() => sashesOf(drawn, layout), [drawn, layout]);
  const groups = useMemo(() => groupsOf(drawn), [drawn]);

  // Window listeners outlive the render that installed them; they read through refs.
  const live$ = useRef({ frame, layout, surface });
  live$.current = { frame, layout, surface };

  const local = useCallback((e: { clientX: number; clientY: number }) => {
    const r = rootRef.current?.getBoundingClientRect();
    return r ? { x: e.clientX - r.left, y: e.clientY - r.top } : { x: 0, y: 0 };
  }, []);

  // --- sash drags ------------------------------------------------------------
  const onSashDown = useCallback(
    (e: React.PointerEvent, sash: Sash) => {
      if (e.button !== 0) return;
      e.preventDefault();
      releaseCapture();
      document.body.classList.add('is-workbench-dragging');
      let last: LiveSizes | null = null;
      const move = (me: PointerEvent) => {
        const p = local(me);
        last =
          sash.kind === 'split'
            ? {
                split: {
                  id: sash.splitId,
                  sizes: dragSplit(sash, sash.orientation === 'row' ? p.x : p.y),
                },
              }
            : { dock: { side: sash.side, size: dragDock(sash.side, p, live$.current.surface) } };
        setLive(last);
      };
      const up = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        window.removeEventListener('pointercancel', up);
        document.body.classList.remove('is-workbench-dragging');
        setLive(null);
        if (last?.split) {
          layoutStore.dispatch({
            type: 'SET_SPLIT_SIZES',
            splitId: last.split.id,
            sizes: last.split.sizes,
          });
        } else if (last?.dock) {
          layoutStore.dispatch({
            type: 'SET_DOCK_SIZE',
            side: last.dock.side,
            size: last.dock.size,
          });
        }
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      window.addEventListener('pointercancel', up);
    },
    [local],
  );

  // --- tab drags ---------------------------------------------------------------
  const justDragged = useRef(false);
  const suppressClick = useCallback(() => {
    const was = justDragged.current;
    justDragged.current = false;
    return was;
  }, []);

  const onTabPointerDown = useCallback(
    (e: React.PointerEvent, instanceId: string, fromAreaId: string) => {
      if (e.button !== 0) return;
      const start = { x: e.clientX, y: e.clientY };
      let started = false;
      let current: TabDrag | null = null;
      // The drop preview draws over panes, and a native browser surface paints
      // above all HTML: it would hide the preview and swallow the pointer.
      let release: (() => void) | null = null;
      const located = findPaneAnywhere(live$.current.frame, instanceId);
      const title = located ? paneDisplayTitle(located.pane) : instanceId;

      const move = (me: PointerEvent) => {
        if (!started) {
          if (Math.hypot(me.clientX - start.x, me.clientY - start.y) < DRAG_SLOP) return;
          started = true;
          // A captured pane (a game, a terminal) would otherwise keep the pointer.
          releaseCapture();
          release = suppressNativeOverlays();
          document.body.classList.add('is-workbench-dragging');
        }
        const { frame: f, layout: l, surface: s } = live$.current;
        const point = local(me);
        const under = document.elementFromPoint(me.clientX, me.clientY);
        const tab = under?.closest<HTMLElement>('[data-wb-tab-index]');
        // A tab strip always means "join these tabs". `resolveDrop` sees only
        // geometry, and the strip lies inside the group's top split band, so
        // without this a drop aimed at the tabs split the group above them.
        const strip = under?.closest<HTMLElement>('[data-wb-strip]')?.dataset.wbStrip;
        const stripRect = strip ? l.areas.get(strip) : undefined;
        current = {
          instanceId,
          fromAreaId,
          title,
          point,
          target:
            strip && stripRect
              ? { kind: 'area', areaId: strip, zone: 'tabs', preview: stripRect }
              : resolveDrop(f, l, s, point),
          index: tab ? Number(tab.dataset.wbTabIndex) : null,
        };
        setDrag(current);
      };
      const up = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        window.removeEventListener('pointercancel', up);
        if (!started) return;
        release?.();
        document.body.classList.remove('is-workbench-dragging');
        justDragged.current = true;
        // Cleared on the next tick in case no click follows (released off the tab).
        setTimeout(() => (justDragged.current = false), 0);
        setDrag(null);
        if (current?.target) commitTabDrop(current);
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      window.addEventListener('pointercancel', up);
    },
    [local],
  );

  // --- focus -------------------------------------------------------------------
  const focusPane = useCallback((instanceId: string) => {
    const f = layoutStore.getSnapshot().frame;
    if (f.focusedInstanceId === instanceId) return;
    const located = findPaneAnywhere(f, instanceId);
    if (located) focusInstance(located);
  }, []);

  // --- entrance ----------------------------------------------------------------
  // Panes a workspace arrives with appear at once; only those opened afterwards
  // play the entrance. Recorded in render so the first paint already knows.
  //
  // An entering pane keeps its class until the animation has had time to finish,
  // not just for one render: opening a pane is followed at once by its focus
  // dispatch, and dropping the class on that re-render would cut the entrance
  // off a frame in. `enter` maps instance → { at, delay } for panes still entering.
  const arrival = useRef<{ key: string; ids: Set<string> } | null>(null);
  const enter = useRef(new Map<string, { at: number; delay: number }>());
  const arrivalKey = `${workspaceId ?? ''}|${hydrated}`;
  if (arrival.current?.key !== arrivalKey) {
    arrival.current = {
      key: arrivalKey,
      ids: new Set(allGroupsOf(frame).flatMap((g) => g.area.tabs.map((t) => t.instanceId))),
    };
    enter.current.clear();
  }
  const now = performance.now();
  for (const [id, e] of enter.current)
    if (now - e.at > ENTER_HOLD_MS + e.delay) enter.current.delete(id);

  // --- the pane layer ----------------------------------------------------------
  // First-seen order (see note 1). Appending a newcomer and dropping a leaver
  // never changes the relative order of the panes that stay.
  const order = useRef<string[]>([]);
  const mounted = new Map<string, { group: GroupEntry }>();
  for (const g of multiWindow ? allGroupsOf(drawn) : groups) {
    const active = g.area.tabs[g.area.activeTab];
    if (active) mounted.set(active.instanceId, { group: g });
  }
  const mountedKey = [...mounted.keys()].join('\n');
  useEffect(() => {
    prunePaneHosts(new Set(mountedKey ? mountedKey.split('\n') : []));
  }, [mountedKey]);
  order.current = [
    ...order.current.filter((id) => mounted.has(id)),
    ...[...mounted.keys()].filter((id) => !order.current.includes(id)),
  ];
  let entering = 0;

  const centreEmpty = drawn.center.kind === 'area' && drawn.center.tabs.length === 0;
  const max = drawn.maximizedAreaId;

  return (
    <div
      ref={rootRef}
      className="wb-root"
      style={{
        position: 'absolute',
        inset: 0,
        zIndex: 30,
        pointerEvents: 'none',
        overflow: 'clip',
      }}
    >
      {surface.w > 0 &&
        groups.map(({ area, host }) => {
          const rect = layout.areas.get(area.id);
          if (!rect) return null;
          const covered = layout.covered.has(area.id);
          if (host.kind === 'center' && centreEmpty) return null;
          const focused = drawn.focusedAreaId === area.id;
          return (
            <section
              key={area.id}
              className="wb-group"
              data-wb-group={area.id}
              aria-label={host.kind === 'dock' ? `${host.side} dock` : undefined}
              onPointerDownCapture={() => {
                if (drawn.focusedAreaId !== area.id) focusGroup(area.id);
              }}
              style={{
                position: 'absolute',
                left: rect.x,
                top: rect.y,
                width: rect.w,
                height: rect.h,
                zIndex: max === area.id ? 3 : 1,
                visibility: covered ? 'hidden' : 'visible',
                pointerEvents: covered ? 'none' : 'auto',
                background: 'var(--win-bg, var(--bg-raised))',
                // The faint tactical grid shows only where no pane covers it: an
                // empty group, or the instant before a new pane paints.
                backgroundImage: GRID,
                backgroundSize: '24px 24px',
                backgroundPosition: '-1px -1px',
                display: 'flex',
                flexDirection: 'column',
              }}
            >
              <GroupHeader
                area={area}
                host={host}
                focused={focused}
                maximized={max === area.id}
                presentedInstanceId={drawn.presentedInstanceId}
                onTabPointerDown={onTabPointerDown}
                suppressClick={suppressClick}
              />
              {area.tabs.length === 0 && <EmptyGroup />}
            </section>
          );
        })}

      {surface.w > 0 &&
        sashes.map((sash) => (
          <SashLine key={sash.key} sash={sash} dragging={!!live} onPointerDown={onSashDown} />
        ))}

      {surface.w > 0 &&
        order.current.map((instanceId) => {
          const { group } = mounted.get(instanceId)!;
          // A popped-out pane's box is its pop-out window (PopoutHost).
          if (group.host.kind === 'popout') return null;
          const shown = layout.areas.get(group.area.id);
          const rect = shown ?? shadow.areas.get(group.area.id);
          const visible = !!shown && !layout.covered.has(group.area.id);
          const presented = drawn.presentedInstanceId === instanceId;
          const body = rect ? bodyOf(rect) : { x: 0, y: 0, w: surface.w, h: surface.h };
          if (!arrival.current!.ids.has(instanceId)) {
            arrival.current!.ids.add(instanceId);
            enter.current.set(instanceId, {
              at: now,
              delay: Math.min(entering++ * STAGGER_STEP_MS, STAGGER_CAP_MS),
            });
          }
          const entry = enter.current.get(instanceId);
          const isNew = !!entry;
          const delay = entry?.delay ?? 0;
          return (
            <PaneSlot
              key={instanceId}
              instanceId={instanceId}
              className={`wb-pane${isNew ? ' wb-pane-enter' : ''}${presented ? ' is-presented' : ''}`}
              data-wb-pane={instanceId}
              aria-hidden={visible || presented ? undefined : true}
              style={
                presented
                  ? {
                      position: 'fixed',
                      inset: 0,
                      zIndex: 48,
                      pointerEvents: 'auto',
                      background: 'var(--bg)',
                    }
                  : {
                      position: 'absolute',
                      left: body.x,
                      top: body.y,
                      width: body.w,
                      height: body.h,
                      zIndex: max === group.area.id ? 4 : 2,
                      visibility: visible ? 'visible' : 'hidden',
                      pointerEvents: visible ? 'auto' : 'none',
                      animationDelay: delay ? `${delay}ms` : undefined,
                    }
              }
            />
          );
        })}

      {/* The panes themselves, each into its own host element (note 4). One keyed
          list wherever the pane is shown, so a move between windows is a prop
          change here, never a remount. */}
      {surface.w > 0 &&
        order.current.map((instanceId) => {
          const { group } = mounted.get(instanceId)!;
          const pane = group.area.tabs[group.area.activeTab];
          const pw =
            group.host.kind === 'popout'
              ? (popoutWindows.get(group.host.popoutId) ?? mainWindow)
              : mainWindow;
          return createPortal(
            <PaneContent pane={pane} paneWindow={pw} onFocus={focusPane} />,
            paneHost(instanceId),
            instanceId,
          );
        })}

      {multiWindow &&
        drawn.popouts.map((p) => (
          <PopoutHost
            key={p.id}
            popout={p}
            focused={drawn.focusedAreaId === p.area.id}
            presentedInstanceId={drawn.presentedInstanceId}
            appTitle={appTitle}
            onWindow={onPopoutWindow}
          />
        ))}

      {drag && <DropPreview drag={drag} />}
    </div>
  );
}

/** A pane's content, rendered into its host element. */
const PaneContent = memo(function PaneContent({
  pane,
  paneWindow,
  onFocus,
}: {
  pane: PaneState;
  paneWindow: PaneWindow;
  onFocus: (instanceId: string) => void;
}) {
  return (
    <PaneWindowContext.Provider value={paneWindow}>
      <div
        className="wb-pane-body"
        onPointerDownCapture={() => onFocus(pane.instanceId)}
        style={{ flex: 1, display: 'flex', minWidth: 0, minHeight: 0 }}
      >
        <PaneWithRegions pane={pane} />
      </div>
    </PaneWindowContext.Provider>
  );
});

/** Turn a finished tab drag into one store action. */
function commitTabDrop(d: TabDrag): void {
  const target = d.target!;
  const frame = layoutStore.getSnapshot().frame;
  const source = findPaneAnywhere(frame, d.instanceId);
  if (!source) return;
  const sourceArea =
    target.kind === 'area' && target.areaId === source.location.areaId ? target.areaId : null;
  if (target.kind === 'area' && target.zone === 'tabs') {
    if (sourceArea) {
      // Within its own strip: a reorder, or nothing.
      if (d.index === null) return;
      const area = groupsOf(frame).find((g) => g.area.id === sourceArea)?.area;
      const from = area?.tabs.findIndex((t) => t.instanceId === d.instanceId) ?? -1;
      if (from >= 0 && from !== d.index) {
        layoutStore.dispatch({ type: 'REORDER_TAB', areaId: sourceArea, from, to: d.index });
      }
      return;
    }
    layoutStore.dispatch({
      type: 'MOVE_PANE',
      instanceId: d.instanceId,
      target: {
        kind: 'area',
        areaId: target.areaId,
        ...(d.index !== null ? { index: d.index } : {}),
      },
    });
    return;
  }
  // Splitting a group by its own only tab would leave it empty beside itself.
  if (sourceArea) {
    const area = groupsOf(frame).find((g) => g.area.id === sourceArea)?.area;
    if (area && area.tabs.length < 2) return;
  }
  layoutStore.dispatch({
    type: 'MOVE_PANE',
    instanceId: d.instanceId,
    target: dropToPlace(target),
  });
}

function SashLine({
  sash,
  dragging,
  onPointerDown,
}: {
  sash: Sash;
  dragging: boolean;
  onPointerDown: (e: React.PointerEvent, sash: Sash) => void;
}) {
  const vertical = sash.line.w === 0;
  const { x, y, w, h } = sash.line;
  return (
    <div
      className={`wb-sash${dragging ? ' is-dragging' : ''}`}
      role="separator"
      aria-orientation={vertical ? 'vertical' : 'horizontal'}
      onPointerDown={(e) => onPointerDown(e, sash)}
      onDoubleClick={() => {
        // Even the split back out; a dock has no "even" to return to.
        if (sash.kind !== 'split') return;
        layoutStore.dispatch({
          type: 'SET_SPLIT_SIZES',
          splitId: sash.splitId,
          sizes: sash.sizes.map(() => 1 / sash.sizes.length),
        });
      }}
      style={{
        position: 'absolute',
        left: vertical ? x - SASH_GRAB : x,
        top: vertical ? y : y - SASH_GRAB,
        width: vertical ? SASH_GRAB * 2 + 1 : w,
        height: vertical ? h : SASH_GRAB * 2 + 1,
        zIndex: 5,
        pointerEvents: 'auto',
        cursor: vertical ? 'col-resize' : 'row-resize',
      }}
    />
  );
}

function DropPreview({ drag }: { drag: TabDrag }) {
  const p = drag.target?.preview;
  return (
    <>
      {p && (
        <div
          className="wb-drop"
          style={{
            position: 'absolute',
            left: p.x,
            top: p.y,
            width: p.w,
            height: p.h,
            zIndex: 20,
            boxSizing: 'border-box',
            borderTop: '2px solid var(--accent)',
            background: 'color-mix(in srgb, var(--accent) 14%, transparent)',
            outline: '1px solid color-mix(in srgb, var(--accent) 45%, transparent)',
            outlineOffset: -1,
            pointerEvents: 'none',
          }}
        />
      )}
      <div
        style={{
          position: 'absolute',
          left: drag.point.x + 12,
          top: drag.point.y + 12,
          zIndex: 21,
          padding: '4px 10px',
          background: 'var(--bg-elevated)',
          borderTop: '2px solid var(--accent)',
          color: 'var(--text-strong)',
          fontSize: 11,
          fontWeight: 650,
          letterSpacing: '0.12em',
          textTransform: 'uppercase',
          whiteSpace: 'nowrap',
          pointerEvents: 'none',
          clipPath: 'polygon(0 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%)',
        }}
      >
        {drag.title}
      </div>
    </>
  );
}

function EmptyGroup() {
  return (
    <div
      style={{
        flex: 1,
        display: 'grid',
        placeItems: 'center',
        color: 'var(--text-muted)',
        fontFamily: 'var(--font-mono, ui-monospace, monospace)',
        fontSize: 11,
        letterSpacing: '0.06em',
      }}
    >
      DROP A TAB HERE
    </div>
  );
}
