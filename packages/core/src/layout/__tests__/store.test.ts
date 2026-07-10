import { beforeEach, describe, expect, it } from 'vitest';

import { layoutStore } from '../store';
import { areaId, createEmptyFrame, findArea, findPaneAnywhere, instanceId } from '../model';
import { seedFromPreset, type FramePreset } from '../presets';
import type { AreaNode, PaneState } from '../types';

const KNOWN = new Set(['scratch.note', 'files.tree', 'editor.buffer', 'dashboard.welcome']);

const preset: FramePreset = {
  id: 'test',
  name: 'Test',
  frame: {
    center: {
      split: 'row',
      children: [{ tabs: ['editor.buffer'] }, { pane: 'dashboard.welcome' }],
    },
    docks: { left: { tools: ['files.tree'] } },
  },
};

function load(): void {
  layoutStore.dispatch({
    type: 'LOAD_WORKSPACE',
    workspaceId: 'test',
    frame: seedFromPreset(preset, { knownViews: KNOWN }),
  });
}

function freshPane(viewId: string): PaneState {
  const seq = layoutStore.getSnapshot().frame.paneSeq;
  return { instanceId: instanceId(viewId, seq), viewId };
}

beforeEach(() => {
  layoutStore.resetForTests();
});

describe('hydration and revisions', () => {
  it('starts unhydrated with a valid empty frame', () => {
    const snap = layoutStore.getSnapshot();
    expect(snap.hydrated).toBe(false);
    expect(snap.workspaceId).toBeNull();
    expect(snap.frame.center.kind).toBe('area');
  });

  it('LOAD_WORKSPACE swaps id+frame atomically without bumping revision', () => {
    const before = layoutStore.getSnapshot().revision;
    load();
    const snap = layoutStore.getSnapshot();
    expect(snap.hydrated).toBe(true);
    expect(snap.workspaceId).toBe('test');
    expect(snap.revision).toBe(before);
  });

  it('mutations bump revision; no-ops return the same snapshot', () => {
    load();
    const snap = layoutStore.getSnapshot();
    const focused = snap.frame.focusedAreaId!;
    // No-op: focusing the already-focused area.
    expect(layoutStore.dispatch({ type: 'FOCUS_AREA', areaId: focused })).toBe(snap);
    // Real mutation.
    const areas = collectAreaIds();
    const other = areas.find((a) => a !== focused)!;
    const next = layoutStore.dispatch({ type: 'FOCUS_AREA', areaId: other });
    expect(next.revision).toBe(snap.revision + 1);
    expect(next.frame.focusedAreaId).toBe(other);
  });
});

function collectAreaIds(): string[] {
  const out: string[] = [];
  const walk = (node: AreaNode | { kind: 'split'; children: unknown[] }): void => {
    if (node.kind === 'area') out.push((node as AreaNode).id);
    else for (const c of (node as { children: AreaNode[] }).children) walk(c);
  };
  walk(layoutStore.getSnapshot().frame.center as AreaNode);
  return out;
}

