/**
 * Floating desktop → docked workbench. The one rule that may not break: every
 * pane that was open is still open afterwards.
 */
import { describe, expect, it } from 'vitest';

import { createEmptyWindowFrame as createEmptyFrame } from '../../legacy-frame';
import { createArea } from '../../model';
import type {
  PaneRole,
  SnapZone,
  WindowFrame as FrameState,
  WindowMode,
  WindowState,
} from '../../types';
import { findPane, listPanes } from '../frame';
import { dockFrameFromWindows } from '../migrate';
import { collectAreas } from '../tree';
import type { SplitNode } from '../types';

const ROLES: Record<string, PaneRole> = {
  doc: 'document',
  note: 'document',
  term: 'tool',
  files: 'tool',
  chat: 'tool',
  clock: 'widget',
};
const roleOf = (viewId: string) => ROLES[viewId];

interface Spec {
  tabs: string[];
  rect?: WindowState['rect'];
  mode?: WindowMode;
  snap?: SnapZone;
  active?: number;
}

function v3(specs: Spec[], extra: Partial<FrameState> = {}): FrameState {
  const windows = specs.map(
    (spec, i): WindowState => ({
      id: `w${i * 2 + 1}`,
      area: {
        ...createArea(`a${i * 2 + 2}`),
        tabs: spec.tabs.map((id) => ({ instanceId: id, viewId: id.split('#')[0] })),
        activeTab: spec.active ?? 0,
      },
      rect: spec.rect ?? { x: 100, y: 100, w: 700, h: 500 },
      mode: spec.mode ?? 'normal',
      ...(spec.snap ? { snap: spec.snap } : {}),
      z: i + 1,
    }),
  );
  return {
    ...createEmptyFrame(),
    windows,
    windowViewport: { w: 1600, h: 900 },
    paneSeq: 100,
    ...extra,
  };
}

const migrate = (frame: FrameState) => dockFrameFromWindows(frame, { roleOf });
const where = (frame: ReturnType<typeof migrate>, id: string) => findPane(frame, id)!.location;

