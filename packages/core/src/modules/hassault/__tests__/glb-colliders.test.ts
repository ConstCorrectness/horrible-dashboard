/**
 * `glb-colliders.json` carries its own cases, and `world3d.rs` replays the same
 * ones, so the browser and the native client agree on what is solid.
 */
import { describe, expect, it } from 'vitest';
import { COLLIDER_TABLE, glbNodeCollides } from '../glb-colliders';

describe('glb colliders', () => {
  it.each(COLLIDER_TABLE.cases)('$name collides: $collides', (c) => {
    expect(glbNodeCollides(c.name, c.materials, c.size)).toBe(c.collides);
  });

  it('matches name keywords case-sensitively, so Perimeter is not a rim', () => {
    expect(glbNodeCollides('Perimeter_Wall_Stub', [], [0.5, 0.5, 0.5])).toBe(true);
    expect(glbNodeCollides('Wheel_Rim', [], [0.5, 0.5, 0.5])).toBe(false);
  });

  it('matches material keywords case-insensitively', () => {
    expect(glbNodeCollides('Pane', ['mat_glass_window'], [0.5, 0.1, 0.5])).toBe(false);
    expect(glbNodeCollides('Pane', ['Mat_Smoked_Glass'], [0.5, 0.1, 0.5])).toBe(false);
  });

  it('never collides with a node marked NonCol, however large', () => {
    expect(glbNodeCollides('Canopy_NonCol', [], [40, 40, 10])).toBe(false);
  });
});
