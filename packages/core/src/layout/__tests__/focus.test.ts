import { beforeEach, describe, expect, it } from 'vitest';

import { layoutStore } from '../store';
import { findPaneAnywhere } from '../model';
import { seedFromPreset, type FramePreset } from '../presets';

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

const frame = () => layoutStore.getSnapshot().frame;

/** Instance id of the one pane of `viewId`, wherever it lives. */
function instanceOf(viewId: string): string {
  const found = [
    ...frame().docks.left.tools,
    ...frame().docks.right.tools,
    ...frame().docks.bottom.tools,
  ].find((p) => p.viewId === viewId);
  if (found) return found.instanceId;
  const walk = (node: ReturnType<typeof frame>['center']): string | null => {
    if (node.kind === 'area') return node.tabs.find((t) => t.viewId === viewId)?.instanceId ?? null;
    for (const child of node.children) {
      const hit = walk(child);
      if (hit) return hit;
    }
    return null;
  };
  const hit = walk(frame().center);
  if (!hit) throw new Error(`no pane for ${viewId}`);
  return hit;
}

beforeEach(() => {
  layoutStore.resetForTests();
  load();
});

describe('FOCUS_PANE', () => {
  it('focusing a center pane focuses its area too', () => {
    const editor = instanceOf('editor.buffer');
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: editor });
    const f = frame();
    expect(f.focusedInstanceId).toBe(editor);
    const area = findPaneAnywhere(f, editor)!.location;
    expect(area.kind).toBe('area');
    expect(f.focusedAreaId).toBe(area.kind === 'area' ? area.areaId : null);
  });

  it('focusing a docked tool leaves the focused AREA alone', () => {
    // The bug this field exists for: a docked tool is not in the center tree, so
    // `focusedAreaId` cannot represent it. Area verbs still need a center target,
    // but pane-scoped keybindings must follow the dock.
    const editor = instanceOf('editor.buffer');
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: editor });
    const areaBefore = frame().focusedAreaId;

    const tool = instanceOf('files.tree');
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: tool });

    expect(frame().focusedInstanceId).toBe(tool);
    expect(frame().focusedAreaId).toBe(areaBefore);
  });

  it('ignores an unknown instance id', () => {
    const before = layoutStore.getSnapshot();
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: 'nope#9' });
    expect(layoutStore.getSnapshot()).toBe(before);
  });

  it('null clears the focused pane', () => {
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: instanceOf('editor.buffer') });
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: null });
    expect(frame().focusedInstanceId).toBeNull();
  });

  it('re-focusing the same pane is a no-op snapshot', () => {
    const editor = instanceOf('editor.buffer');
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: editor });
    const snap = layoutStore.getSnapshot();
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: editor });
    expect(layoutStore.getSnapshot()).toBe(snap);
  });
});

describe('focus is never left dangling', () => {
  it('closing the focused pane clears it', () => {
    const editor = instanceOf('editor.buffer');
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: editor });
    layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId: editor });
    expect(frame().focusedInstanceId).toBeNull();
  });

  it('closing a docked focused tool clears it', () => {
    const tool = instanceOf('files.tree');
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: tool });
    layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId: tool });
    expect(frame().focusedInstanceId).toBeNull();
  });

  it('a moved pane keeps focus (MOVE_TOOL removes and re-inserts it)', () => {
    const tool = instanceOf('files.tree');
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: tool });
    layoutStore.dispatch({ type: 'MOVE_TOOL', instanceId: tool, side: 'right' });
    expect(frame().focusedInstanceId).toBe(tool);
  });

  it('loading a workspace drops a focus id that workspace does not contain', () => {
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: instanceOf('editor.buffer') });
    const seeded = seedFromPreset(preset, { knownViews: KNOWN });
    layoutStore.dispatch({
      type: 'LOAD_WORKSPACE',
      workspaceId: 'other',
      frame: { ...seeded, focusedInstanceId: 'ghost.pane#42' },
    });
    expect(frame().focusedInstanceId).toBeNull();
  });
});
