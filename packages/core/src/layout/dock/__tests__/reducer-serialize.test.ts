/**
 * The workbench reducer and the v4 blob: no-ops keep the same reference (so
 * nothing re-renders and autosave stays clean), and a saved layout comes back as
 * it was — or, from an older version, migrated without losing a pane.
 */
import { describe, expect, it } from 'vitest';

import { createEmptyWindowFrame as createEmptyFrame } from '../../legacy-frame';
import { createArea } from '../../model';
import { serialize } from '../../serialize';
import type { PaneRole, PaneState } from '../../types';
import { createEmptyDockFrame, findPane, listPanes } from '../frame';
import { dockReduce } from '../reducer';
import type { DockAction } from '../reducer';
import { DOCK_FRAME_VERSION, deserializeDock, serializeDock } from '../serialize';
import { collectAreas } from '../tree';
import type { DockFrame, SplitNode } from '../types';

const pane = (id: string): PaneState => ({ instanceId: id, viewId: id.split('#')[0] });
const run = (...actions: DockAction[]): DockFrame =>
  actions.reduce(dockReduce, createEmptyDockFrame());

const ROLES: Record<string, PaneRole> = { doc: 'document', note: 'document', term: 'tool' };
const opts = { roleOf: (viewId: string) => ROLES[viewId] };
const KNOWN = new Set(Object.keys(ROLES));

describe('dockReduce', () => {
  const base = run(
    { type: 'INSERT_PANE', pane: pane('doc#4'), target: { kind: 'center' } },
    { type: 'INSERT_PANE', pane: pane('note#6'), target: { kind: 'center' } },
    { type: 'INSERT_PANE', pane: pane('term#8'), target: { kind: 'dock', side: 'bottom' } },
  );

  it('returns the same frame for every no-op', () => {
    const home = collectAreas(base.center)[0];
    const noops: DockAction[] = [
      { type: 'INSERT_PANE', pane: pane('doc#4'), target: { kind: 'center' } },
      { type: 'REMOVE_PANE', instanceId: 'nope#1' },
      { type: 'MOVE_PANE', instanceId: 'term#8', target: { kind: 'dock', side: 'bottom' } },
      { type: 'SET_ACTIVE_TAB', areaId: home.id, index: home.activeTab },
      { type: 'SET_ACTIVE_TAB', areaId: home.id, index: 9 },
      { type: 'REORDER_TAB', areaId: home.id, from: 0, to: 0 },
      { type: 'RETARGET_PANE', instanceId: 'doc#4', newInstanceId: 'note#6' },
      { type: 'SET_SECTION', instanceId: 'nope#1', section: 'x' },
      { type: 'SET_PANE_ATTENTION', instanceId: 'doc#4', attention: false },
      { type: 'FOCUS_PANE', instanceId: 'term#8' },
      { type: 'SET_PRESENTED', instanceId: 'nope#1' },
      { type: 'SET_DOCK_VISIBLE', side: 'left', visible: true },
      { type: 'SET_DOCK_SIZE', side: 'bottom', size: base.docks.bottom.size },
      { type: 'SET_POPOUT_BOUNDS', popoutId: 'p99', bounds: { x: 0, y: 0, w: 500, h: 500 } },
      { type: 'SET_BACKDROP', backdrop: base.backdrop },
    ];
    for (const action of noops) expect(dockReduce(base, action), action.type).toBe(base);
  });

  it('steps the counter past the pane id it was handed', () => {
    const before = createEmptyDockFrame();
    const after = dockReduce(before, {
      type: 'INSERT_PANE',
      pane: pane(`doc#${before.paneSeq}`),
      target: { kind: 'split', areaId: collectAreas(before.center)[0].id, side: 'right' },
    });
    // The pane took `paneSeq`, so the new group and split start after it.
    const ids = collectAreas(after.center).map((a) => Number(a.id.slice(1)));
    expect(Math.min(...ids.filter((n) => n !== 0))).toBe(before.paneSeq + 1);
  });

  it('carries focus across a retarget', () => {
    const next = dockReduce(base, {
      type: 'RETARGET_PANE',
      instanceId: 'note#6',
      newInstanceId: 'note#file.md',
      params: { path: 'file.md' },
    });
    expect(next.focusedInstanceId).not.toBe('note#6');
    const focused = dockReduce(next, { type: 'FOCUS_PANE', instanceId: 'note#file.md' });
    expect(findPane(focused, 'note#file.md')!.pane.params).toEqual({ path: 'file.md' });
    // Focus on the retargeted pane follows the rename.
    const viaFocus = dockReduce(dockReduce(base, { type: 'FOCUS_PANE', instanceId: 'note#6' }), {
      type: 'RETARGET_PANE',
      instanceId: 'note#6',
      newInstanceId: 'note#x',
    });
    expect(viaFocus.focusedInstanceId).toBe('note#x');
  });

  it('focusing an already-focused pane in a hidden dock still shows the dock', () => {
    const hidden = dockReduce(base, { type: 'SET_DOCK_VISIBLE', side: 'bottom', visible: false });
    // Hiding moved focus to the centre; force it back to the dock pane without showing it.
    const stale: DockFrame = {
      ...hidden,
      focusedInstanceId: 'term#8',
      focusedAreaId: hidden.docks.bottom.area.id,
    };
    expect(
      dockReduce(stale, { type: 'FOCUS_PANE', instanceId: 'term#8' }).docks.bottom.visible,
    ).toBe(true);
  });

  it('docks a popout back by the routing it is given', () => {
    const out = dockReduce(base, {
      type: 'MOVE_PANE',
      instanceId: 'term#8',
      target: { kind: 'popout' },
    });
    expect(out.popouts).toHaveLength(1);
    const back = dockReduce(out, {
      type: 'DOCK_POPOUT',
      popoutId: out.popouts[0].id,
      targets: { 'term#8': { kind: 'dock', side: 'bottom' } },
    });
    expect(findPane(back, 'term#8')!.location).toMatchObject({ kind: 'dock', side: 'bottom' });
    expect(back.docks.bottom.visible).toBe(true);
  });
});

