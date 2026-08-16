import { describe, expect, it } from 'vitest';

import { deserialize, FRAME_SCHEMA, FRAME_VERSION, serialize } from '../serialize';
import { seedFromPreset, type FramePreset } from '../presets';
import { DEFAULT_BACKDROP } from '../types';
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

  it('round-trips a minimized pane, so a tidied workspace stays tidied', () => {
    const frame = JSON.parse(JSON.stringify(seeded())) as FrameState;
    const area = (frame.center as { children: Array<{ tabs?: Array<Record<string, unknown>> }> })
      .children[0];
    area.tabs![0].minimized = true;
    const back = deserialize(serialize(frame), KNOWN)!;
    const backArea = (back.center as { children: Array<{ tabs: Array<{ minimized?: boolean }> }> })
      .children[0];
    expect(backArea.tabs[0].minimized).toBe(true);
    // Absent rather than `false` on the ones that are showing — the flag is
    // additive, and a blob written before minimizing existed has none at all.
    expect(backArea.tabs[1].minimized).toBeUndefined();
  });
});

describe('v1 → v2 migration', () => {
  /**
   * A blob exactly as the pre-desktop build wrote it: `floating[]` with rects that
   * are fractions of the old center grid, no `windows`, no `mode`, no `backdrop`.
   * This is the single most important test in the desktop refactor — it is what
   * proves an existing user's saved workspaces still open.
   */
  function v1Blob() {
    const frame = JSON.parse(JSON.stringify(seeded())) as Record<string, unknown>;
    delete frame.windows;
    delete frame.windowViewport;
    delete frame.mode;
    delete frame.backdrop;
    delete frame.focusedWindowId;
    frame.floating = [
      {
        pane: { instanceId: 'scratch.note#7', viewId: 'scratch.note' },
        rect: { x: 0.2, y: 0.15, w: 0.5, h: 0.55 },
        z: 3,
      },
    ];
    return { schema: FRAME_SCHEMA, version: 1, frame };
  }

  it('opens a v1 workspace as a TILING desktop, unchanged', () => {
    const back = deserialize(v1Blob(), KNOWN)!;
    // The whole point: a workspace saved before the desktop shell existed must
    // come back as the frame its owner left, not as a wallpaper full of windows.
    expect(back.mode).toBe('tiling');
    // The backdrop is the one thing that does NOT come back as it was, because
    // there was nothing there to come back: a v1 desktop had no backdrop, so a
    // migrated one gets the default rather than `none`. Picking `none` here
    // would read as conservative and would in fact hand every upgrading user a
    // blank landing surface for a feature they never turned off.
    expect(back.backdrop).toEqual({ id: DEFAULT_BACKDROP });
    expect(back.docks.left.tools).toHaveLength(1);
    const area = (back.center as { children: Array<{ tabs: unknown[] }> }).children[0];
    expect(area.tabs).toHaveLength(2);
  });

  it('migrates floating panes into windows on a unit viewport', () => {
    const back = deserialize(v1Blob(), KNOWN)!;
    expect(back.windows).toHaveLength(1);
    const w = back.windows[0];
    expect(w.area.tabs.map((t) => t.instanceId)).toEqual(['scratch.note#7']);
    expect(w.mode).toBe('normal');
    expect(w.z).toBe(3);
    // The old fractions are carried through as-is against a 1×1 basis, so the
    // ordinary rescale-on-measure path turns them into pixels with no
    // migration-only arithmetic to get wrong.
    expect(back.windowViewport).toEqual({ w: 1, h: 1 });
    expect(w.rect).toEqual({ x: 0.2, y: 0.15, w: 0.5, h: 0.55 });
  });

  it('mints non-colliding ids for migrated windows', () => {
    const blob = v1Blob();
    (blob.frame.floating as unknown[]).push({
      pane: { instanceId: 'files.tree#8', viewId: 'files.tree' },
      rect: { x: 0.3, y: 0.3, w: 0.4, h: 0.4 },
      z: 4,
    });
    const back = deserialize(blob, KNOWN)!;
    expect(back.windows).toHaveLength(2);
    const ids = back.windows.map((w) => w.id);
    const areaIds = back.windows.map((w) => w.area.id);
    expect(new Set([...ids, ...areaIds]).size).toBe(4);
    // And the counter is past every id it just minted, so the next window opened
    // cannot be handed one of them again.
    for (const id of [...ids, ...areaIds]) {
      expect(Number(id.slice(1))).toBeLessThan(back.paneSeq);
    }
  });

  it('still round-trips at v2 once written back', () => {
    const migrated = deserialize(v1Blob(), KNOWN)!;
    const rewritten = serialize(migrated);
    expect(rewritten.version).toBe(FRAME_VERSION);
    expect(deserialize(rewritten, KNOWN)).toEqual(migrated);
  });

  it('refuses a blob from a newer schema rather than misreading it', () => {
    const blob = serialize(seeded());
    expect(deserialize({ ...blob, version: FRAME_VERSION + 1 }, KNOWN)).toBeNull();
  });
});

