/**
 * Frame-level verbs: a pane or group is addressed the same way wherever it
 * lives, and no edit leaves focus, a maximize or a presentation pointing at
 * something that is gone.
 */
import { describe, expect, it } from 'vitest';

import type { PaneState } from '../../types';
import {
  createEmptyDockFrame,
  dockPopout,
  findPane,
  focusPane,
  insertPane,
  listPanes,
  movePane,
  removePane,
  setDockSize,
  setDockVisible,
  toggleMaximized,
  visiblePanes,
} from '../frame';
import { collectAreas } from '../tree';
import type { DockFrame, PlaceTarget, SplitNode } from '../types';

const pane = (id: string): PaneState => ({ instanceId: id, viewId: id.split('#')[0] });

function build(...steps: [string, PlaceTarget][]): DockFrame {
  return steps.reduce(
    (frame, [id, target]) => insertPane(frame, pane(id), target)!,
    createEmptyDockFrame(),
  );
}

const ids = (frame: DockFrame) => listPanes(frame).map((p) => p.pane.instanceId);

describe('insertPane', () => {
  it('opens documents as tabs of the focused centre group', () => {
    const frame = build(['a#1', { kind: 'center' }], ['b#2', { kind: 'center' }]);
    expect(collectAreas(frame.center)).toHaveLength(1);
    expect(ids(frame)).toEqual(['a#1', 'b#2']);
    expect(frame.focusedInstanceId).toBe('b#2');
  });

  it('shows a hidden dock when a pane opens into it', () => {
    const frame = build(['term#1', { kind: 'dock', side: 'bottom' }]);
    expect(frame.docks.bottom.visible).toBe(true);
    expect(findPane(frame, 'term#1')!.location).toMatchObject({ kind: 'dock', side: 'bottom' });
  });

  it('leaves a hidden dock hidden for a background insert', () => {
    const frame = insertPane(
      createEmptyDockFrame(),
      pane('term#1'),
      { kind: 'dock', side: 'bottom' },
      { activate: false },
    )!;
    expect(frame.docks.bottom.visible).toBe(false);
    expect(frame.focusedInstanceId).toBeNull();
  });

  it('splits the centre and focuses the new group', () => {
    const one = build(['a#1', { kind: 'center' }]);
    const home = collectAreas(one.center)[0].id;
    const two = insertPane(one, pane('b#2'), { kind: 'split', areaId: home, side: 'right' })!;
    const root = two.center as SplitNode;
    expect(root.orientation).toBe('row');
    expect(collectAreas(two.center).map((a) => a.tabs[0].instanceId)).toEqual(['a#1', 'b#2']);
    expect(two.focusedAreaId).toBe(root.children[1].id);
  });

  it('falls back to the centre when asked to split a dock', () => {
    const frame = build(['term#1', { kind: 'dock', side: 'bottom' }]);
    const next = insertPane(frame, pane('a#2'), {
      kind: 'split',
      areaId: frame.docks.bottom.area.id,
      side: 'right',
    })!;
    expect(findPane(next, 'a#2')!.location.kind).toBe('center');
  });

  it('returns null for an unknown group', () => {
    expect(
      insertPane(createEmptyDockFrame(), pane('a#1'), { kind: 'area', areaId: 'zz' }),
    ).toBeNull();
  });

  it('never reuses an id', () => {
    let frame = build(['a#1', { kind: 'center' }]);
    const seen = new Set<string>(collectAreas(frame.center).map((a) => a.id));
    for (let i = 0; i < 6; i++) {
      const target = collectAreas(frame.center)[0].id;
      frame = insertPane(frame, pane(`n#${i + 10}`), {
        kind: 'split',
        areaId: target,
        side: i % 2 ? 'below' : 'right',
      })!;
    }
    frame = insertPane(frame, pane('p#99'), { kind: 'popout' })!;
    const all = [
      ...collectAreas(frame.center).map((a) => a.id),
      ...Object.values(frame.docks).map((d) => d.area.id),
      ...frame.popouts.flatMap((p) => [p.id, p.area.id]),
    ];
    expect(new Set(all).size).toBe(all.length);
    expect(all.length).toBeGreaterThan(seen.size);
  });
});