describe('v4 round trip', () => {
  const frame = run(
    { type: 'INSERT_PANE', pane: pane('doc#4'), target: { kind: 'center' } },
    {
      type: 'INSERT_PANE',
      pane: pane('note#6'),
      target: { kind: 'split', areaId: 'a0', side: 'right' },
    },
    { type: 'INSERT_PANE', pane: pane('term#10'), target: { kind: 'dock', side: 'bottom' } },
    { type: 'SET_DOCK_SIZE', side: 'bottom', size: 300 },
    {
      type: 'INSERT_PANE',
      pane: pane('doc#3'),
      target: { kind: 'popout', bounds: { x: 1700, y: 40, w: 900, h: 700 } },
    },
    { type: 'TOGGLE_MAXIMIZED', areaId: 'a0' },
  );

  it('writes v4 and drops maximize and presentation', () => {
    const blob = serializeDock({ ...frame, presentedInstanceId: 'doc#4' });
    expect(blob.version).toBe(DOCK_FRAME_VERSION);
    expect(blob.frame).not.toHaveProperty('maximizedAreaId');
    expect(blob.frame).not.toHaveProperty('presentedInstanceId');
  });

  it('reads back what it wrote', () => {
    const back = deserializeDock(JSON.parse(JSON.stringify(serializeDock(frame))), KNOWN, opts)!;
    expect(back).toEqual({ ...frame, maximizedAreaId: null, presentedInstanceId: null });
  });

  it('prunes uninstalled views and drops the groups they emptied', () => {
    const blob = serializeDock(frame);
    const back = deserializeDock(blob, new Set(['doc', 'term']), opts)!;
    expect(
      listPanes(back)
        .map((p) => p.pane.instanceId)
        .sort(),
    ).toEqual(['doc#3', 'doc#4', 'term#10']);
    // The split held note#6 alone on one side; it collapses to one group.
    expect(back.center.kind).toBe('area');
  });

  it('a popout whose only pane is gone is dropped, and an empty centre stays a group', () => {
    const back = deserializeDock(serializeDock(frame), new Set(['term']), opts)!;
    expect(back.popouts).toEqual([]);
    expect(back.center).toMatchObject({ kind: 'area', tabs: [] });
    // The fresh centre group's id does not collide with anything that survived.
    expect(back.center.id).not.toBe(back.docks.bottom.area.id);
    expect(back.paneSeq).toBeGreaterThan(Number(back.center.id.slice(1)));
  });

  it('salvages a damaged blob instead of discarding it', () => {
    const blob = serializeDock(frame);
    const f = blob.frame as Record<string, unknown>;
    const center = f.center as SplitNode;
    const damaged = {
      ...blob,
      frame: {
        ...f,
        center: { ...center, id: undefined, sizes: ['x', -1] },
        docks: { bottom: { ...(f.docks as DockFrame['docks']).bottom, size: 3 } },
        popouts: [
          { ...((f.popouts as unknown[])[0] as object), bounds: { x: 0, y: 0, w: 5, h: 5 } },
        ],
      },
    };
    const back = deserializeDock(damaged, KNOWN, opts)!;
    const root = back.center as SplitNode;
    expect(root.sizes).toEqual([0.5, 0.5]);
    expect(root.id).toMatch(/^s\d+$/);
    expect(back.docks.bottom.size).toBe(240);
    expect(back.docks.left.area.tabs).toEqual([]);
    expect(back.popouts[0].bounds).toBeNull();
    expect(listPanes(back)).toHaveLength(4);
  });

  it('rejects a newer version and a foreign schema', () => {
    const blob = serializeDock(frame);
    expect(deserializeDock({ ...blob, version: 5 }, KNOWN, opts)).toBeNull();
    expect(deserializeDock({ ...blob, schema: 'dockview' }, KNOWN, opts)).toBeNull();
  });
});