describe('window state round-trip', () => {
  it('preserves rect, mode, snap, restoreRect and merged tabs', () => {
    const frame = seeded();
    const withWindow: FrameState = {
      ...frame,
      // Past every id below: the counter is what stops a restored layout minting
      // an id it already uses, and the deserializer repairs it if it isn't.
      paneSeq: 42,
      windowViewport: { w: 1600, h: 900 },
      windows: [
        {
          id: 'w40',
          area: {
            kind: 'area',
            id: 'a41',
            tabs: [
              { instanceId: 'scratch.note#20', viewId: 'scratch.note' },
              { instanceId: 'files.tree#21', viewId: 'files.tree' },
            ],
            activeTab: 1,
          },
          rect: { x: 0, y: 0, w: 800, h: 900 },
          restoreRect: { x: 120, y: 90, w: 640, h: 480 },
          mode: 'normal',
          snap: 'left',
          z: 2,
        },
      ],
      focusedWindowId: 'w40',
    };
    expect(deserialize(serialize(withWindow), KNOWN)).toEqual(withWindow);
  });

  it('drops a focusedWindowId that no longer names a window', () => {
    const frame: FrameState = { ...seeded(), focusedWindowId: 'w-gone' };
    expect(deserialize(serialize(frame), KNOWN)!.focusedWindowId).toBeNull();
  });
});