describe('dockFrameFromWindows', () => {
  it('keeps every pane, exactly once', () => {
    const before = v3([
      { tabs: ['doc#1', 'note#2'] },
      { tabs: ['term#3'], mode: 'minimized' },
      { tabs: ['files#4'] },
      { tabs: ['clock#5'] },
      { tabs: ['gone#6'] },
      { tabs: ['doc#7'], snap: 'left' },
    ]);
    const after = migrate(before);
    const open = listPanes(after).map((p) => p.pane.instanceId);
    expect(open.sort()).toEqual([
      'clock#5',
      'doc#1',
      'doc#7',
      'files#4',
      'gone#6',
      'note#2',
      'term#3',
    ]);
  });

  it('stacks document windows as tabs with the topmost one in front', () => {
    const after = migrate(v3([{ tabs: ['doc#1'] }, { tabs: ['note#2'] }]));
    const [main] = collectAreas(after.center);
    expect(main.tabs.map((t) => t.instanceId)).toEqual(['doc#1', 'note#2']);
    expect(main.tabs[main.activeTab].instanceId).toBe('note#2');
  });

  it('turns a left/right snap pair into two columns', () => {
    const after = migrate(
      v3([
        { tabs: ['doc#1'], snap: 'left' },
        { tabs: ['note#2'], snap: 'right' },
      ]),
    );
    const root = after.center as SplitNode;
    expect(root.orientation).toBe('row');
    expect(root.sizes).toEqual([0.5, 0.5]);
    expect(collectAreas(root).map((a) => a.tabs[0].instanceId)).toEqual(['doc#1', 'note#2']);
  });

  it('puts an unsnapped document between snapped columns', () => {
    const after = migrate(
      v3([
        { tabs: ['doc#1'], snap: 'third-r' },
        { tabs: ['note#2'] },
        { tabs: ['doc#3'], snap: 'third-l' },
      ]),
    );
    expect(collectAreas(after.center).map((a) => a.tabs[0].instanceId)).toEqual([
      'doc#3',
      'note#2',
      'doc#1',
    ]);
  });

  it('a minimized snapped window is a background tab, not a column', () => {
    const after = migrate(
      v3([{ tabs: ['doc#1'] }, { tabs: ['note#2'], snap: 'left', mode: 'minimized' }]),
    );
    const areas = collectAreas(after.center);
    expect(areas).toHaveLength(1);
    expect(areas[0].tabs[areas[0].activeTab].instanceId).toBe('doc#1');
  });

  it('reads a wide, short tool window as a bottom panel', () => {
    const after = migrate(v3([{ tabs: ['term#1'], rect: { x: 200, y: 600, w: 1200, h: 280 } }]));
    expect(where(after, 'term#1')).toMatchObject({ kind: 'dock', side: 'bottom' });
    expect(after.docks.bottom.visible).toBe(true);
    expect(after.docks.bottom.size).toBe(280);
  });

  it('sends a tall tool window to the side of the screen it was on', () => {
    const after = migrate(
      v3([
        { tabs: ['files#1'], rect: { x: 0, y: 0, w: 300, h: 800 } },
        { tabs: ['chat#2'], rect: { x: 1200, y: 0, w: 380, h: 800 } },
      ]),
    );
    expect(where(after, 'files#1')).toMatchObject({ kind: 'dock', side: 'left' });
    expect(where(after, 'chat#2')).toMatchObject({ kind: 'dock', side: 'right' });
    expect(after.docks.left.size).toBe(300);
  });

  it('prefers the side a view declares over the window’s shape', () => {
    const after = dockFrameFromWindows(
      v3([{ tabs: ['term#1'], rect: { x: 0, y: 0, w: 300, h: 800 } }]),
      { roleOf, dockOf: (viewId) => (viewId === 'term' ? 'bottom' : undefined) },
    );
    expect(where(after, 'term#1')).toMatchObject({ kind: 'dock', side: 'bottom' });
  });

  it('a dock holding only minimized tools starts hidden, with the tool still in it', () => {
    const after = migrate(
      v3([{ tabs: ['term#1'], mode: 'minimized', rect: { x: 0, y: 600, w: 1200, h: 280 } }]),
    );
    expect(after.docks.bottom.visible).toBe(false);
    expect(after.docks.bottom.area.tabs).toHaveLength(1);
  });

  it('keeps a dock size sane whatever the window was', () => {
    const after = migrate(v3([{ tabs: ['files#1'], rect: { x: 0, y: 0, w: 90, h: 900 } }]));
    expect(after.docks.left.size).toBe(160);
    const huge = migrate(v3([{ tabs: ['files#1'], rect: { x: 0, y: 0, w: 780, h: 900 } }]));
    expect(huge.docks.left.size).toBe(520);
  });

  it('opens a view whose module is not loaded in the centre', () => {
    const after = migrate(v3([{ tabs: ['gone#1'] }]));
    expect(where(after, 'gone#1').kind).toBe('center');
  });

  it('carries focus across and names the group that holds it', () => {
    const after = migrate(
      v3([{ tabs: ['doc#1'] }, { tabs: ['term#2'], rect: { x: 0, y: 600, w: 1200, h: 280 } }], {
        focusedInstanceId: 'term#2',
      }),
    );
    expect(after.focusedInstanceId).toBe('term#2');
    expect(after.focusedAreaId).toBe(after.docks.bottom.area.id);
  });

  it('an empty desktop becomes an empty workbench with its backdrop', () => {
    const after = migrate(v3([], { backdrop: { id: 'board' } }));
    expect(listPanes(after)).toEqual([]);
    expect(after.backdrop).toEqual({ id: 'board' });
    expect(after.focusedAreaId).toBe(collectAreas(after.center)[0].id);
  });

  it('mints ids past the old counter so nothing collides', () => {
    const before = v3([
      { tabs: ['doc#1'], snap: 'left' },
      { tabs: ['note#2'], snap: 'right' },
    ]);
    const after = migrate(before);
    const minted = [
      ...collectAreas(after.center).map((a) => a.id),
      ...Object.values(after.docks).map((d) => d.area.id),
      (after.center as SplitNode).id,
    ].map((id) => Number(id.slice(1)));
    expect(Math.min(...minted)).toBeGreaterThanOrEqual(before.paneSeq);
    expect(after.paneSeq).toBeGreaterThan(Math.max(...minted));
  });
});
