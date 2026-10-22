/**
 * The window-placement recorder: what it writes, and how often.
 *
 * The bug these guard: every window geometry change wrote `frame.windowPlacement`
 * synchronously, and resizing the app rescaled every window's rect once per frame
 * — so an app resize wrote the setting (one `PUT`, one app-wide settings
 * notification) per window per frame, and a maximize paid for a settings write
 * inside the frame its animation started on.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { registry } from '../../registry';
import { setSettingsBackend, type SettingValue } from '../../settings';
import { installFrameController, setPaneWindowed } from '../controller';
import { seedFromPreset, type FramePreset } from '../presets';
import { rectForZone } from '../snap';
import { layoutStore } from '../store';
import type { FrameState, LayoutNode } from '../types';
import {
  flushPlacements,
  lastPlacement,
  PLACEMENT_FLUSH_MS,
  WINDOW_PLACEMENT_KEY,
} from '../window-placement';

const Stub = () => null;
const KNOWN = new Set(['wp.a']);
const VIEW = { w: 1600, h: 900 };

const preset: FramePreset = {
  id: 'wp',
  name: 'Window placement',
  frame: { center: { pane: 'wp.a' } },
};

const puts: [string, SettingValue][] = [];

beforeAll(() => {
  registry.register({
    id: 'window-placement-test',
    title: 'Window placement test',
    panels: [{ id: 'wp.a', title: 'Alpha', component: Stub, role: 'document', icon: 'A' }],
  });
  setSettingsBackend({
    load: async () => ({ values: {} }),
    put: async (key, value) => {
      puts.push([key, value]);
    },
    remove: async () => {},
  });
  installFrameController();
});

beforeEach(() => {
  vi.useFakeTimers();
  layoutStore.resetForTests();
  layoutStore.dispatch({
    type: 'LOAD_WORKSPACE',
    workspaceId: 'wp',
    frame: seedFromPreset(preset, { knownViews: KNOWN }),
  });
  layoutStore.dispatch({ type: 'SET_WINDOW_VIEWPORT', viewport: VIEW });
  flushPlacements();
  puts.length = 0;
});

afterEach(() => {
  vi.useRealTimers();
});

const frame = (): FrameState => layoutStore.getSnapshot().frame;
const placementWrites = () => puts.filter(([key]) => key === WINDOW_PLACEMENT_KEY);

function float(): string {
  const walk = (node: LayoutNode): string[] =>
    node.kind === 'area' ? node.tabs.map((t) => t.instanceId) : node.children.flatMap(walk);
  setPaneWindowed(walk(frame().center)[0], true);
  return frame().windows[0].id;
}

describe('placement recorder', () => {
  it('lands a burst of moves as one write, readable before it lands', () => {
    const id = float();
    for (let i = 1; i <= 5; i++) {
      layoutStore.dispatch({
        type: 'SET_WINDOW_RECT',
        windowId: id,
        rect: { x: 10 * i, y: 20, w: 400, h: 300 },
      });
    }
    expect(placementWrites()).toHaveLength(0);
    expect(lastPlacement('wp.a')?.rect.x).toBe(50);

    vi.advanceTimersByTime(PLACEMENT_FLUSH_MS);
    expect(placementWrites()).toHaveLength(1);
    expect(JSON.parse(String(placementWrites()[0][1]))['wp.a'].rect.x).toBe(50);
  });

  it('does not record the rescale of an app resize, then or on the next dispatch', () => {
    const id = float();
    layoutStore.dispatch({
      type: 'SET_WINDOW_RECT',
      windowId: id,
      rect: { x: 100, y: 100, w: 800, h: 450 },
    });
    vi.advanceTimersByTime(PLACEMENT_FLUSH_MS);
    puts.length = 0;

    for (let i = 1; i <= 10; i++) {
      layoutStore.dispatch({
        type: 'SET_WINDOW_VIEWPORT',
        viewport: { w: VIEW.w - i * 40, h: VIEW.h - i * 20 },
      });
    }
    expect(frame().windows[0].rect.w).not.toBe(800);
    // Any later dispatch sees rects that differ from the stored placement but
    // not from the last snapshot — still not a placement.
    layoutStore.dispatch({ type: 'FOCUS_WINDOW', windowId: id });
    vi.advanceTimersByTime(PLACEMENT_FLUSH_MS);

    expect(placementWrites()).toHaveLength(0);
    expect(lastPlacement('wp.a')?.rect).toEqual({ x: 100, y: 100, w: 800, h: 450 });
  });

  it('still records a maximize — it is a geometry change, not a rescale', () => {
    const id = float();
    layoutStore.dispatch({
      type: 'SET_WINDOW_MODE',
      windowId: id,
      mode: 'maximized',
      viewport: VIEW,
    });
    expect(lastPlacement('wp.a')?.rect).toEqual(rectForZone('max', VIEW));
  });
});
