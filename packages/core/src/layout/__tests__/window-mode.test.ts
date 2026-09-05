/**
 * Window mode transitions: what a window comes back to, and snap assist.
 *
 * The bug these guard: `WindowMode` is a single enum, so minimizing a maximized
 * window overwrote the only record that it *was* maximized. Restoring then took the
 * free-floating path, which consumes `restoreRect` — and a maximized notebook came
 * back at whatever small size it had before it was maximized. The rect was never
 * lost; the mode was, and losing the mode is what discarded the rect.
 */
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../../registry';
import { setPaneWindowed } from '../controller';
import { seedFromPreset, type FramePreset } from '../presets';
import { rectForZone } from '../snap';
import { layoutStore } from '../store';
import type { FrameState, LayoutNode, WindowState } from '../types';

const Stub = () => null;
const KNOWN = new Set(['wm.a', 'wm.b']);
const VIEW = { w: 1600, h: 900 };

const preset: FramePreset = {
  id: 'wm',
  name: 'Window mode',
  frame: { center: { split: 'row', children: [{ pane: 'wm.a' }, { pane: 'wm.b' }] } },
};

beforeAll(() => {
  registry.register({
    id: 'window-mode-test',
    title: 'Window mode test',
    panels: [
      { id: 'wm.a', title: 'Alpha', component: Stub, role: 'document', icon: 'A' },
      { id: 'wm.b', title: 'Beta', component: Stub, role: 'document', icon: 'B' },
    ],
  });
});

beforeEach(() => {
  layoutStore.resetForTests();
  layoutStore.dispatch({
    type: 'LOAD_WORKSPACE',
    workspaceId: 'wm',
    frame: seedFromPreset(preset, { knownViews: KNOWN }),
  });
  layoutStore.dispatch({ type: 'SET_WINDOW_VIEWPORT', viewport: VIEW });
});

const frame = (): FrameState => layoutStore.getSnapshot().frame;
const byId = (id: string): WindowState => frame().windows.find((w) => w.id === id)!;

/**
 * Float the first remaining centre pane and return its window id. Always the
 * first: floating one removes it from the centre tree, so a fixed index would
 * name a different pane on the second call — and on the third, nothing at all.
 */
function float(): string {
  const walk = (node: LayoutNode): string[] =>
    node.kind === 'area' ? node.tabs.map((t) => t.instanceId) : node.children.flatMap(walk);
  const instanceId = walk(frame().center)[0];
  expect(instanceId).toBeDefined();
  setPaneWindowed(instanceId, true);
  return frame().windows[frame().windows.length - 1].id;
}

const mode = (id: string, m: 'normal' | 'minimized' | 'maximized', extra: object = {}) =>
  layoutStore.dispatch({
    type: 'SET_WINDOW_MODE',
    windowId: id,
    mode: m,
    viewport: VIEW,
    ...extra,
  });

