import { describe, expect, it } from 'vitest';

import { createEmptyFrame } from '../../../layout/model';
import type { AreaNode, FrameState, PaneState } from '../../../layout/types';
import type { ViewLookup } from '../mirror';
import { isClear, isPixelSafe, preflight } from '../preflight';

/**
 * The pre-flight rule.
 *
 * Unlike the redactor, this is not an enforcement boundary — a capture sends
 * light and cannot withhold anything. What is under test is that the *warning* is
 * both complete (nothing undeclared goes unlisted) and quiet (nothing already
 * shared gets listed), because a warning with false positives is one people learn
 * to click through.
 */

function pane(viewId: string, n = 1): PaneState {
  return { instanceId: `${viewId}#${n}`, viewId };
}

function area(id: string, tabs: PaneState[], activeTab = 0): AreaNode {
  return { kind: 'area', id, tabs, activeTab };
}

function frameWith(center: FrameState['center']): FrameState {
  return { ...createEmptyFrame(), center };
}

const lookup: ViewLookup = (viewId) => {
  if (viewId === 'shared.note') return { title: 'Scratch', share: { mode: 'collab' } };
  if (viewId === 'shared.term') return { title: 'Terminal', share: { mode: 'mirror' } };
  if (viewId === 'shared.game') return { title: 'Game', share: { mode: 'pixels' } };
  if (viewId === 'secret.settings') return { title: 'Settings' };
  return undefined;
};

describe('isPixelSafe', () => {
  it('accepts every declared mode, not only `pixels`', () => {
    // `pixels` marks a pane that can *only* be shared this way — it is not a
    // stricter permission. Reading it as one would flag every shared pane in the
    // workspace and train the host to click through the warning.
    expect(isPixelSafe({ mode: 'pixels' })).toBe(true);
    expect(isPixelSafe({ mode: 'mirror' })).toBe(true);
    expect(isPixelSafe({ mode: 'collab' })).toBe(true);
  });

  it('rejects an absent declaration', () => {
    expect(isPixelSafe(undefined)).toBe(false);
  });
});

describe('preflight', () => {
  it('is clear when every visible pane declared itself', () => {
    const result = preflight(frameWith(area('a1', [pane('shared.note')])), lookup);
    expect(isClear(result)).toBe(true);
    expect(result.checked).toBe(1);
  });

  it('names an undeclared pane', () => {
    const result = preflight(frameWith(area('a1', [pane('secret.settings')])), lookup);
    expect(isClear(result)).toBe(false);
    expect(result.undeclared.map((p) => p.title)).toEqual(['Settings']);
  });

  it('names a pane whose view is not registered at all', () => {
    // A capture would show it regardless of whether we can identify it, so an
    // unknown view has to be listed rather than skipped.
    const result = preflight(frameWith(area('a1', [pane('ghost.gone')])), lookup);
    expect(result.undeclared).toHaveLength(1);
    expect(result.undeclared[0].viewId).toBe('ghost.gone');
  });

  it('ignores a background tab, which is not on screen', () => {
    // Listing it would be a false positive: an inactive tab is not in the light.
    const result = preflight(
      frameWith(area('a1', [pane('shared.note'), pane('secret.settings')], 0)),
      lookup,
    );
    expect(isClear(result)).toBe(true);
  });

  it('catches an undeclared pane that IS the active tab', () => {
    const result = preflight(
      frameWith(area('a1', [pane('shared.note'), pane('secret.settings')], 1)),
      lookup,
    );
    expect(result.undeclared.map((p) => p.title)).toEqual(['Settings']);
  });

  it('checks visible docks', () => {
    const frame = createEmptyFrame();
    frame.docks.right.visible = true;
    frame.docks.right.tools = [pane('secret.settings')];
    frame.docks.right.activeTool = 'secret.settings#1';
    expect(isClear(preflight(frame, lookup))).toBe(false);
  });

  it('ignores a hidden dock', () => {
    const frame = createEmptyFrame();
    frame.docks.right.visible = false;
    frame.docks.right.tools = [pane('secret.settings')];
    frame.docks.right.activeTool = 'secret.settings#1';
    expect(isClear(preflight(frame, lookup))).toBe(true);
  });

  it('checks a window that is on screen', () => {
    const frame = createEmptyFrame();
    frame.windows = [
      {
        id: 'w1',
        area: area('wa1', [pane('secret.settings')]),
        rect: { x: 0, y: 0, w: 10, h: 10 },
        mode: 'normal',
        z: 1,
      },
    ];
    expect(isClear(preflight(frame, lookup))).toBe(false);
  });

  it('ignores a minimized window', () => {
    // The pane stays mounted, but nobody can see it — including the capture.
    const frame = createEmptyFrame();
    frame.windows = [
      {
        id: 'w1',
        area: area('wa1', [pane('secret.settings')]),
        rect: { x: 0, y: 0, w: 10, h: 10 },
        mode: 'minimized',
        z: 1,
      },
    ];
    expect(isClear(preflight(frame, lookup))).toBe(true);
  });

  it('lists several undeclared panes rather than stopping at the first', () => {
    const frame = frameWith({
      kind: 'split',
      id: 's1',
      orientation: 'row',
      children: [area('a1', [pane('secret.settings')]), area('a2', [pane('ghost.gone')])],
      sizes: [0.5, 0.5],
    });
    expect(preflight(frame, lookup).undeclared).toHaveLength(2);
  });
});
