/**
 * Placement is the part of a floating layer that only ever gets exercised at the
 * edges of the window, which is exactly where nobody clicks while developing —
 * hence the two real bugs pinned here: a menu positioned at the raw cursor with no
 * clamp at all, and one clamped against a hardcoded guess at its own size.
 */
import { describe, expect, it } from 'vitest';

import { placeLayer, type Rect } from '../placement';

const VIEWPORT = { width: 1000, height: 800 };
const point = (x: number, y: number): Rect => ({ x, y, width: 0, height: 0 });

describe('placeLayer', () => {
  it('puts a menu at the cursor when there is room', () => {
    const p = placeLayer({
      anchor: point(100, 100),
      content: { width: 200, height: 300 },
      viewport: VIEWPORT,
    });
    expect(p).toMatchObject({ left: 100, top: 100, side: 'bottom' });
  });

  it('flips above the cursor near the bottom edge', () => {
    // 780 + 300 would run 280px past the bottom.
    const p = placeLayer({
      anchor: point(100, 780),
      content: { width: 200, height: 300 },
      viewport: VIEWPORT,
    });
    expect(p.side).toBe('top');
    expect(p.top).toBe(480);
    expect(p.top + 300).toBeLessThanOrEqual(VIEWPORT.height);
  });

  it('clamps horizontally near the right edge instead of overhanging', () => {
    const p = placeLayer({
      anchor: point(950, 100),
      content: { width: 200, height: 100 },
      viewport: VIEWPORT,
      padding: 4,
    });
    expect(p.left).toBe(796); // 1000 - 200 - 4
  });

  it('handles the corner: both axes at once', () => {
    const p = placeLayer({
      anchor: point(990, 790),
      content: { width: 240, height: 320 },
      viewport: VIEWPORT,
      padding: 8,
    });
    expect(p.left).toBe(752);
    expect(p.top).toBe(470);
    expect(p.left).toBeGreaterThanOrEqual(8);
    expect(p.top).toBeGreaterThanOrEqual(8);
  });

  it('caps a menu taller than the window to the space below the cursor', () => {
    const p = placeLayer({
      anchor: point(100, 400),
      content: { width: 200, height: 1200 },
      viewport: VIEWPORT,
      padding: 10,
      shrink: true,
    });
    // It stays at the cursor and scrolls. Jumping to the top of the window to show
    // more of it would detach the menu from the thing it acts on.
    expect(p.top).toBe(400);
    expect(p.maxHeight).toBe(390); // 800 - 400 - 10
    expect(p.top + p.maxHeight! + 10).toBeLessThanOrEqual(VIEWPORT.height);
  });

  it('does not report a max size when the layer fits', () => {
    const p = placeLayer({
      anchor: point(10, 10),
      content: { width: 100, height: 100 },
      viewport: VIEWPORT,
      shrink: true,
    });
    expect(p.maxHeight).toBeUndefined();
    expect(p.maxWidth).toBeUndefined();
  });

  it('picks the roomier side when the layer fits on neither', () => {
    // 350 above, 400 below (anchor is 50 tall), content 600: neither fits, so the
    // preference (`top`) must lose to the side that shows more of the menu.
    const p = placeLayer({
      anchor: { x: 100, y: 350, width: 0, height: 50 },
      content: { width: 200, height: 600 },
      viewport: VIEWPORT,
      side: 'top',
      shrink: true,
    });
    expect(p.side).toBe('bottom');
  });

  it('opens a submenu to the left when the right edge is close', () => {
    const p = placeLayer({
      anchor: { x: 900, y: 100, width: 80, height: 24 },
      content: { width: 220, height: 150 },
      viewport: VIEWPORT,
      side: 'right',
    });
    expect(p.side).toBe('left');
    expect(p.left).toBe(680); // 900 - 220
  });

  it('aligns a popover under its anchor and keeps it on screen', () => {
    const p = placeLayer({
      anchor: { x: 940, y: 40, width: 40, height: 24 },
      content: { width: 300, height: 120 },
      viewport: VIEWPORT,
      side: 'bottom',
      align: 'end',
      offset: 6,
    });
    expect(p.side).toBe('bottom');
    expect(p.top).toBe(70); // 40 + 24 + 6
    // `end` puts the popover's right edge on the anchor's: 940 + 40 - 300. That
    // already sits inside the padded viewport, so no clamping is needed.
    expect(p.left).toBe(680);
  });

  it('clamps an end-aligned popover whose anchor is hard against the edge', () => {
    const p = placeLayer({
      anchor: { x: 985, y: 40, width: 15, height: 24 },
      content: { width: 300, height: 120 },
      viewport: VIEWPORT,
      align: 'end',
      padding: 4,
    });
    expect(p.left).toBe(696); // 1000 - 300 - 4
  });

  it('centres on the anchor when asked', () => {
    const p = placeLayer({
      anchor: { x: 400, y: 300, width: 100, height: 20 },
      content: { width: 200, height: 80 },
      viewport: VIEWPORT,
      align: 'center',
    });
    expect(p.left).toBe(350); // 400 + 50 - 100
  });

  it('never returns a negative coordinate for a tiny viewport', () => {
    // A narrow window (a docked pane in the desktop shell) is the case where the
    // layer is simply bigger than the space; it must still start on screen.
    const p = placeLayer({
      anchor: point(5, 5),
      content: { width: 400, height: 400 },
      viewport: { width: 200, height: 200 },
      padding: 6,
      shrink: true,
    });
    expect(p.left).toBe(6);
    expect(p.top).toBe(6);
    expect(p.maxHeight).toBe(188);
  });
});