describe('minimize and restore', () => {
  it('brings a maximized window back maximized and full-size', () => {
    const id = float();
    mode(id, 'maximized');
    const maxed = { ...byId(id).rect };
    mode(id, 'minimized');
    expect(byId(id).mode).toBe('minimized');
    mode(id, 'normal');
    expect(byId(id).mode).toBe('maximized');
    expect(byId(id).rect).toEqual(maxed);
    expect(byId(id).rect).toEqual(rectForZone('max', VIEW));
  });

  it('brings a snapped window back snapped to the same zone', () => {
    const id = float();
    mode(id, 'normal', { snap: 'left' });
    mode(id, 'minimized');
    mode(id, 'normal');
    expect(byId(id).snap).toBe('left');
    expect(byId(id).rect).toEqual(rectForZone('left', VIEW));
  });

  it('leaves a plain window exactly where it was — the path that already worked', () => {
    const id = float();
    const rect = { x: 100, y: 80, w: 500, h: 400 };
    layoutStore.dispatch({ type: 'SET_WINDOW_RECT', windowId: id, rect });
    const before = { ...byId(id).rect };
    mode(id, 'minimized');
    mode(id, 'normal');
    expect(byId(id).mode).toBe('normal');
    expect(byId(id).snap).toBeUndefined();
    expect(byId(id).rect).toEqual(before);
  });

  it('keeps the pre-maximize rect available after a minimize round trip', () => {
    // The restore rect belongs to the maximize being resumed. Un-minimizing must
    // not consume it, or un-maximizing afterwards has nothing to go back to.
    const id = float();
    const rect = { x: 120, y: 90, w: 480, h: 360 };
    layoutStore.dispatch({ type: 'SET_WINDOW_RECT', windowId: id, rect });
    mode(id, 'maximized');
    mode(id, 'minimized');
    mode(id, 'normal');
    mode(id, 'normal');
    expect(byId(id).mode).toBe('normal');
    expect(byId(id).rect).toEqual(rect);
  });

  it('does not overwrite the record when minimized twice', () => {
    const id = float();
    mode(id, 'maximized');
    mode(id, 'minimized');
    mode(id, 'minimized');
    mode(id, 'normal');
    expect(byId(id).mode).toBe('maximized');
  });

  it('re-derives the zone against a surface that changed while minimized', () => {
    const id = float();
    mode(id, 'maximized');
    mode(id, 'minimized');
    const bigger = { w: 2000, h: 1200 };
    layoutStore.dispatch({ type: 'SET_WINDOW_VIEWPORT', viewport: bigger });
    // No explicit viewport: this is the taskbar's restore, which reads the frame's.
    layoutStore.dispatch({ type: 'SET_WINDOW_MODE', windowId: id, mode: 'normal' });
    expect(byId(id).rect).toEqual(rectForZone('max', bigger));
  });

  it('forgets the record when the window is placed by hand while minimized', () => {
    const id = float();
    mode(id, 'maximized');
    mode(id, 'minimized');
    const rect = { x: 200, y: 120, w: 400, h: 300 };
    layoutStore.dispatch({ type: 'SET_WINDOW_RECT', windowId: id, rect });
    mode(id, 'normal');
    expect(byId(id).mode).toBe('normal');
    expect(byId(id).rect).toEqual(rect);
  });
});

describe('snap assist fill', () => {
  it('snaps a maximized window into the other half', () => {
    const a = float();
    const b = float();
    mode(a, 'maximized');
    mode(b, 'normal', { snap: 'right', fill: true });
    expect(byId(b).rect).toEqual(rectForZone('right', VIEW));
    expect(byId(a).snap).toBe('left');
    expect(byId(a).rect).toEqual(rectForZone('left', VIEW));
  });

  it('does nothing without the flag, so agent tools move one window only', () => {
    const a = float();
    const b = float();
    mode(a, 'maximized');
    mode(b, 'normal', { snap: 'right' });
    expect(byId(a).mode).toBe('maximized');
    expect(byId(a).snap).toBeUndefined();
  });

  it('keeps the filled window a restore rect from before it was maximized', () => {
    const a = float();
    const b = float();
    const rect = { x: 90, y: 70, w: 420, h: 320 };
    layoutStore.dispatch({ type: 'SET_WINDOW_RECT', windowId: a, rect });
    mode(a, 'maximized');
    mode(b, 'normal', { snap: 'left', fill: true });
    expect(byId(a).snap).toBe('right');
    mode(a, 'normal');
    expect(byId(a).rect).toEqual(rect);
  });

  it('leaves a window that is not in the way alone', () => {
    const a = float();
    const b = float();
    layoutStore.dispatch({
      type: 'SET_WINDOW_RECT',
      windowId: a,
      rect: { x: 900, y: 500, w: 400, h: 300 },
    });
    const before = { ...byId(a).rect };
    mode(b, 'normal', { snap: 'left', fill: true });
    expect(byId(a).rect).toEqual(before);
    expect(byId(a).snap).toBeUndefined();
  });

  it('does not fill for a corner, whose remainder is an L', () => {
    const a = float();
    const b = float();
    mode(a, 'maximized');
    mode(b, 'normal', { snap: 'tl', fill: true });
    expect(byId(a).mode).toBe('maximized');
  });

  it('does not fill on maximize, which leaves no other half', () => {
    const a = float();
    const b = float();
    mode(a, 'normal', { snap: 'left' });
    mode(b, 'maximized', { fill: true });
    expect(byId(a).snap).toBe('left');
  });
});
