import { describe, expect, it } from 'vitest';

import { deserialize, FRAME_SCHEMA, FRAME_VERSION, serialize } from '../serialize';
import { seedFromPreset, type FramePreset } from '../presets';
import type { FrameState, RegionState } from '../types';

const KNOWN = new Set([
  'scratch.note',
  'files.tree',
  'editor.buffer',
  'code.outline',
  'dashboard.welcome',
]);

const preset: FramePreset = {
  id: 'test',
  name: 'Test',
  frame: {
    center: {
      split: 'row',
      sizes: [0.6, 0.4],
      children: [{ tabs: ['editor.buffer', 'scratch.note'] }, { pane: 'dashboard.welcome' }],
    },
    docks: { left: { tools: ['files.tree'], size: 260 } },
  },
};

const region: RegionState = {
  open: true,
  size: 300,
  collapsed: false,
  views: ['code.outline'],
  activeView: 'code.outline',
};

function seeded(): FrameState {
  return seedFromPreset(preset, { knownViews: KNOWN });
}

describe('serialize round-trip', () => {
  it('survives serialize → deserialize intact', () => {
    const frame = seeded();
    const back = deserialize(serialize(frame), KNOWN);
    expect(back).toEqual(frame);
  });

  it('round-trips region state per pane instance', () => {
    // Attach a region to the first editor tab.
    const first = JSON.parse(JSON.stringify(seeded())) as FrameState;
    const area = (first.center as { children: Array<{ tabs?: Array<Record<string, unknown>> }> })
      .children[0];
    area.tabs![0].regions = { right: region };
    const back = deserialize(serialize(first), KNOWN)!;
    const backArea = (back.center as { children: Array<{ tabs: Array<{ regions?: unknown }> }> })
      .children[0];
    expect(backArea.tabs[0].regions).toEqual({ right: region });
  });
});

describe('deserialize rejects non-frame blobs', () => {
  it('rejects a legacy dockview layout', () => {
    expect(deserialize({ grid: { root: {} }, panels: { a: {} } }, KNOWN)).toBeNull();
  });

  it('rejects null, wrong schema, and future versions', () => {
    expect(deserialize(null, KNOWN)).toBeNull();
    expect(deserialize({ schema: 'other', version: 1, frame: {} }, KNOWN)).toBeNull();
    expect(
      deserialize({ schema: FRAME_SCHEMA, version: FRAME_VERSION + 1, frame: {} }, KNOWN),
    ).toBeNull();
  });
});

describe('deserialize pruning', () => {
  it('prunes panes with unknown views from tabs, docks, and floating', () => {
    const frame = seeded();
    const blob = JSON.parse(JSON.stringify(serialize(frame))) as Record<string, unknown>;
    const f = blob.frame as {
      center: { children: Array<{ tabs: Array<{ viewId: string }> }> };
      docks: { left: { tools: Array<{ viewId: string }> } };
      floating: unknown[];
    };
    f.center.children[0].tabs[1].viewId = 'ghost.pane';
    f.docks.left.tools[0].viewId = 'ghost.tool';
    f.floating.push({
      pane: { instanceId: 'ghost.float#9', viewId: 'ghost.float' },
      rect: { x: 0.1, y: 0.1, w: 0.3, h: 0.3 },
      z: 1,
    });
    const back = deserialize(blob, KNOWN)!;
    const area = (back.center as { children: Array<{ tabs: unknown[] }> }).children[0];
    expect(area.tabs).toHaveLength(1);
    expect(back.docks.left.tools).toHaveLength(0);
    expect(back.docks.left.visible).toBe(false);
    expect(back.floating).toHaveLength(0);
  });

  it('prunes unknown region views and repairs activeView', () => {
    const frame = seeded();
    const blob = JSON.parse(JSON.stringify(serialize(frame))) as Record<string, unknown>;
    const f = blob.frame as {
      center: { children: Array<{ tabs: Array<Record<string, unknown>> }> };
    };
    f.center.children[0].tabs[0].regions = {
      right: { ...region, views: ['ghost.view', 'code.outline'], activeView: 'ghost.view' },
      bottom: { ...region, views: ['ghost.only'], activeView: 'ghost.only' },
    };
    const back = deserialize(blob, KNOWN)!;
    const tab = (
      back.center as { children: Array<{ tabs: Array<{ regions?: Record<string, RegionState> }> }> }
    ).children[0].tabs[0];
    expect(tab.regions?.right.views).toEqual(['code.outline']);
    expect(tab.regions?.right.activeView).toBe('code.outline');
    expect(tab.regions?.bottom).toBeUndefined();
  });

  it('recovers paneSeq past every surviving id', () => {
    const frame = seeded();
    const blob = JSON.parse(JSON.stringify(serialize(frame))) as Record<string, unknown>;
    (blob.frame as { paneSeq: number }).paneSeq = 0;
    const back = deserialize(blob, KNOWN)!;
    expect(back.paneSeq).toBeGreaterThan(0);
    // Every allocatable id must be fresh.
    const ids = new Set<string>();
    const walk = (node: FrameState['center']): void => {
      ids.add(node.id);
      if (node.kind === 'split') node.children.forEach(walk);
    };
    walk(back.center);
    expect(ids.has(`a${back.paneSeq}`)).toBe(false);
  });

  it('repairs focus/fullscreen pointing at pruned areas', () => {
    const frame = seeded();
    const blob = JSON.parse(JSON.stringify(serialize(frame))) as Record<string, unknown>;
    (blob.frame as { focusedAreaId: string }).focusedAreaId = 'a999';
    (blob.frame as { fullscreenAreaId: string }).fullscreenAreaId = 'a999';
    const back = deserialize(blob, KNOWN)!;
    expect(back.fullscreenAreaId).toBeNull();
    expect(back.focusedAreaId).not.toBe('a999');
  });
});