describe('pane and area actions', () => {
  it('SPLIT_AREA with a pane focuses the new area (precomputable id)', () => {
    load();
    const snap = layoutStore.getSnapshot();
    const target = snap.frame.focusedAreaId!;
    const expectedAreaId = areaId(snap.frame.paneSeq);
    const pane = {
      instanceId: instanceId('scratch.note', snap.frame.paneSeq),
      viewId: 'scratch.note',
    };
    const next = layoutStore.dispatch({
      type: 'SPLIT_AREA',
      areaId: target,
      direction: 'below',
      pane,
    });
    expect(next.frame.focusedAreaId).toBe(expectedAreaId);
    const area = findArea(next.frame.center, expectedAreaId)!;
    expect(area.tabs[0].viewId).toBe('scratch.note');
  });

  it('MOVE_PANE relocates a tab and refocuses the target area', () => {
    load();
    const snap = layoutStore.getSnapshot();
    const editor = snap.frame.center as { children: AreaNode[] };
    const source = editor.children[0];
    const target = editor.children[1];
    const inst = source.tabs[0].instanceId;
    const next = layoutStore.dispatch({
      type: 'MOVE_PANE',
      instanceId: inst,
      targetAreaId: target.id,
    });
    expect(findPaneAnywhere(next.frame, inst)?.location).toEqual({
      kind: 'area',
      areaId: target.id,
    });
    expect(next.frame.focusedAreaId).toBe(target.id);
  });

  it('SET_REGION patches per-instance region state', () => {
    load();
    const snap = layoutStore.getSnapshot();
    const inst = (snap.frame.center as { children: AreaNode[] }).children[0].tabs[0].instanceId;
    const region = {
      open: true,
      size: 280,
      collapsed: false,
      views: ['files.tree'],
      activeView: 'files.tree',
    };
    const next = layoutStore.dispatch({
      type: 'SET_REGION',
      instanceId: inst,
      position: 'right',
      region,
    });
    expect(findPaneAnywhere(next.frame, inst)?.pane.regions?.right).toEqual(region);
    const cleared = layoutStore.dispatch({
      type: 'SET_REGION',
      instanceId: inst,
      position: 'right',
      region: null,
    });
    expect(findPaneAnywhere(cleared.frame, inst)?.pane.regions).toBeUndefined();
  });

  it('SET_FULLSCREEN validates the area and clears on null', () => {
    load();
    expect(
      layoutStore.dispatch({ type: 'SET_FULLSCREEN', areaId: 'a999' }).frame.fullscreenAreaId,
    ).toBeNull();
    const areaIdReal = collectAreaIds()[0];
    expect(
      layoutStore.dispatch({ type: 'SET_FULLSCREEN', areaId: areaIdReal }).frame.fullscreenAreaId,
    ).toBe(areaIdReal);
    expect(
      layoutStore.dispatch({ type: 'SET_FULLSCREEN', areaId: null }).frame.fullscreenAreaId,
    ).toBeNull();
  });
});

describe('dock and floating actions', () => {
  it('INSERT_TOOL activates and reveals the dock; SET_DOCK cannot open an empty dock', () => {
    load();
    const tool = freshPane('files.tree');
    const next = layoutStore.dispatch({ type: 'INSERT_TOOL', side: 'right', pane: tool });
    expect(next.frame.docks.right.visible).toBe(true);
    expect(next.frame.docks.right.activeTool).toBe(tool.instanceId);
    const bottom = layoutStore.dispatch({
      type: 'SET_DOCK',
      side: 'bottom',
      patch: { visible: true },
    });
    expect(bottom.frame.docks.bottom.visible).toBe(false);
  });

  it('FLOAT_PANE → DOCK_FLOATING round-trips a pane through the floating layer', () => {
    load();
    const snap = layoutStore.getSnapshot();
    const areaNode = (snap.frame.center as { children: AreaNode[] }).children[0];
    const inst = areaNode.tabs[0].instanceId;
    const floated = layoutStore.dispatch({ type: 'FLOAT_PANE', instanceId: inst });
    expect(findPaneAnywhere(floated.frame, inst)?.location).toEqual({ kind: 'floating' });
    const target = collectAreaIds()[0];
    const docked = layoutStore.dispatch({
      type: 'DOCK_FLOATING',
      instanceId: inst,
      areaId: target,
    });
    expect(findPaneAnywhere(docked.frame, inst)?.location).toEqual({
      kind: 'area',
      areaId: target,
    });
    expect(docked.frame.floating).toHaveLength(0);
  });

  it('REMOVE_PANE keeps a valid frame when the last center pane closes', () => {
    layoutStore.dispatch({
      type: 'LOAD_WORKSPACE',
      workspaceId: 'mini',
      frame: (() => {
        const f = createEmptyFrame();
        return f;
      })(),
    });
    const pane = freshPane('scratch.note');
    const root = layoutStore.getSnapshot().frame.center.id;
    layoutStore.dispatch({ type: 'INSERT_PANE', areaId: root, pane });
    const next = layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId: pane.instanceId });
    expect(next.frame.center.kind).toBe('area');
    expect((next.frame.center as AreaNode).tabs).toHaveLength(0);
  });
});