describe('per-tool dock size', () => {
  it("seeds a view's declared defaultDockSize, unless the preset sets the dock size", () => {
    const sized = seedFromPreset(
      {
        ...preset,
        frame: {
          ...preset.frame,
          // No explicit dock size, so the view's own declared width applies.
          docks: { left: { tools: ['files.tree'] }, right: { tools: ['code.outline'] } },
        },
      },
      { knownViews: KNOWN, dockSizeFor: (v) => (v === 'files.tree' ? 300 : undefined) },
    );
    expect(sized.docks.left.tools[0].dockSize).toBe(300);
    expect(sized.docks.right.tools[0].dockSize).toBeUndefined();

    // An explicit preset size is the author's call for the whole dock and wins.
    const overridden = seedFromPreset(preset, {
      knownViews: KNOWN,
      dockSizeFor: () => 300,
    });
    expect(overridden.docks.left.tools[0].dockSize).toBeUndefined();
    expect(overridden.docks.left.size).toBe(260);
  });

  it("round-trips a docked tool's remembered width", () => {
    const frame = JSON.parse(JSON.stringify(seeded())) as FrameState;
    frame.docks.left.tools[0].dockSize = 340;
    const back = deserialize(serialize(frame), KNOWN)!;
    expect(back.docks.left.tools[0].dockSize).toBe(340);
  });

  it('reads a blob written before per-tool sizing, leaving the field absent', () => {
    // A pre-change blob is exactly a seeded frame with no `dockSize` anywhere:
    // docks carry a size, panes carry none. It must survive at the same version.
    const frame = seeded();
    expect(frame.docks.left.tools[0].dockSize).toBeUndefined();
    const back = deserialize(serialize(frame), KNOWN)!;
    expect(back.docks.left.tools[0].dockSize).toBeUndefined();
    // The dock's own size still drives the render, so nothing resizes on upgrade.
    expect(back.docks.left.size).toBe(260);
  });

  it('rejects a corrupt dockSize rather than rendering a broken dock', () => {
    const frame = JSON.parse(JSON.stringify(seeded())) as FrameState;
    frame.docks.left.tools[0].dockSize = 4;
    const back = deserialize(serialize(frame), KNOWN)!;
    expect(back.docks.left.tools[0].dockSize).toBeUndefined();
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
  it('prunes panes with unknown views from tabs, docks, and windows', () => {
    const frame = seeded();
    const blob = JSON.parse(JSON.stringify(serialize(frame))) as Record<string, unknown>;
    const f = blob.frame as {
      center: { children: Array<{ tabs: Array<{ viewId: string }> }> };
      docks: { left: { tools: Array<{ viewId: string }> } };
      windows: unknown[];
    };
    f.center.children[0].tabs[1].viewId = 'ghost.pane';
    f.docks.left.tools[0].viewId = 'ghost.tool';
    f.windows.push({
      id: 'w90',
      area: {
        kind: 'area',
        id: 'a91',
        tabs: [{ instanceId: 'ghost.win#9', viewId: 'ghost.win' }],
        activeTab: 0,
      },
      rect: { x: 10, y: 10, w: 300, h: 300 },
      mode: 'normal',
      z: 1,
    });
    const back = deserialize(blob, KNOWN)!;
    const area = (back.center as { children: Array<{ tabs: unknown[] }> }).children[0];
    expect(area.tabs).toHaveLength(1);
    expect(back.docks.left.tools).toHaveLength(0);
    expect(back.docks.left.visible).toBe(false);
    // The whole window goes, not just its tab — a titlebar with nothing under it
    // is not a state the running app can produce.
    expect(back.windows).toHaveLength(0);
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

  it('drops areas a prune emptied instead of leaving a placeholder', () => {
    const frame = seeded();
    const blob = JSON.parse(JSON.stringify(serialize(frame))) as Record<string, unknown>;
    const f = blob.frame as {
      center: { children: Array<{ tabs: Array<{ viewId: string }> }> };
    };
    // The second area holds one pane; retire its view and the area has nothing left.
    f.center.children[1].tabs[0].viewId = 'ghost.pane';
    const back = deserialize(blob, KNOWN)!;
    // The split collapses to the one surviving area rather than keeping an empty one.
    expect(back.center.kind).toBe('area');
    expect((back.center as { tabs: unknown[] }).tabs).toHaveLength(2);
  });
});

// A saved layout outlives the code that wrote it: a workspace naming a view that
// has since been merged away must keep working, not open with holes in it.
describe('deserialize view renames', () => {
  const RENAMED = new Set(['games.lobby', 'games.log', 'scratch.note']);

  function blobWith(viewIds: string[]): Record<string, unknown> {
    return {
      schema: FRAME_SCHEMA,
      version: FRAME_VERSION,
      frame: {
        center: {
          kind: 'split',
          id: 's1',
          orientation: 'row',
          sizes: viewIds.map(() => 1 / viewIds.length),
          children: viewIds.map((viewId, i) => ({
            kind: 'area',
            id: `a${i + 1}`,
            tabs: [{ instanceId: `${viewId}#${i + 1}`, viewId }],
            activeTab: 0,
          })),
        },
        docks: {},
        floating: [],
        focusedAreaId: 'a1',
        fullscreenAreaId: null,
        paneSeq: 9,
      },
    };
  }

  function viewIdsOf(node: FrameState['center']): string[] {
    if (node.kind === 'area') return node.tabs.map((t) => t.viewId);
    return node.children.flatMap(viewIdsOf);
  }

  it('renames a retired view to its replacement', () => {
    const back = deserialize(blobWith(['games.thoughts', 'scratch.note']), RENAMED)!;
    expect(viewIdsOf(back.center)).toEqual(['games.log', 'scratch.note']);
  });

  it('collapses two retired views that merged into one pane', () => {
    // games.board and games.loadout both became sections of games.lobby.
    const back = deserialize(blobWith(['games.board', 'games.loadout']), RENAMED)!;
    expect(viewIdsOf(back.center)).toEqual(['games.lobby']);
  });

  it('keeps the pane the layout already placed over a renamed duplicate', () => {
    const back = deserialize(blobWith(['games.lobby', 'games.board']), RENAMED)!;
    expect(viewIdsOf(back.center)).toEqual(['games.lobby']);
  });

  it('renames region views and their activeView', () => {
    const blob = blobWith(['scratch.note']);
    const center = (blob.frame as { center: { children: Array<{ tabs: Array<object> }> } }).center;
    center.children[0].tabs[0] = {
      instanceId: 'scratch.note#1',
      viewId: 'scratch.note',
      regions: {
        right: {
          open: true,
          size: 300,
          collapsed: false,
          views: ['games.thoughts'],
          activeView: 'games.thoughts',
        },
      },
    };
    const back = deserialize(blob, RENAMED)!;
    const pane = (back.center as { tabs: Array<{ regions?: Record<string, RegionState> }> })
      .tabs[0];
    expect(pane.regions?.right.views).toEqual(['games.log']);
    expect(pane.regions?.right.activeView).toBe('games.log');
  });
});
