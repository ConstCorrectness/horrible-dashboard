/**
 * A floating desktop's taskbar: minimize semantics for panes with no surface, and
 * the attention flash that makes minimizing something that keeps working useful.
 *
 * The bug this guards: on a floating desktop the centre tree is retained but never
 * rendered, so "Dock back into the frame" put a pane somewhere it could not be
 * drawn. The window disappeared, the taskbar button stayed, and clicking it did
 * nothing — the pane was running, invisible, and unreachable without flipping the
 * whole desktop to tiling.
 */
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../../registry';
import {
  activateTaskbarEntry,
  requestPaneAttention,
  setDesktopMode,
  setPaneWindowed,
} from '../controller';
import { seedFromPreset, type FramePreset } from '../presets';
import { layoutStore } from '../store';
import { taskbarEntries } from '../taskbar';

const Stub = () => null;
const KNOWN = new Set(['ft.a', 'ft.b']);

const preset: FramePreset = {
  id: 'ft',
  name: 'Floating taskbar',
  frame: { center: { split: 'row', children: [{ pane: 'ft.a' }, { pane: 'ft.b' }] } },
};

beforeAll(() => {
  registry.register({
    id: 'floating-taskbar-test',
    title: 'Floating taskbar test',
    panels: [
      { id: 'ft.a', title: 'Alpha', component: Stub, role: 'document', icon: 'A' },
      { id: 'ft.b', title: 'Beta', component: Stub, role: 'document', icon: 'B' },
    ],
  });
});

beforeEach(() => {
  layoutStore.resetForTests();
  layoutStore.dispatch({
    type: 'LOAD_WORKSPACE',
    workspaceId: 'ft',
    frame: seedFromPreset(preset, { knownViews: KNOWN }),
  });
});

const frame = () => layoutStore.getSnapshot().frame;
const entryFor = (id: string) => taskbarEntries(frame()).find((e) => e.instanceId === id)!;
const firstId = () => taskbarEntries(frame())[0].instanceId;

describe('a pane with nowhere to be drawn', () => {
  it('reads as minimized, not hidden, on a floating desktop', () => {
    const id = firstId();
    setDesktopMode('floating');
    // Put it back in the (unrendered) centre tree — what "Dock back into the
    // frame" does.
    setPaneWindowed(id, false);

    expect(entryFor(id).location.kind).toBe('area');
    expect(entryFor(id).state).toBe('minimized');
  });

  it('comes back as a window when its taskbar button is clicked', () => {
    const id = firstId();
    setDesktopMode('floating');
    setPaneWindowed(id, false);
    expect(entryFor(id).location.kind).toBe('area');

    activateTaskbarEntry(id);

    // The pane was never closed — it just had no surface. This is the only way
    // back, and before it existed the button was a dead end.
    expect(entryFor(id).location.kind).toBe('window');
    expect(entryFor(id).state).not.toBe('minimized');
  });

  it('is never lost: the pane stays in the taskbar throughout', () => {
    const id = firstId();
    const before = taskbarEntries(frame()).length;
    setDesktopMode('floating');
    setPaneWindowed(id, false);
    expect(taskbarEntries(frame())).toHaveLength(before);
    activateTaskbarEntry(id);
    expect(taskbarEntries(frame())).toHaveLength(before);
  });

  it('still minimizes normally on a tiling desktop', () => {
    /** The floating branch must not change tiling behaviour, where a centre pane
     *  genuinely can be on screen. */
    const id = firstId();
    expect(entryFor(id).state).toBe('focused');
    activateTaskbarEntry(id); // showing + focused → minimize
    expect(entryFor(id).state).toBe('minimized');
    activateTaskbarEntry(id); // and back
    expect(entryFor(id).state).not.toBe('minimized');
  });
});

describe('attention', () => {
  it('flags a pane that finished while out of sight', () => {
    const id = firstId();
    setDesktopMode('floating');
    setPaneWindowed(id, false); // out of sight, still running

    expect(requestPaneAttention(id)).toBe(true);
    expect(entryFor(id).attention).toBe(true);
  });

  it('is cleared by looking at the pane', () => {
    const id = firstId();
    setDesktopMode('floating');
    setPaneWindowed(id, false);
    requestPaneAttention(id);

    activateTaskbarEntry(id);

    expect(entryFor(id).attention).toBe(false);
  });

  it('does not flash a pane the user is already looking at', () => {
    /** A button pulsing for the thing on screen is noise, not a notification. */
    const id = firstId();
    expect(entryFor(id).state).toBe('focused');
    expect(requestPaneAttention(id)).toBe(false);
    expect(entryFor(id).attention).toBe(false);
  });

  it('can be raised for a background tab, not just a floating pane', () => {
    const [a, b] = taskbarEntries(frame()).map((e) => e.instanceId);
    activateTaskbarEntry(b); // focus the other one
    expect(requestPaneAttention(a)).toBe(true);
    expect(entryFor(a).attention).toBe(true);
    // …and the focused one is still refused.
    expect(requestPaneAttention(b)).toBe(false);
  });

  it('can be cleared explicitly by the pane that raised it', () => {
    const [a, b] = taskbarEntries(frame()).map((e) => e.instanceId);
    activateTaskbarEntry(b);
    requestPaneAttention(a);
    expect(requestPaneAttention(a, false)).toBe(true);
    expect(entryFor(a).attention).toBe(false);
  });

  it('is absent rather than false on the pane, so layouts do not carry stale flags', () => {
    const [a, b] = taskbarEntries(frame()).map((e) => e.instanceId);
    activateTaskbarEntry(b);
    requestPaneAttention(a);
    requestPaneAttention(a, false);
    const json = JSON.stringify(frame());
    expect(json).not.toContain('"attention"');
  });
});
