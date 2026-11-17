/**
 * Building workbench frames for tests: one centre group per entry, side by side,
 * each holding the listed views as tabs.
 */
import { createEmptyDockFrame } from '../dock/frame';
import type { DockSide, LayoutNode } from '../dock/types';
import { areaId, instanceId } from '../model';
import { layoutStore } from '../store';
import { createEmptyWindowFrame } from '../legacy-frame';
import type { AreaNode, FrameState, PaneState, WindowFrame, WindowState } from '../types';

/** A group's contents: one view id, or several (its tabs, first showing). */
export type GroupSpec = string | string[];

/**
 * A frame with one centre group per spec, in a row. Instance ids are
 * `<view>#<n>` in order; group ids follow, as the reducer would mint them. Docks
 * are given as `{ side: spec }` and start visible.
 */
export function groupsFrame(
  specs: GroupSpec[],
  docks: Partial<Record<DockSide, GroupSpec>> = {},
  base: Partial<FrameState> = {},
): FrameState {
  const empty = createEmptyDockFrame();
  let seq = empty.paneSeq;
  const group = (spec: GroupSpec): AreaNode => {
    const tabs: PaneState[] = [spec]
      .flat()
      .map((viewId) => ({ instanceId: instanceId(viewId, seq++), viewId }));
    return { kind: 'area', id: areaId(seq++), tabs, activeTab: 0 };
  };
  const groups = specs.map(group);
  const center: LayoutNode =
    groups.length === 0
      ? empty.center
      : groups.length === 1
        ? groups[0]
        : {
            kind: 'split',
            id: `s${seq++}`,
            orientation: 'row',
            children: groups,
            sizes: groups.map(() => 1 / groups.length),
          };
  const dockState = { ...empty.docks };
  for (const [side, spec] of Object.entries(docks) as [DockSide, GroupSpec][]) {
    const area = group(spec);
    dockState[side] = { ...dockState[side], visible: true, area };
  }
  return {
    ...empty,
    center,
    docks: dockState,
    focusedAreaId: null,
    focusedInstanceId: null,
    paneSeq: seq,
    ...base,
  };
}

/** Kept for tests written against the floating desktop: one group per spec. */
export const windowsFrame = (specs: GroupSpec[]): FrameState => groupsFrame(specs);

/** Load `frame` as the active desktop. */
export function loadFrame(frame: FrameState, workspaceId = 'desktop'): FrameState {
  layoutStore.dispatch({ type: 'LOAD_WORKSPACE', workspaceId, frame });
  return layoutStore.getSnapshot().frame;
}

/** The live frame. */
export function current(): FrameState {
  return layoutStore.getSnapshot().frame;
}

/** The group holding `instanceId`. */
export function groupOf(instanceId: string): AreaNode | undefined {
  const f = current();
  const all: AreaNode[] = [];
  const walk = (n: LayoutNode) =>
    n.kind === 'area' ? all.push(n) : n.children.forEach((c) => walk(c));
  walk(f.center);
  for (const side of ['left', 'right', 'bottom'] as const) all.push(f.docks[side].area);
  for (const p of f.popouts) all.push(p.area);
  return all.find((a) => a.tabs.some((t) => t.instanceId === instanceId));
}

// ---------------------------------------------------------------------------
// Frame v3 (the floating desktop), for the legacy reader's tests.
// ---------------------------------------------------------------------------

/** A v3 desktop with one window per spec, cascaded so their rects differ. */
export function v3WindowsFrame(specs: GroupSpec[], base: Partial<WindowFrame> = {}): WindowFrame {
  let seq = 1;
  const windows: WindowState[] = specs.map((spec, i) => {
    const tabs: PaneState[] = [spec]
      .flat()
      .map((viewId) => ({ instanceId: instanceId(viewId, seq++), viewId }));
    const id = `w${seq++}`;
    const area = { kind: 'area' as const, id: `a${seq++}`, tabs, activeTab: 0 };
    return { id, area, rect: { x: 40 * i, y: 40 * i, w: 640, h: 480 }, mode: 'normal', z: i + 1 };
  });
  return {
    ...createEmptyWindowFrame(),
    windows,
    windowViewport: { w: 1280, h: 800 },
    paneSeq: seq,
    ...base,
  };
}

/** Every pane of a v3 desktop, with its window. */
export function v3Panes(frame: WindowFrame): { pane: PaneState; windowId: string }[] {
  return frame.windows.flatMap((w) => w.area.tabs.map((pane) => ({ pane, windowId: w.id })));
}
