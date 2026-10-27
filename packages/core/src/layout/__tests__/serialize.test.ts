import { describe, expect, it } from 'vitest';

import { listPanes } from '../model';
import { deserialize, FRAME_SCHEMA, FRAME_VERSION, serialize } from '../serialize';
import { DEFAULT_BACKDROP } from '../types';
import type { FrameState, RegionState } from '../types';
import { windowsFrame } from './fixtures';

const KNOWN = new Set([
  'scratch.note',
  'files.tree',
  'editor.buffer',
  'code.outline',
  'dashboard.welcome',
]);

const region: RegionState = {
  open: true,
  size: 300,
  collapsed: false,
  views: ['code.outline'],
  activeView: 'code.outline',
};

function frame(): FrameState {
  return windowsFrame([['editor.buffer', 'scratch.note'], 'dashboard.welcome']);
}

const viewIds = (f: FrameState) => listPanes(f).map((p) => p.pane.viewId);

describe('serialize round-trip', () => {
  it('survives serialize → deserialize intact', () => {
    const f = frame();
    expect(deserialize(serialize(f), KNOWN)).toEqual(f);
  });

  it('writes the current version and nothing the tiling frame used to carry', () => {
    const blob = serialize(frame());
    expect(blob.version).toBe(FRAME_VERSION);
    for (const key of ['center', 'docks', 'mode', 'fullscreenAreaId', 'focusedAreaId']) {
      expect(blob.frame).not.toHaveProperty(key);
    }
  });

  it('does not persist a presented pane', () => {
    const f = { ...frame(), presentedInstanceId: 'editor.buffer#1' };
    expect(serialize(f).frame).not.toHaveProperty('presentedInstanceId');
    expect(deserialize(serialize(f), KNOWN)!.presentedInstanceId).toBeNull();
  });

  it('round-trips region state per pane instance', () => {
    const f = frame();
    f.windows[0].area.tabs[0].regions = { right: region };
    const back = deserialize(serialize(f), KNOWN)!;
    expect(back.windows[0].area.tabs[0].regions).toEqual({ right: region });
    expect(back.windows[0].area.tabs[1].regions).toBeUndefined();
  });

  it('preserves rect, mode, snap, restoreRect, minimizedFrom and merged tabs', () => {
    const f: FrameState = {
      ...frame(),
      windowViewport: { w: 1600, h: 900 },
      windows: [
        {
          ...frame().windows[0],
          rect: { x: 0, y: 0, w: 800, h: 900 },
          restoreRect: { x: 120, y: 90, w: 640, h: 480 },
          snap: 'left',
          area: { ...frame().windows[0].area, activeTab: 1 },
        },
        {
          ...frame().windows[1],
          mode: 'minimized',
          minimizedFrom: { mode: 'maximized' },
        },
      ],
      focusedWindowId: frame().windows[0].id,
    };
    expect(deserialize(serialize(f), KNOWN)).toEqual(f);
  });

  it('drops a focusedWindowId that no longer names a window', () => {
    const f: FrameState = { ...frame(), focusedWindowId: 'w-gone' };
    expect(deserialize(serialize(f), KNOWN)!.focusedWindowId).toBeNull();
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

/**
 * Blobs written by the tiling frame. Every saved layout has one: a tiling desktop's
 * panes lived in `center` and `docks`, and even a floating desktop kept a (hidden)
 * centre tree. Losing them would close the user's panes on upgrade.
 */
describe('migrating a tiling-era layout', () => {
  const area = (id: string, viewIds: string[], extra: Record<string, unknown> = {}) => ({
    kind: 'area',
    id,
    tabs: viewIds.map((viewId, i) => ({ instanceId: `${viewId}#${id}${i}`, viewId, ...extra })),
    activeTab: 0,
  });

  /** A v2 tiling desktop: editor | welcome at 60/40, a visible left dock, a window. */
  function v2Tiling(mode: 'tiling' | 'floating' = 'tiling') {
    return {
      schema: FRAME_SCHEMA,
      version: 2,
      frame: {
        center: {
          kind: 'split',
          id: 's1',
          orientation: 'row',
          sizes: [0.6, 0.4],
          children: [
            area('a2', ['editor.buffer', 'scratch.note']),
            area('a3', ['dashboard.welcome']),
          ],
        },
        docks: {
          left: {
            visible: true,
            size: 260,
            tools: [{ instanceId: 'files.tree#4', viewId: 'files.tree' }],
            activeTool: 'files.tree#4',
          },
          right: {
            visible: false,
            size: 320,
            tools: [{ instanceId: 'code.outline#5', viewId: 'code.outline' }],
            activeTool: 'code.outline#5',
          },
        },
        windows: [
          {
            id: 'w6',
            area: {
              kind: 'area',
              id: 'a7',
              tabs: [{ instanceId: 'scratch.note#8', viewId: 'scratch.note' }],
              activeTab: 0,
            },
            rect: { x: 100, y: 100, w: 400, h: 300 },
            mode: 'normal',
            z: 1,
          },
        ],
        windowViewport: { w: 1000, h: 800 },
        mode,
        backdrop: { id: 'aurora' },
        fullscreenAreaId: null,
        focusedAreaId: 'a2',
        focusedInstanceId: null,
        focusedWindowId: null,
        paneSeq: 9,
      },
    };
  }

  it('keeps every pane', () => {
    const back = deserialize(v2Tiling(), KNOWN)!;
    expect(viewIds(back).sort()).toEqual(
      [
        'code.outline',
        'dashboard.welcome',
        'editor.buffer',
        'files.tree',
        'scratch.note',
        'scratch.note',
      ].sort(),
    );
    expect(back.backdrop).toEqual({ id: 'aurora' });
  });

  it('turns each centre area into a window at the pixels it occupied', () => {
    const back = deserialize(v2Tiling(), KNOWN)!;
    const editor = back.windows.find((w) => w.area.tabs[0].viewId === 'editor.buffer')!;
    // Tabs travel together: a tabbed area becomes a tabbed window.
    expect(editor.area.tabs.map((t) => t.viewId)).toEqual(['editor.buffer', 'scratch.note']);
    // The grid sat beside the visible 260px left dock, not under it: 0.6 of 740.
    expect(editor.rect).toEqual({ x: 260, y: 0, w: 444, h: 800 });
    expect(editor.mode).toBe('normal');
    const welcome = back.windows.find((w) => w.area.tabs[0].viewId === 'dashboard.welcome')!;
    expect(welcome.rect).toEqual({ x: 704, y: 0, w: 296, h: 800 });
  });

  it('lays the grid out beside only the docks that were showing', () => {
    // The right dock was hidden, so it takes no room; on a floating desktop no dock
    // showed at all, but its grid is minimized, so only a tiling one is checked.
    const blob = v2Tiling();
    (blob.frame.docks as Record<string, { visible: boolean }>).left.visible = false;
    const back = deserialize(blob, KNOWN)!;
    const editor = back.windows.find((w) => w.area.tabs[0].viewId === 'editor.buffer')!;
    expect(editor.rect).toEqual({ x: 0, y: 0, w: 600, h: 800 });
  });

  it('shows a visible dock’s tool at the dock’s geometry and minimizes the rest', () => {
    const back = deserialize(v2Tiling(), KNOWN)!;
    const files = back.windows.find((w) => w.area.tabs[0].viewId === 'files.tree')!;
    expect(files.mode).toBe('normal');
    expect(files.rect).toEqual({ x: 0, y: 0, w: 260, h: 800 });
    const outline = back.windows.find((w) => w.area.tabs[0].viewId === 'code.outline')!;
    expect(outline.mode).toBe('minimized');
  });

  it('keeps the windows the desktop already had in front of the migrated ones', () => {
    const back = deserialize(v2Tiling(), KNOWN)!;
    const original = back.windows.find((w) => w.id === 'w6')!;
    expect(original.z).toBe(Math.max(...back.windows.map((w) => w.z)));
  });

  it('minimizes everything that was hidden on a floating desktop', () => {
    // A floating desktop never drew its centre tree or docks; the taskbar already
    // called those panes minimized, and they stay that way.
    const back = deserialize(v2Tiling('floating'), KNOWN)!;
    const migrated = back.windows.filter((w) => w.id !== 'w6');
    expect(migrated).toHaveLength(4);
    expect(migrated.every((w) => w.mode === 'minimized')).toBe(true);
  });

  it('minimizes an area whose panes the user had all minimized', () => {
    const blob = v2Tiling();
    blob.frame.center.children[1] = area('a3', ['dashboard.welcome'], { minimized: true });
    const back = deserialize(blob, KNOWN)!;
    expect(back.windows.find((w) => w.area.tabs[0].viewId === 'dashboard.welcome')!.mode).toBe(
      'minimized',
    );
  });

  it('mints ids that collide with nothing, and moves the counter past them', () => {
    const back = deserialize(v2Tiling(), KNOWN)!;
    const ids = back.windows.flatMap((w) => [w.id, w.area.id]);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(Number(id.slice(1))).toBeLessThan(back.paneSeq);
  });

  it('prunes unknown views in the old tree, and drops an area left empty', () => {
    const blob = v2Tiling();
    blob.frame.center.children[1] = area('a3', ['ghost.pane']);
    const back = deserialize(blob, KNOWN)!;
    expect(viewIds(back)).not.toContain('ghost.pane');
    expect(back.windows).toHaveLength(4);
  });

  it('writes the migrated layout back at the current version, and it round-trips', () => {
    const migrated = deserialize(v2Tiling(), KNOWN)!;
    const rewritten = serialize(migrated);
    expect(rewritten.version).toBe(FRAME_VERSION);
    expect(deserialize(rewritten, KNOWN)).toEqual(migrated);
  });

  describe('from v1', () => {
    /**
     * A blob exactly as the pre-desktop build wrote it: `floating[]` with rects that
     * are fractions of the old centre grid, no `windows`, no `mode`, no `backdrop`.
     */
    function v1Blob() {
      return {
        schema: FRAME_SCHEMA,
        version: 1,
        frame: {
          center: area('a1', ['editor.buffer']),
          docks: {},
          floating: [
            {
              pane: { instanceId: 'scratch.note#7', viewId: 'scratch.note' },
              rect: { x: 0.2, y: 0.15, w: 0.5, h: 0.55 },
              z: 3,
            },
          ],
          focusedAreaId: 'a1',
          fullscreenAreaId: null,
          paneSeq: 8,
        },
      };
    }

    it('gets the default backdrop rather than none', () => {
      // A v1 desktop had no backdrop at all, so there is nothing to preserve, and
      // `none` would hand every upgrading user a blank landing surface.
      expect(deserialize(v1Blob(), KNOWN)!.backdrop).toEqual({ id: DEFAULT_BACKDROP });
    });

    it('carries floating panes and the old grid on one unit basis', () => {
      const back = deserialize(v1Blob(), KNOWN)!;
      // Fractions throughout, against a 1×1 viewport, so the ordinary
      // rescale-on-measure path turns all of them into pixels together.
      expect(back.windowViewport).toEqual({ w: 1, h: 1 });
      const floated = back.windows.find((w) => w.area.tabs[0].instanceId === 'scratch.note#7')!;
      expect(floated.rect).toEqual({ x: 0.2, y: 0.15, w: 0.5, h: 0.55 });
      const grid = back.windows.find((w) => w.area.tabs[0].viewId === 'editor.buffer')!;
      expect(grid.rect).toEqual({ x: 0, y: 0, w: 1, h: 1 });
    });
  });
});

describe('deserialize pruning', () => {
  it('drops a window whose every tab names an unknown view', () => {
    const blob = JSON.parse(JSON.stringify(serialize(frame()))) as {
      frame: { windows: Array<{ area: { tabs: Array<{ viewId: string }> } }> };
    };
    blob.frame.windows[1].area.tabs[0].viewId = 'ghost.pane';
    blob.frame.windows[0].area.tabs[1].viewId = 'ghost.pane';
    const back = deserialize(blob as never, KNOWN)!;
    // The whole window goes, not just its tab — a titlebar with nothing under it
    // is not a state the running app can produce.
    expect(back.windows).toHaveLength(1);
    expect(back.windows[0].area.tabs.map((t) => t.viewId)).toEqual(['editor.buffer']);
  });

  it('prunes unknown region views and repairs activeView', () => {
    const f = frame();
    f.windows[0].area.tabs[0].regions = {
      right: { ...region, views: ['ghost.view', 'code.outline'], activeView: 'ghost.view' },
      bottom: { ...region, views: ['ghost.only'], activeView: 'ghost.only' },
    };
    const tab = deserialize(serialize(f), KNOWN)!.windows[0].area.tabs[0];
    expect(tab.regions?.right?.views).toEqual(['code.outline']);
    expect(tab.regions?.right?.activeView).toBe('code.outline');
    expect(tab.regions?.bottom).toBeUndefined();
  });

  it('recovers paneSeq past every surviving id', () => {
    const blob = JSON.parse(JSON.stringify(serialize(frame()))) as { frame: { paneSeq: number } };
    blob.frame.paneSeq = 0;
    const back = deserialize(blob as never, KNOWN)!;
    for (const w of back.windows) {
      expect(Number(w.id.slice(1))).toBeLessThan(back.paneSeq);
      expect(Number(w.area.id.slice(1))).toBeLessThan(back.paneSeq);
    }
  });
});

// A saved layout outlives the code that wrote it: a layout naming a view that has
// since been merged away must keep working, not open with holes in it.
describe('deserialize view renames', () => {
  const RENAMED = new Set(['games.lobby', 'games.log', 'scratch.note']);

  function blobWith(ids: string[]) {
    const f = windowsFrame(ids);
    return serialize(f);
  }

  it('renames a retired view to its replacement', () => {
    const back = deserialize(blobWith(['games.thoughts', 'scratch.note']), RENAMED)!;
    expect(viewIds(back)).toEqual(['games.log', 'scratch.note']);
  });

  it('collapses two retired views that merged into one pane', () => {
    // games.board and games.loadout both became sections of games.lobby.
    const back = deserialize(blobWith(['games.board', 'games.loadout']), RENAMED)!;
    expect(viewIds(back)).toEqual(['games.lobby']);
  });

  it('keeps the pane the layout already placed over a renamed duplicate', () => {
    const back = deserialize(blobWith(['games.lobby', 'games.board']), RENAMED)!;
    expect(viewIds(back)).toEqual(['games.lobby']);
  });

  it('renames region views and their activeView', () => {
    const f = windowsFrame(['scratch.note']);
    f.windows[0].area.tabs[0].regions = {
      right: { ...region, views: ['games.thoughts'], activeView: 'games.thoughts' },
    };
    const back = deserialize(serialize(f), RENAMED)!;
    const pane = back.windows[0].area.tabs[0];
    expect(pane.regions?.right?.views).toEqual(['games.log']);
    expect(pane.regions?.right?.activeView).toBe('games.log');
  });

  describe('into a section (the interpretability suite)', () => {
    const SUITE = new Set(['interpretability.suite', 'llamacpp.server']);
    const panes = (f: FrameState) => listPanes(f).map((p) => p.pane);

    it('lands a retired pane on the section that replaced it', () => {
      const back = deserialize(blobWith(['interpretability.architecture']), SUITE)!;
      const [pane] = panes(back);
      expect(pane.viewId).toBe('interpretability.suite');
      expect(pane.activeSection).toBe('model');
    });

    it('drops the old pane own section, which named a tab of the old view', () => {
      const f = windowsFrame(['agentpedia.hub']);
      f.windows[0].area.tabs[0].activeSection = 'forks';
      const [pane] = panes(deserialize(serialize(f), SUITE)!);
      expect(pane.activeSection).toBe('runs');
    });

    it('collapses four retired panes into one suite pane, with no holes', () => {
      const back = deserialize(
        blobWith([
          'interpretability.context',
          'interpretability.architecture',
          'agentpedia.hub',
          'webml.lens',
          'llamacpp.server',
        ]),
        SUITE,
      )!;
      expect(viewIds(back)).toEqual(['interpretability.suite', 'llamacpp.server']);
      // The first one the layout named wins, and keeps its own landing section.
      expect(panes(back)[0].activeSection).toBe('context');
    });
  });
});