describe('older blobs', () => {
  it('a v3 desktop migrates to docks with every pane kept', () => {
    const v3 = serialize({
      ...createEmptyFrame(),
      windows: [
        {
          id: 'w1',
          area: { ...createArea('a2'), tabs: [pane('doc#3')] },
          rect: { x: 0, y: 0, w: 800, h: 900 },
          mode: 'normal',
          snap: 'left',
          z: 1,
        },
        {
          id: 'w4',
          area: { ...createArea('a5'), tabs: [pane('term#6')] },
          rect: { x: 0, y: 650, w: 1600, h: 250 },
          mode: 'normal',
          z: 2,
        },
      ],
      windowViewport: { w: 1600, h: 900 },
      paneSeq: 7,
    });
    expect(v3.version).toBe(3);
    const back = deserializeDock(v3, KNOWN, opts)!;
    expect(findPane(back, 'doc#3')!.location.kind).toBe('center');
    expect(findPane(back, 'term#6')!.location).toMatchObject({ kind: 'dock', side: 'bottom' });
    expect(back.docks.bottom.visible).toBe(true);
  });

  it('a v2 tiling blob still loads, through both migrations', () => {
    const v2 = {
      schema: 'horrible.frame',
      version: 2,
      frame: {
        mode: 'tiling',
        center: {
          kind: 'split',
          id: 's9',
          orientation: 'row',
          children: [
            { kind: 'area', id: 'a1', tabs: [pane('doc#2')], activeTab: 0 },
            { kind: 'area', id: 'a3', tabs: [pane('note#4')], activeTab: 0 },
          ],
          sizes: [0.5, 0.5],
        },
        docks: {
          bottom: { visible: true, size: 240, tools: [pane('term#5')], activeTool: 'term#5' },
        },
        windows: [],
        paneSeq: 10,
      },
    };
    const back = deserializeDock(v2, KNOWN, opts)!;
    expect(
      listPanes(back)
        .map((p) => p.pane.instanceId)
        .sort(),
    ).toEqual(['doc#2', 'note#4', 'term#5']);
    expect(findPane(back, 'term#5')!.location.kind).toBe('dock');
  });
});
