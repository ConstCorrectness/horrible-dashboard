/**
 * Joining two areas without adopting the neighbour's tabs is a close in all but
 * name — and it used to skip everything a close does. No close guard ran, so
 * unsaved work vanished without a prompt; no session teardown ran, so whatever the
 * dropped pane held (a PTY, a call's audio) kept running with no pane left to stop it.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { setPaneDirty } from '../close-guards';
import { joinAreaDirection } from '../controller';
import { findPaneAnywhere, listPanes } from '../model';
import {
  hasPaneSession,
  paneSession,
  paneSessionKey,
  resetPaneSessionsForTests,
} from '../pane-lifetime';
import { seedFromPreset, type FramePreset } from '../presets';
import { layoutStore } from '../store';

const KNOWN = new Set(['editor.buffer', 'dashboard.welcome']);

const preset: FramePreset = {
  id: 'test',
  name: 'Test',
  frame: {
    center: {
      split: 'row',
      children: [{ tabs: ['editor.buffer'] }, { pane: 'dashboard.welcome' }],
    },
  },
};

function load(): void {
  layoutStore.dispatch({
    type: 'LOAD_WORKSPACE',
    workspaceId: 'ws',
    frame: seedFromPreset(preset, { knownViews: KNOWN }),
  });
}

/** The right-hand area joins leftward, swallowing the left area. */
function areas() {
  const panes = listPanes(layoutStore.getSnapshot().frame);
  const left = panes.find((p) => p.pane.viewId === 'editor.buffer')!;
  const right = panes.find((p) => p.pane.viewId === 'dashboard.welcome')!;
  const areaOf = (p: typeof left) => (p.location.kind === 'area' ? p.location.areaId : '');
  return { left: left.pane.instanceId, right: right.pane.instanceId, rightArea: areaOf(right) };
}

beforeEach(() => {
  layoutStore.resetForTests();
  resetPaneSessionsForTests();
  load();
});

describe('a join that drops panes closes them', () => {
  it('disposes what the dropped pane held', () => {
    // A widget area swallowing a document area does not adopt its tabs.
    const { left, rightArea } = areas();
    const dispose = vi.fn();
    paneSession(paneSessionKey('ws', left), () => ({}), dispose);

    expect(joinAreaDirection(rightArea, 'left')).toBe(true);
    expect(findPaneAnywhere(layoutStore.getSnapshot().frame, left)).toBeNull();
    expect(dispose).toHaveBeenCalledTimes(1);
    expect(hasPaneSession(paneSessionKey('ws', left))).toBe(false);
  });

  it('refuses rather than silently discarding unsaved work', () => {
    const { left, rightArea } = areas();
    setPaneDirty(left, true);
    try {
      const before = layoutStore.getSnapshot();
      expect(joinAreaDirection(rightArea, 'left')).toBe(false);
      expect(layoutStore.getSnapshot()).toBe(before);
      expect(findPaneAnywhere(layoutStore.getSnapshot().frame, left)).not.toBeNull();
    } finally {
      setPaneDirty(left, false);
    }
  });
});
