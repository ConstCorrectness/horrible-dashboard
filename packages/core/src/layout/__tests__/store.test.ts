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

  it('RETARGET_PANE renames in place, keeping position and regions', () => {
    load();
    const children = (layoutStore.getSnapshot().frame.center as { children: AreaNode[] }).children;
    const inst = findPaneAnywhere(layoutStore.getSnapshot().frame, children[0].tabs[0].instanceId)!;
    const region = {
      open: true,
      size: 300,
      collapsed: false,
      views: ['editor.recentNotes'],
      activeView: 'editor.recentNotes',
    };
    layoutStore.dispatch({
      type: 'SET_REGION',
      instanceId: inst.pane.instanceId,
      position: 'left',
      region,
    });
    const next = layoutStore.dispatch({
      type: 'RETARGET_PANE',
      instanceId: inst.pane.instanceId,
      newInstanceId: 'editor.buffer:note:7',
      params: { source: 'note:7' },
    });
    const moved = findPaneAnywhere(next.frame, 'editor.buffer:note:7');
    expect(findPaneAnywhere(next.frame, inst.pane.instanceId)).toBeNull();
    expect(moved?.location).toEqual(inst.location);
    expect(moved?.pane.viewId).toBe('editor.buffer');
    expect(moved?.pane.params).toEqual({ source: 'note:7' });
    expect(moved?.pane.regions?.left).toEqual(region);
  });

  it('RETARGET_PANE refuses to mint an id another pane already holds', () => {
    load();
    const frame = layoutStore.getSnapshot().frame;
    const children = (frame.center as { children: AreaNode[] }).children;
    const next = layoutStore.dispatch({
      type: 'RETARGET_PANE',
      instanceId: children[0].tabs[0].instanceId,
      newInstanceId: children[1].tabs[0].instanceId,
    });
    expect(next.frame).toBe(frame);
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

  it('SET_TOOL_SIZE remembers a width per tool and mirrors it as the dock fallback', () => {
    load();
    const files = freshPane('files.tree');
    layoutStore.dispatch({ type: 'INSERT_TOOL', side: 'right', pane: files });
    const chat = freshPane('agent.chat');
    layoutStore.dispatch({ type: 'INSERT_TOOL', side: 'right', pane: chat });

    layoutStore.dispatch({
      type: 'SET_TOOL_SIZE',
      side: 'right',
      instanceId: files.instanceId,
      size: 280,
    });
    const after = layoutStore.dispatch({
      type: 'SET_TOOL_SIZE',
      side: 'right',
      instanceId: chat.instanceId,
      size: 500,
    });

    const tools = after.frame.docks.right.tools;
    // Each tool keeps its own width — the whole point of the per-tool field.
    expect(tools.find((t) => t.instanceId === files.instanceId)?.dockSize).toBe(280);
    expect(tools.find((t) => t.instanceId === chat.instanceId)?.dockSize).toBe(500);
    // ...and the dock tracks the last one, so the next tool opened here inherits it.
    expect(after.frame.docks.right.size).toBe(500);
  });

  it('SET_TOOL_SIZE is a no-op for an unknown tool or an unchanged size', () => {
    load();
    const tool = freshPane('files.tree');
    layoutStore.dispatch({ type: 'INSERT_TOOL', side: 'right', pane: tool });
    const sized = layoutStore.dispatch({
      type: 'SET_TOOL_SIZE',
      side: 'right',
      instanceId: tool.instanceId,
      size: 300,
    });
    expect(
      layoutStore.dispatch({
        type: 'SET_TOOL_SIZE',
        side: 'right',
        instanceId: tool.instanceId,
        size: 300,
      }),
    ).toBe(sized);
    expect(
      layoutStore.dispatch({
        type: 'SET_TOOL_SIZE',
        side: 'right',
        instanceId: 'nope#9',
        size: 400,
      }),
    ).toBe(sized);
  });

  it('WINDOW_FROM_PANE → DOCK_WINDOW round-trips a pane through the window layer', () => {
    load();
    const snap = layoutStore.getSnapshot();
    const areaNode = (snap.frame.center as { children: AreaNode[] }).children[0];
    const inst = areaNode.tabs[0].instanceId;
    const windowed = layoutStore.dispatch({ type: 'WINDOW_FROM_PANE', instanceId: inst });
    const located = findPaneAnywhere(windowed.frame, inst)!;
    expect(located.location.kind).toBe('window');
    expect(windowed.frame.windows).toHaveLength(1);
    const windowId = (located.location as { windowId: string }).windowId;
    const target = collectAreaIds()[0];
    const docked = layoutStore.dispatch({ type: 'DOCK_WINDOW', windowId, areaId: target });
    expect(findPaneAnywhere(docked.frame, inst)?.location).toEqual({
      kind: 'area',
      areaId: target,
    });
    expect(docked.frame.windows).toHaveLength(0);
    expect(docked.frame.focusedWindowId).toBeNull();
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

describe('MOVE_TOOL (rail customization drop verb)', () => {
  it('moves a docked tool to another dock and makes it active there', () => {
    load();
    const files = freshPane('files.tree');
    layoutStore.dispatch({ type: 'INSERT_TOOL', side: 'left', pane: files });
    const next = layoutStore.dispatch({
      type: 'MOVE_TOOL',
      instanceId: files.instanceId,
      side: 'right',
    });
    expect(findPaneAnywhere(next.frame, files.instanceId)?.location).toEqual({
      kind: 'dock',
      dock: 'right',
    });
    expect(next.frame.docks.right.activeTool).toBe(files.instanceId);
    // It was the left dock's visible tool, so the right dock reveals it.
    expect(next.frame.docks.right.visible).toBe(true);
  });

  it('keeps a hidden target dock hidden when the tool was stacked out of sight', () => {
    load();
    const files = freshPane('files.tree');
    layoutStore.dispatch({ type: 'INSERT_TOOL', side: 'left', pane: files });
    const scratch = freshPane('scratch.note');
    layoutStore.dispatch({ type: 'INSERT_TOOL', side: 'left', pane: scratch });
    // files.tree is now stacked behind scratch.note; bottom dock is hidden.
    const next = layoutStore.dispatch({
      type: 'MOVE_TOOL',
      instanceId: files.instanceId,
      side: 'bottom',
    });
    expect(findPaneAnywhere(next.frame, files.instanceId)?.location).toEqual({
      kind: 'dock',
      dock: 'bottom',
    });
    expect(next.frame.docks.bottom.visible).toBe(false);
    expect(next.frame.docks.bottom.activeTool).toBe(files.instanceId);
  });

  it('re-docks a pane living in a center area, revealing the dock', () => {
    load();
    const children = (layoutStore.getSnapshot().frame.center as { children: AreaNode[] }).children;
    const inst = children[0].tabs[0].instanceId;
    const next = layoutStore.dispatch({ type: 'MOVE_TOOL', instanceId: inst, side: 'right' });
    expect(findPaneAnywhere(next.frame, inst)?.location).toEqual({ kind: 'dock', dock: 'right' });
    expect(next.frame.docks.right.visible).toBe(true);
  });

  it('no-ops for the same side or an unknown instance', () => {
    load();
    const files = freshPane('files.tree');
    layoutStore.dispatch({ type: 'INSERT_TOOL', side: 'left', pane: files });
    const snap = layoutStore.getSnapshot();
    expect(
      layoutStore.dispatch({ type: 'MOVE_TOOL', instanceId: files.instanceId, side: 'left' }),
    ).toBe(snap);
    expect(layoutStore.dispatch({ type: 'MOVE_TOOL', instanceId: 'nope#9', side: 'right' })).toBe(
      snap,
    );
  });
});
