import { describe, expect, it } from 'vitest';

import { MIN_POPOUT, clampToWorkArea, popoutIdOf } from '../src/popoutBounds';

const laptop = { x: 0, y: 0, width: 1920, height: 1040 };
const right = { x: 1920, y: 0, width: 2560, height: 1400 };

describe('popoutIdOf', () => {
  it('reads the id from a pop-out frame name', () => {
    expect(popoutIdOf('horrible-popout:p12')).toBe('p12');
  });

  it('refuses every other name', () => {
    expect(popoutIdOf('')).toBeNull();
    expect(popoutIdOf('_blank')).toBeNull();
    expect(popoutIdOf('horrible-popout:')).toBeNull();
    expect(popoutIdOf('horrible-popout:../x')).toBeNull();
    expect(popoutIdOf('other:p1')).toBeNull();
  });
});

describe('clampToWorkArea', () => {
  it('leaves a window that already fits alone', () => {
    const b = { x: 100, y: 80, width: 800, height: 600 };
    expect(clampToWorkArea(b, [laptop, right])).toEqual(b);
  });

  it('keeps a window on the monitor it mostly sits on', () => {
    const b = { x: 2200, y: 100, width: 900, height: 700 };
    expect(clampToWorkArea(b, [laptop, right])).toEqual(b);
  });

  it('brings a window from a monitor that is gone back onto the primary', () => {
    const b = { x: 2200, y: 100, width: 900, height: 700 };
    expect(clampToWorkArea(b, [laptop])).toEqual({ x: 1020, y: 100, width: 900, height: 700 });
  });

  it('shrinks a window larger than the work area', () => {
    const b = { x: -50, y: -50, width: 4000, height: 3000 };
    expect(clampToWorkArea(b, [laptop])).toEqual({ ...laptop });
  });

  it('grows a too-small window to the minimum', () => {
    const b = { x: 10, y: 10, width: 20, height: 20 };
    expect(clampToWorkArea(b, [laptop])).toEqual({
      x: 10,
      y: 10,
      width: MIN_POPOUT.width,
      height: MIN_POPOUT.height,
    });
  });

  it('passes bounds through when the displays are unknown', () => {
    const b = { x: 5000, y: 5000, width: 10, height: 10 };
    expect(clampToWorkArea(b, [])).toEqual(b);
  });
});