describe('removePane', () => {
  it('hides a dock when its last pane closes', () => {
    const frame = build(['term#1', { kind: 'dock', side: 'bottom' }]);
    const next = removePane(frame, 'term#1')!.frame;
    expect(next.docks.bottom.visible).toBe(false);
    expect(next.docks.bottom.area.tabs).toEqual([]);
  });

  it('closes a popout with its last pane', () => {
    const frame = build(['a#1', { kind: 'popout' }]);
    expect(removePane(frame, 'a#1')!.frame.popouts).toEqual([]);
  });

  it('moves focus to the tab that took the closed one’s place', () => {
    const frame = build(['a#1', { kind: 'center' }], ['b#2', { kind: 'center' }]);
    const next = removePane(frame, 'b#2')!.frame;
    expect(next.focusedInstanceId).toBe('a#1');
  });

  it('falls back to a centre tab when the focused dock pane closes', () => {
    const frame = build(['a#1', { kind: 'center' }], ['term#2', { kind: 'dock', side: 'bottom' }]);
    expect(frame.focusedInstanceId).toBe('term#2');
    const next = removePane(frame, 'term#2')!.frame;
    expect(next.focusedInstanceId).toBe('a#1');
    expect(findPane(next, next.focusedInstanceId!)!.location.areaId).toBe(next.focusedAreaId);
  });

  it('leaves focus on an empty centre when the last pane closes', () => {
    const frame = build(['a#1', { kind: 'center' }]);
    const next = removePane(frame, 'a#1')!.frame;
    expect(next.focusedInstanceId).toBeNull();
    expect(next.focusedAreaId).toBe(collectAreas(next.center)[0].id);
  });

  it('clears a maximize whose group went away', () => {
    const one = build(['a#1', { kind: 'center' }]);
    const home = collectAreas(one.center)[0].id;
    const two = insertPane(one, pane('b#2'), { kind: 'split', areaId: home, side: 'right' })!;
    const maxed = toggleMaximized(two, two.focusedAreaId!);
    expect(maxed.maximizedAreaId).toBe(two.focusedAreaId);
    expect(removePane(maxed, 'b#2')!.frame.maximizedAreaId).toBeNull();
  });
});

describe('movePane', () => {
  it('moves a pane between hosts without changing the pane', () => {
    const frame = build(['a#1', { kind: 'center' }], ['b#2', { kind: 'center' }]);
    const next = movePane(frame, 'b#2', { kind: 'dock', side: 'right' })!;
    expect(findPane(next, 'b#2')!.location).toMatchObject({ kind: 'dock', side: 'right' });
    expect(findPane(next, 'b#2')!.pane).toEqual(pane('b#2'));
    expect(ids(next).sort()).toEqual(['a#1', 'b#2']);
  });

  it('is a no-op when a pane is dropped on its own group', () => {
    const frame = build(['a#1', { kind: 'center' }]);
    expect(movePane(frame, 'a#1', { kind: 'center' })).toBe(frame);
  });

  it('reorders within a group when given an index', () => {
    const frame = build(['a#1', { kind: 'center' }], ['b#2', { kind: 'center' }]);
    const home = collectAreas(frame.center)[0].id;
    const next = movePane(frame, 'b#2', { kind: 'area', areaId: home, index: 0 })!;
    expect(ids(next)).toEqual(['b#2', 'a#1']);
  });

  it('refuses to split a group around its only tab', () => {
    const frame = build(['a#1', { kind: 'center' }]);
    const home = collectAreas(frame.center)[0].id;
    expect(movePane(frame, 'a#1', { kind: 'split', areaId: home, side: 'right' })).toBe(frame);
  });

  it('splits a group in two when one of several tabs is dragged to its edge', () => {
    const frame = build(['a#1', { kind: 'center' }], ['b#2', { kind: 'center' }]);
    const home = collectAreas(frame.center)[0].id;
    const next = movePane(frame, 'b#2', { kind: 'split', areaId: home, side: 'below' })!;
    expect((next.center as SplitNode).orientation).toBe('column');
    expect(collectAreas(next.center).map((a) => a.tabs.map((t) => t.instanceId))).toEqual([
      ['a#1'],
      ['b#2'],
    ]);
  });

  it('pops a pane out into its own window and back', () => {
    const frame = build(['a#1', { kind: 'center' }], ['b#2', { kind: 'center' }]);
    const out = movePane(frame, 'b#2', {
      kind: 'popout',
      bounds: { x: 10, y: 20, w: 800, h: 600 },
    })!;
    expect(out.popouts).toHaveLength(1);
    expect(out.popouts[0].bounds).toEqual({ x: 10, y: 20, w: 800, h: 600 });
    expect(findPane(out, 'b#2')!.location.kind).toBe('popout');

    const back = dockPopout(out, out.popouts[0].id, () => ({ kind: 'center' }));
    expect(back.popouts).toEqual([]);
    expect(findPane(back, 'b#2')!.location.kind).toBe('center');
    expect(back.focusedInstanceId).toBe('b#2');
  });

  it('docking a popout back never drops a pane, even if routing says popout', () => {
    const frame = build(['a#1', { kind: 'popout' }]);
    const back = dockPopout(frame, frame.popouts[0].id, () => ({ kind: 'popout' }));
    expect(ids(back)).toEqual(['a#1']);
    expect(back.popouts).toEqual([]);
  });
});

