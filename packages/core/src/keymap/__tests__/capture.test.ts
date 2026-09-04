import { beforeEach, describe, expect, it, vi } from 'vitest';

import { layoutStore } from '../../layout/store';
import { seedFromPreset, type FramePreset } from '../../layout/presets';
import { getCapture, releaseCapture, requestCapture } from '../capture';

const KNOWN = new Set(['editor.buffer', 'hassault.play', 'files.tree']);

const preset: FramePreset = {
  id: 'test',
  name: 'Test',
  frame: {
    center: {
      split: 'row',
      children: [{ pane: 'hassault.play' }, { pane: 'editor.buffer' }],
    },
    docks: { left: { tools: ['files.tree'] } },
  },
};

const frame = () => layoutStore.getSnapshot().frame;

function instanceOf(viewId: string): string {
  const walk = (node: ReturnType<typeof frame>['center']): string | null => {
    if (node.kind === 'area') return node.tabs.find((t) => t.viewId === viewId)?.instanceId ?? null;
    for (const child of node.children) {
      const hit = walk(child);
      if (hit) return hit;
    }
    return null;
  };
  return (
    walk(frame().center) ?? frame().docks.left.tools.find((t) => t.viewId === viewId)!.instanceId
  );
}

function focus(instanceId: string | null): void {
  layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId });
}

beforeEach(() => {
  releaseCapture();
  layoutStore.resetForTests();
  layoutStore.dispatch({
    type: 'LOAD_WORKSPACE',
    workspaceId: 'test',
    frame: seedFromPreset(preset, { knownViews: KNOWN }),
  });
});

describe('requestCapture', () => {
  it('grants capture to the focused pane', () => {
    const game = instanceOf('hassault.play');
    focus(game);
    expect(
      requestCapture({
        mode: 'full',
        escape: 'passthrough',
        instanceId: game,
        viewId: 'hassault.play',
      }),
    ).toBe(true);
    expect(getCapture()).toMatchObject({ mode: 'full', viewId: 'hassault.play' });
  });

  it('refuses a pane that is not focused', () => {
    // A background pane grabbing the keyboard is the exact bug this replaces:
    // the game used to keep window-level listeners alive whatever was focused.
    const game = instanceOf('hassault.play');
    focus(instanceOf('editor.buffer'));
    expect(
      requestCapture({
        mode: 'full',
        escape: 'release',
        instanceId: game,
        viewId: 'hassault.play',
      }),
    ).toBe(false);
    expect(getCapture()).toBeNull();
  });
});

describe('capture follows focus', () => {
  it('releases when focus moves to another pane', () => {
    const game = instanceOf('hassault.play');
    const onRelease = vi.fn();
    focus(game);
    requestCapture({
      mode: 'full',
      escape: 'release',
      instanceId: game,
      viewId: 'hassault.play',
      onRelease,
    });

    focus(instanceOf('editor.buffer'));
    expect(getCapture()).toBeNull();
    expect(onRelease).toHaveBeenCalledTimes(1);
  });

  it('releases when focus moves to a docked tool', () => {
    const game = instanceOf('hassault.play');
    focus(game);
    requestCapture({ mode: 'full', escape: 'release', instanceId: game, viewId: 'hassault.play' });
    focus(instanceOf('files.tree'));
    expect(getCapture()).toBeNull();
  });

  it('releases when focus leaves every pane', () => {
    const game = instanceOf('hassault.play');
    focus(game);
    requestCapture({ mode: 'full', escape: 'release', instanceId: game, viewId: 'hassault.play' });
    focus(null);
    expect(getCapture()).toBeNull();
  });

  it('releases when the capturing pane is closed', () => {
    const game = instanceOf('hassault.play');
    focus(game);
    requestCapture({ mode: 'full', escape: 'release', instanceId: game, viewId: 'hassault.play' });
    layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId: game });
    expect(getCapture()).toBeNull();
  });
});

describe('releaseCapture', () => {
  it('runs onRelease exactly once, however release is triggered', () => {
    const game = instanceOf('hassault.play');
    const onRelease = vi.fn();
    focus(game);
    requestCapture({
      mode: 'full',
      escape: 'release',
      instanceId: game,
      viewId: 'hassault.play',
      onRelease,
    });

    releaseCapture(game);
    releaseCapture(game);
    focus(null);
    expect(onRelease).toHaveBeenCalledTimes(1);
  });

  it('ignores a release from a pane that is not the holder', () => {
    const game = instanceOf('hassault.play');
    focus(game);
    requestCapture({ mode: 'full', escape: 'release', instanceId: game, viewId: 'hassault.play' });
    releaseCapture('someone.else#1');
    expect(getCapture()).not.toBeNull();
  });

  it('a second pane taking capture releases the first', () => {
    const game = instanceOf('hassault.play');
    const onRelease = vi.fn();
    focus(game);
    requestCapture({
      mode: 'full',
      escape: 'release',
      instanceId: game,
      viewId: 'hassault.play',
      onRelease,
    });

    const editor = instanceOf('editor.buffer');
    focus(editor);
    requestCapture({
      mode: 'keyboard',
      escape: 'passthrough',
      instanceId: editor,
      viewId: 'editor.buffer',
    });

    expect(onRelease).toHaveBeenCalledTimes(1);
    expect(getCapture()).toMatchObject({ viewId: 'editor.buffer', mode: 'keyboard' });
  });
});

describe('systemKeys', () => {
  it('carries the request through to the published state', () => {
    // `canHoldSystemKeys` reads this off the live capture, so a flag lost between
    // the request and the snapshot would silently disable the whole feature —
    // and it would fail *open*, looking exactly like an unsupported browser.
    const game = instanceOf('hassault.play');
    focus(game);
    requestCapture({
      mode: 'full',
      escape: 'passthrough',
      systemKeys: true,
      instanceId: game,
      viewId: 'hassault.play',
    });
    expect(getCapture()).toMatchObject({ systemKeys: true });
  });

  it('defaults to false when a pane does not ask', () => {
    // Absent must never read as "yes": every pane that predates the flag, and
    // every plugin, has to keep the OS's chords working.
    const editor = instanceOf('editor.buffer');
    focus(editor);
    requestCapture({
      mode: 'keyboard',
      escape: 'release',
      instanceId: editor,
      viewId: 'editor.buffer',
    });
    expect(getCapture()).toMatchObject({ systemKeys: false });
  });

  it('does not survive a release', () => {
    const game = instanceOf('hassault.play');
    focus(game);
    requestCapture({
      mode: 'full',
      escape: 'passthrough',
      systemKeys: true,
      instanceId: game,
      viewId: 'hassault.play',
    });
    releaseCapture(game);
    expect(getCapture()).toBeNull();
  });
});
