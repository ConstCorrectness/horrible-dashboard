/**
 * Drag-out: the drop verb and the undock action underneath it. Views are
 * registered synthetically — a real module manifest reaches the editor's
 * module-scope WebSocket and dies without jsdom.
 */
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../../registry';
import { openToolInDock } from '../controller';
import { dropPaneOnArea, paneDrag } from '../drag';
import { collectAreas, findPaneAnywhere } from '../model';
import { seedFromPreset, type FramePreset } from '../presets';
import { layoutStore } from '../store';

const Stub = () => null;
const KNOWN = new Set(['d.doc', 'd.tool', 'd.other']);

const preset: FramePreset = {
  id: 'drag',
  name: 'Drag',
  frame: {
    center: { split: 'row', children: [{ pane: 'd.doc' }, { tabs: [] }] },
    docks: { left: { tools: ['d.tool'], visible: true } },
  },
};

beforeAll(() => {
  registry.register({
    id: 'drag-test',
    title: 'Drag test',
    panels: [
      { id: 'd.doc', title: 'Doc', component: Stub, role: 'document', singleton: true },
      { id: 'd.tool', title: 'Tool', component: Stub, role: 'tool', singleton: true },
      {
        id: 'd.other',
        title: 'Other',
        component: Stub,
        role: 'tool',
        defaultDock: 'right',
        singleton: true,
      },
    ],
  });
});

beforeEach(() => {
  paneDrag.end();
  layoutStore.resetForTests();
  layoutStore.dispatch({
    type: 'LOAD_WORKSPACE',
    workspaceId: 'drag',
    frame: seedFromPreset(preset, { knownViews: KNOWN }),
  });
});

const frame = () => layoutStore.getSnapshot().frame;
const emptyAreaId = () => collectAreas(frame().center).find((a) => a.tabs.length === 0)!.id;
const dockedTool = () => frame().docks.left.tools[0];

describe('paneDrag store', () => {
  it('reports the in-flight payload and clears on end', () => {
    expect(paneDrag.getSnapshot()).toBeNull();
    paneDrag.begin({ kind: 'view', viewId: 'd.other', title: 'Other' });
    expect(paneDrag.getSnapshot()).toEqual({ kind: 'view', viewId: 'd.other', title: 'Other' });
    paneDrag.end();
    expect(paneDrag.getSnapshot()).toBeNull();
  });

  it('notifies subscribers on begin and end, but not on a redundant end', () => {
    let calls = 0;
    const stop = paneDrag.subscribe(() => calls++);
    paneDrag.begin({ kind: 'view', viewId: 'd.other', title: 'Other' });
    paneDrag.end();
    paneDrag.end(); // already clear — must not re-notify
    stop();
    expect(calls).toBe(2);
  });
});

describe('dropPaneOnArea', () => {
  it('carries a docked tool out into a center area', () => {
    const tool = dockedTool();
    const areaId = emptyAreaId();
    const id = dropPaneOnArea(
      { kind: 'pane', instanceId: tool.instanceId, viewId: 'd.tool', title: 'Tool' },
      areaId,
    );
    expect(id).toBe(tool.instanceId);
    expect(findPaneAnywhere(frame(), tool.instanceId)?.location).toEqual({ kind: 'area', areaId });
    // ...and it is genuinely gone from the dock, not copied.
    expect(frame().docks.left.tools).toHaveLength(0);
  });

  it('opens a not-yet-open view where it lands', () => {
    const areaId = emptyAreaId();
    const id = dropPaneOnArea({ kind: 'view', viewId: 'd.other', title: 'Other' }, areaId);
    expect(id).not.toBeNull();
    expect(findPaneAnywhere(frame(), id!)?.location).toEqual({ kind: 'area', areaId });
  });

  it('focuses the destination area after a drop', () => {
    const areaId = emptyAreaId();
    dropPaneOnArea({ kind: 'view', viewId: 'd.other', title: 'Other' }, areaId);
    expect(frame().focusedAreaId).toBe(areaId);
  });

  it('falls back to opening the view when the dragged pane vanished mid-drag', () => {
    const tool = dockedTool();
    const payload = {
      kind: 'pane' as const,
      instanceId: tool.instanceId,
      viewId: 'd.tool',
      title: 'Tool',
    };
    layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId: tool.instanceId });
    const areaId = emptyAreaId();
    const id = dropPaneOnArea(payload, areaId);
    expect(id).not.toBeNull();
    expect(findPaneAnywhere(frame(), id!)?.location).toEqual({ kind: 'area', areaId });
  });

  it('is a no-op dropping a pane on the area it already occupies', () => {
    const areaId = emptyAreaId();
    const id = dropPaneOnArea({ kind: 'view', viewId: 'd.other', title: 'Other' }, areaId)!;
    const before = layoutStore.getSnapshot();
    expect(
      dropPaneOnArea({ kind: 'pane', instanceId: id, viewId: 'd.other', title: 'Other' }, areaId),
    ).toBeNull();
    expect(layoutStore.getSnapshot()).toBe(before);
  });

  it('rejects a drop on an unknown area without disturbing the layout', () => {
    const tool = dockedTool();
    const before = layoutStore.getSnapshot();
    expect(
      dropPaneOnArea(
        { kind: 'pane', instanceId: tool.instanceId, viewId: 'd.tool', title: 'Tool' },
        'nope',
      ),
    ).toBeNull();
    expect(layoutStore.getSnapshot()).toBe(before);
  });

  it('leaves the rail able to re-dock a tool that was dragged out', () => {
    const tool = dockedTool();
    dropPaneOnArea(
      { kind: 'pane', instanceId: tool.instanceId, viewId: 'd.tool', title: 'Tool' },
      emptyAreaId(),
    );
    // The view is out in the center, so the dock is empty; opening it again
    // must produce a fresh docked instance rather than being refused.
    expect(openToolInDock('d.tool', 'left')).not.toBeNull();
    expect(frame().docks.left.tools).toHaveLength(1);
  });
});
