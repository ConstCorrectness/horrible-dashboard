/**
 * What a change of surface size does to the windows on it.
 *
 * The bug these guard: every viewport change rescaled every free window
 * proportionally and rounded. The rounding is lossy, so shrinking the app and
 * restoring it left windows a few pixels from where they had been (a 483px window
 * came back 492px), and a 10px scrollbar appearing resized every window on
 * screen. A layout *loaded* from a different surface still rescales — once.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import { clampRect, rectForZone } from '../snap';
import { layoutStore } from '../store';
import type { WindowRect, WindowState } from '../types';
import { loadFrame, windowsFrame } from './fixtures';

const VIEW = { w: 1024, h: 728 };
const RECT: WindowRect = { x: -5, y: 6, w: 483, h: 700 };

const frame = () => layoutStore.getSnapshot().frame;
const only = (): WindowState => frame().windows[0];
const resize = (w: number, h: number) =>
  layoutStore.dispatch({ type: 'SET_WINDOW_VIEWPORT', viewport: { w, h } });

/** One free window at RECT on a measured VIEW surface. */
function measuredDesktop(): string {
  loadFrame(windowsFrame([], { windowViewport: null }), 'vp');
  resize(VIEW.w, VIEW.h);
  layoutStore.dispatch({
    type: 'OPEN_WINDOW',
    pane: { instanceId: 'vp.a#1', viewId: 'vp.a' },
    rect: RECT,
  });
  return only().id;
}

beforeEach(() => {
  layoutStore.resetForTests();
});

describe('resizing the live surface', () => {
  it('leaves a free window exactly where the user put it', () => {
    measuredDesktop();
    resize(1014, 728); // a scrollbar appears
    expect(only().rect).toEqual(RECT);
    resize(500, 300);
    expect(only().rect).toEqual(RECT);
  });

  it('round-trips without drift', () => {
    measuredDesktop();
    for (let i = 0; i < 20; i++) {
      resize(500 + i * 7, 300 + i * 3);
      resize(VIEW.w, VIEW.h);
    }
    expect(only().rect).toEqual(RECT);
    expect(frame().windowViewport).toEqual(VIEW);
  });

  it('keeps the stored rect drawable: clamping it puts the titlebar on the surface', () => {
    measuredDesktop();
    resize(400, 300);
    const drawn = clampRect(only().rect, frame().windowViewport!);
    expect(drawn.y).toBeGreaterThanOrEqual(0);
    expect(drawn.y).toBeLessThan(300);
    expect(drawn.x).toBeLessThan(400);
  });

  it('still re-derives a snapped or maximized window from its zone', () => {
    const id = measuredDesktop();
    layoutStore.dispatch({ type: 'SET_WINDOW_MODE', windowId: id, mode: 'maximized' });
    resize(1400, 900);
    expect(only().rect).toEqual(rectForZone('max', { w: 1400, h: 900 }));
  });

  it('is not a user edit', () => {
    measuredDesktop();
    const { revision } = layoutStore.getSnapshot();
    resize(900, 600);
    expect(layoutStore.getSnapshot().revision).toBe(revision);
  });
});

describe('loading a layout saved on another surface', () => {
  // `windowsFrame` puts its one window at { x: 0, y: 0, w: 640, h: 480 }.
  const saved = () => windowsFrame(['vp.a'], { windowViewport: { w: 1600, h: 900 } });
  const HALF = { x: 0, y: 0, w: 320, h: 240 };

  it('rescales once when the surface is measured after the load', () => {
    loadFrame(saved(), 'vp');
    resize(800, 450);
    expect(only().rect).toEqual(HALF);
    // …and from then on the surface is the user's own.
    resize(1000, 600);
    expect(only().rect).toEqual(HALF);
  });

  it('rescales at the load itself when the surface was measured first', () => {
    resize(800, 450);
    loadFrame(saved(), 'vp');
    expect(frame().windowViewport).toEqual({ w: 800, h: 450 });
    expect(only().rect).toEqual(HALF);
  });
});