describe('docks', () => {
  it('cannot show an empty dock', () => {
    const frame = createEmptyDockFrame();
    expect(setDockVisible(frame, 'left', true)).toBe(frame);
  });

  it('hands focus back to the centre when the focused dock is hidden', () => {
    const frame = build(['a#1', { kind: 'center' }], ['term#2', { kind: 'dock', side: 'bottom' }]);
    const next = setDockVisible(frame, 'bottom', false);
    expect(next.docks.bottom.visible).toBe(false);
    expect(next.focusedInstanceId).toBe('a#1');
    // The pane is put away, not closed.
    expect(findPane(next, 'term#2')).not.toBeNull();
  });

  it('focusing a pane in a hidden dock shows the dock', () => {
    const frame = setDockVisible(
      build(['a#1', { kind: 'center' }], ['term#2', { kind: 'dock', side: 'bottom' }]),
      'bottom',
      false,
    );
    expect(focusPane(frame, 'term#2')!.docks.bottom.visible).toBe(true);
  });

  it('clamps a dock to a usable minimum', () => {
    expect(setDockSize(createEmptyDockFrame(), 'left', 10).docks.left.size).toBe(160);
  });
});

describe('visiblePanes', () => {
  it('reports each showing group’s active tab and skips hidden docks', () => {
    const frame = setDockVisible(
      build(
        ['a#1', { kind: 'center' }],
        ['b#2', { kind: 'center' }],
        ['term#3', { kind: 'dock', side: 'bottom' }],
        ['pop#4', { kind: 'popout' }],
      ),
      'bottom',
      false,
    );
    expect(visiblePanes(frame).map((p) => p.pane.instanceId)).toEqual(['b#2', 'pop#4']);
  });

  it('a maximized group hides the rest of the main window, not the popouts', () => {
    const frame = build(
      ['a#1', { kind: 'center' }],
      ['term#2', { kind: 'dock', side: 'bottom' }],
      ['pop#3', { kind: 'popout' }],
    );
    const maxed = toggleMaximized(frame, frame.docks.bottom.area.id);
    expect(visiblePanes(maxed).map((p) => p.pane.instanceId)).toEqual(['term#2', 'pop#3']);
  });
});

describe('focusPane', () => {
  it('clears the attention flag: looking at a pane is the acknowledgement', () => {
    const frame = build(['a#1', { kind: 'center' }], ['b#2', { kind: 'center' }]);
    const home = collectAreas(frame.center)[0];
    const flagged: DockFrame = {
      ...frame,
      center: { ...home, tabs: home.tabs.map((t) => ({ ...t, attention: true })) },
    };
    const next = focusPane(flagged, 'a#1')!;
    expect(findPane(next, 'a#1')!.pane.attention).toBeUndefined();
    expect(findPane(next, 'b#2')!.pane.attention).toBe(true);
  });
});
