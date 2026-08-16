/**
 * Presenting a pane: the floating-desktop counterpart of area fullscreen.
 *
 * The rules worth pinning are the ones that fail *silently* — a flag set for a
 * pane no renderer can promote, or a flag left pointing at a pane that is gone,
 * which paints a screen with no chrome and no way out.
 */
import { describe, expect, it } from 'vitest';

import { listPanes } from '../model';
import { seedFromPreset, type FramePreset } from '../presets';
import { serialize, deserialize } from '../serialize';
import { layoutStore } from '../store';
import type { FrameState } from '../types';
import { explodeToWindows } from '../windows';

const KNOWN = new Set(['scratch.note', 'editor.buffer']);
const VIEW = { w: 1600, h: 900 };

const preset: FramePreset = {
  id: 'test',
  name: 'Test',
  frame: {
    center: {
      split: 'row',
      sizes: [0.5, 0.5],
      children: [{ pane: 'editor.buffer' }, { pane: 'scratch.note' }],
    },
  },
};

const tiled = (): FrameState => seedFromPreset(preset, { knownViews: KNOWN });

/** The same two panes, exploded into two windows. */
function windowed(): { frame: FrameState; first: string } {
  const frame = explodeToWindows(tiled(), VIEW);
  return { frame, first: frame.windows[0].area.tabs[0].instanceId };
}

function load(frame: FrameState): void {
  layoutStore.dispatch({ type: 'LOAD_WORKSPACE', workspaceId: 'test', frame });
}

describe('SET_PRESENTED', () => {
  it('presents a pane that lives in a window', () => {
    const { frame, first } = windowed();
    load(frame);
    expect(
      layoutStore.dispatch({ type: 'SET_PRESENTED', instanceId: first }).frame.presentedInstanceId,
    ).toBe(first);
  });

  it('refuses a pane that is not in a window', () => {
    // A centre or docked pane has its own mechanism (`fullscreenAreaId`).
    // Accepting one here would set a flag nothing renders: the pane would look
    // untouched while Escape reported that it had unwound something.
    const frame = tiled();
    load(frame);
    const centre = listPanes(frame)[0].pane.instanceId;
    expect(
      layoutStore.dispatch({ type: 'SET_PRESENTED', instanceId: centre }).frame.presentedInstanceId,
    ).toBeNull();
  });

  it('refuses an instance id that names nothing', () => {
    const { frame } = windowed();
    load(frame);
    expect(
      layoutStore.dispatch({ type: 'SET_PRESENTED', instanceId: 'nope' }).frame.presentedInstanceId,
    ).toBeNull();
  });

  it('clears when the presented pane closes', () => {
    const { frame, first } = windowed();
    load(frame);
    layoutStore.dispatch({ type: 'SET_PRESENTED', instanceId: first });
    // Stale here is not a mis-aimed keybinding: presentation hides the taskbar
    // and the workspace strip, so a dangling id is a blank screen with no chrome.
    expect(
      layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId: first }).frame.presentedInstanceId,
    ).toBeNull();
  });

  it('clears when the desktop flips back to tiling', () => {
    const { frame, first } = windowed();
    load({ ...frame, mode: 'floating' });
    layoutStore.dispatch({ type: 'SET_PRESENTED', instanceId: first });
    expect(
      layoutStore.dispatch({
        type: 'SET_DESKTOP_MODE',
        mode: 'tiling',
        viewport: VIEW,
        dockFor: {},
      }).frame.presentedInstanceId,
    ).toBeNull();
  });
});

describe('persistence', () => {
  it('is never written and never restored', () => {
    // A momentary way of looking at a pane, not a property of the workspace.
    // Restoring one hands the user a full-screen pane with the chrome hidden and
    // no memory of having asked for it.
    const { frame, first } = windowed();
    const presented: FrameState = { ...frame, presentedInstanceId: first };
    const blob = serialize(presented);
    expect((blob.frame as Record<string, unknown>).presentedInstanceId).toBeUndefined();
    const back = deserialize(blob as unknown as Parameters<typeof deserialize>[0], KNOWN);
    expect(back?.presentedInstanceId).toBeNull();
  });
});
