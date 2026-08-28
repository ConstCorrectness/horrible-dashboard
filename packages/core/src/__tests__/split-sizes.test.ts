import { describe, expect, it } from 'vitest';

import { clampSize } from '../split-sizes';

describe('clampSize', () => {
  it('leaves a comfortable size alone', () => {
    expect(clampSize(300, 1000, 190, 320)).toBe(300);
  });

  it('holds the measured side at its floor', () => {
    expect(clampSize(10, 1000, 190, 320)).toBe(190);
  });

  it('stops before eating the other side s minimum', () => {
    expect(clampSize(900, 1000, 190, 320)).toBe(680);
  });

  /* Both minimums cannot be met in a container this narrow. The side under the
     pointer wins, because collapsing it would make the drag feel broken; the real
     answer to a container this small is the caller's `narrowBelow` stacking. */
  it('prefers the dragged side when the container cannot satisfy both', () => {
    expect(clampSize(400, 300, 190, 320)).toBe(190);
  });

  /* A zero extent is a container that has not been measured yet — the first frame,
     or a hidden pane. Returning NaN there would reach a style property. */
  it('survives an unmeasured container', () => {
    expect(clampSize(300, 0, 190, 320)).toBe(190);
    expect(clampSize(NaN, 1000, 190, 320)).toBe(190);
  });
});
