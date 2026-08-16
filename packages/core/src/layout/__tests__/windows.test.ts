import { describe, expect, it } from 'vitest';

import { collectAreas, createEmptyFrame, insertPane, listPanes } from '../model';
import { seedFromPreset, type FramePreset } from '../presets';
import { explodeToWindows, tileWindows } from '../windows';
import type { DockSide, FrameState, PaneState } from '../types';

const KNOWN = new Set(['scratch.note', 'files.tree', 'editor.buffer', 'dashboard.welcome']);
const VIEW = { w: 1600, h: 900 };
const DOCK_FOR: Record<string, DockSide> = { 'files.tree': 'left' };

const preset: FramePreset = {
  id: 'test',
  name: 'Test',
  frame: {
    center: {
      split: 'row',
      sizes: [0.6, 0.4],
      children: [{ tabs: ['editor.buffer', 'scratch.note'] }, { pane: 'dashboard.welcome' }],
    },
    docks: { left: { tools: ['files.tree'], size: 260, visible: true } },
  },
};

const seeded = (): FrameState => seedFromPreset(preset, { knownViews: KNOWN });
const ids = (frame: FrameState): string[] =>
  listPanes(frame)
    .map((p) => p.pane.instanceId)
    .sort();

describe('explodeToWindows (tiling → floating)', () => {
  it('makes one window per non-empty area, carrying its tabs whole', () => {
    const out = explodeToWindows(seeded(), VIEW);
    // Two center areas + one docked tool.
    expect(out.windows).toHaveLength(3);
    const tabbed = out.windows.find((w) => w.area.tabs.length === 2);
    expect(tabbed?.area.tabs.map((t) => t.viewId)).toEqual(['editor.buffer', 'scratch.note']);
  });

  it('positions windows at the pixel rect their area occupied', () => {
    const out = explodeToWindows(seeded(), VIEW);
    // The 0.6/0.4 row split becomes a 960px window beside a 640px one, so the
    // flip is visually continuous rather than a pile of cascaded boxes.
    const widths = out.windows.map((w) => w.rect.w).sort((a, b) => a - b);
    expect(widths).toContain(960);
    expect(widths).toContain(640);
  });

  it('minimizes a hidden dock tool rather than dropping or showing it', () => {
    const frame = seeded();
    const hidden: FrameState = {
      ...frame,
      docks: { ...frame.docks, left: { ...frame.docks.left, visible: false } },
    };
    const out = explodeToWindows(hidden, VIEW);
    const tool = out.windows.find((w) => w.area.tabs[0]?.viewId === 'files.tree');
    expect(tool?.mode).toBe('minimized');
  });

  it('empties the center tree and the docks, losing no pane', () => {
    const before = seeded();
    const out = explodeToWindows(before, VIEW);
    expect(collectAreas(out.center)).toHaveLength(1);
    expect(collectAreas(out.center)[0].tabs).toHaveLength(0);
    expect(out.docks.left.tools).toHaveLength(0);
    expect(ids(out)).toEqual(ids(before));
  });

  it('mints ids past the counter so nothing collides', () => {
    const out = explodeToWindows(seeded(), VIEW);
    const all = [...out.windows.map((w) => w.id), ...out.windows.map((w) => w.area.id)];
    expect(new Set(all).size).toBe(all.length);
    for (const id of all) expect(Number(id.slice(1))).toBeLessThan(out.paneSeq);
  });
});

describe('tileWindows (floating → tiling)', () => {
  it('returns every pane to the frame, losing none', () => {
    const before = seeded();
    const floated = explodeToWindows(before, VIEW);
    const back = tileWindows(floated, DOCK_FOR);
    expect(back.windows).toHaveLength(0);
    expect(ids(back)).toEqual(ids(before));
  });

  it('sends tools back to their dock instead of into the document grid', () => {
    const back = tileWindows(explodeToWindows(seeded(), VIEW), DOCK_FOR);
    expect(back.docks.left.tools.map((t) => t.viewId)).toEqual(['files.tree']);
    expect(back.docks.left.activeTool).toBe(back.docks.left.tools[0].instanceId);
    for (const area of collectAreas(back.center)) {
      expect(area.tabs.some((t) => t.viewId === 'files.tree')).toBe(false);
    }
  });

  it('keeps a merged window merged: a tabbed window becomes a tabbed area', () => {
    const back = tileWindows(explodeToWindows(seeded(), VIEW), DOCK_FOR);
    const tabbed = collectAreas(back.center).find((a) => a.tabs.length === 2);
    expect(tabbed?.tabs.map((t) => t.viewId)).toEqual(['editor.buffer', 'scratch.note']);
  });

  it('lands a minimized window as a background tab, still mounted', () => {
    // Minimized meant "mounted but not showing"; a background tab is the closest
    // thing the tiling frame has, and dropping it would lose a live pane.
    const frame = createEmptyFrame();
    const pane: PaneState = { instanceId: 'scratch.note#1', viewId: 'scratch.note' };
    const other: PaneState = { instanceId: 'editor.buffer#2', viewId: 'editor.buffer' };
    const withWindows: FrameState = {
      ...frame,
      windows: [
        {
          id: 'w5',
          area: { kind: 'area', id: 'a6', tabs: [other], activeTab: 0 },
          rect: { x: 0, y: 0, w: 800, h: 600 },
          mode: 'normal',
          z: 1,
        },
        {
          id: 'w7',
          area: { kind: 'area', id: 'a8', tabs: [pane], activeTab: 0 },
          rect: { x: 40, y: 40, w: 400, h: 300 },
          mode: 'minimized',
          z: 2,
        },
      ],
      paneSeq: 9,
    };
    const back = tileWindows(withWindows, {});
    expect(ids(back)).toEqual(['editor.buffer#2', 'scratch.note#1']);
    const holder = collectAreas(back.center).find((a) =>
      a.tabs.some((t) => t.instanceId === 'scratch.note#1'),
    )!;
    // Present, but not the tab on screen.
    expect(holder.tabs[holder.activeTab].instanceId).not.toBe('scratch.note#1');
  });

  it('is a no-op when there are no windows', () => {
    const frame = seeded();
    expect(tileWindows(frame, DOCK_FOR)).toBe(frame);
  });
});

describe('the round trip', () => {
  it('preserves the set of open panes in both directions', () => {
    // This is the ONLY invariant the round trip actually guarantees: split ratios
    // are lost going out and exact rects are lost coming back. Asserting more
    // would be asserting a lie.
    const before = seeded();
    const there = explodeToWindows(before, VIEW);
    const back = tileWindows(there, DOCK_FOR);
    const again = explodeToWindows(back, VIEW);
    expect(ids(again)).toEqual(ids(before));
  });

  it('never duplicates a pane instance', () => {
    const frame = insertPane(seeded().center, collectAreas(seeded().center)[1].id, {
      instanceId: 'scratch.note#99',
      viewId: 'scratch.note',
    });
    const withExtra: FrameState = { ...seeded(), center: frame!, paneSeq: 100 };
    const out = tileWindows(explodeToWindows(withExtra, VIEW), DOCK_FOR);
    const all = listPanes(out).map((p) => p.pane.instanceId);
    expect(new Set(all).size).toBe(all.length);
  });
});
