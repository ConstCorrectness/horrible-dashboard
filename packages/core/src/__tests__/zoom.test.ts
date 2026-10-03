/** Page zoom: the step ladder, and that the browser layout never takes the keys. */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setWindowControl, type WindowControl } from '../window';
import { canZoom, currentZoom, setZoom, stepZoom, zoomIn, zoomOut } from '../zoom';

describe('stepZoom', () => {
  it('walks Chromium’s ladder and stops at its ends', () => {
    expect(stepZoom(1, 1)).toBe(1.1);
    expect(stepZoom(1, -1)).toBe(0.9);
    expect(stepZoom(1, 2)).toBe(1.25);
    expect(stepZoom(3, 1)).toBe(3);
    expect(stepZoom(0.5, -1)).toBe(0.5);
  });

  it('moves a level between rungs to the next rung in that direction', () => {
    expect(stepZoom(1.05, 1)).toBe(1.1);
    expect(stepZoom(1.05, -1)).toBe(1);
  });
});

describe('setZoom', () => {
  afterEach(() => setWindowControl(null));

  it('is unavailable in the browser layout', async () => {
    setWindowControl(null);
    expect(canZoom()).toBe(false);
    expect(await setZoom(1.5)).toBeNull();
  });

  it('drives the shell and keeps the level it reports', async () => {
    const setZoomImpl = vi.fn(async (f: number) => Math.min(f, 2));
    setWindowControl({ setZoom: setZoomImpl } as unknown as WindowControl);
    expect(canZoom()).toBe(true);
    await setZoom(1);
    await zoomIn();
    expect(setZoomImpl).toHaveBeenLastCalledWith(1.1);
    expect(currentZoom()).toBe(1.1);
    await zoomOut();
    expect(currentZoom()).toBe(1);
    expect(await setZoom(3)).toBe(2);
    expect(currentZoom()).toBe(2);
  });
});
