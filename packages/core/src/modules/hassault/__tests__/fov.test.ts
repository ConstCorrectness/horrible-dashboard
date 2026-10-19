/**
 * One field-of-view row for both clients. The pane's old `hassault.fov` is read
 * only while `hassault.video.fov` has never been saved, and both clamp to the
 * schema's 60–110.
 */
import { describe, expect, it } from 'vitest';

import { clampFov, FOV_KEY, LEGACY_FOV_KEY, resolveFov } from '../menu-panels';

describe('field of view', () => {
  it('is the shared native row', () => {
    expect(FOV_KEY).toBe('hassault.video.fov');
    expect(LEGACY_FOV_KEY).toBe('hassault.fov');
  });

  it('reads the old row only until the shared one is saved', () => {
    expect(resolveFov(75, false, 100)).toBe(100);
    expect(resolveFov(90, true, 100)).toBe(90);
    expect(resolveFov(75, false, undefined)).toBe(75);
  });

  it('clamps to 60-110 from either row', () => {
    expect(resolveFov(75, false, 130)).toBe(110);
    expect(resolveFov(40, true, undefined)).toBe(60);
    expect(clampFov('wide')).toBe(75);
  });
});
