/**
 * Mouse look, and the one sign in it that is easy to get backwards.
 *
 * This shipped inverted: moving the mouse right turned the view left. It is not
 * the sort of thing a type checker or a physics vector catches, because both
 * directions are perfectly valid rotations — the only thing that makes one of them
 * wrong is which way the world is drawn. So the assertion here is written against
 * the *rendered* frame of reference rather than against the sign of `yaw`:
 *
 *   - `step` walks the player toward `(cos yaw, sin yaw)` in cube coordinates, so
 *     that is the forward direction by definition (`player.ts`);
 *   - the renderer maps cube `(x, y)` onto three's `(x, z)`, and three's camera
 *     right vector is `forward × up`, which works out to `(-sin yaw, cos yaw)` in
 *     those same cube axes (`HorribleAssaultPanel`'s camera rotation);
 *   - therefore "the view turned right" means the new forward direction leans
 *     toward the old right vector — a positive dot product.
 *
 * Which is the whole point: if someone later changes the camera's rotation
 * derivation, this test still describes what the player experiences.
 */
import { describe, expect, it } from 'vitest';

import { applyLook, createPlayer, LOOK_RADIANS_PER_PIXEL } from '../player';

/** Forward, in cube axes, as `step` defines it. */
function forward(yaw: number): [number, number] {
  return [Math.cos(yaw), Math.sin(yaw)];
}

/** The camera's right vector, in the same axes. */
function right(yaw: number): [number, number] {
  return [-Math.sin(yaw), Math.cos(yaw)];
}

function dot(a: [number, number], b: [number, number]): number {
  return a[0] * b[0] + a[1] * b[1];
}

describe('applyLook', () => {
  it('turns the view toward the right when the mouse moves right', () => {
    const player = createPlayer(0, 0, 0);
    const wasRight = right(player.yaw);
    applyLook(player, 100, 0);
    expect(dot(forward(player.yaw), wasRight)).toBeGreaterThan(0);
  });

  it('turns the view away from the right when the mouse moves left', () => {
    const player = createPlayer(0, 0, 0);
    const wasRight = right(player.yaw);
    applyLook(player, -100, 0);
    expect(dot(forward(player.yaw), wasRight)).toBeLessThan(0);
  });

  it('holds at any starting angle, not just zero', () => {
    for (const start of [-2.5, -0.7, 0, 1.2, 3.1]) {
      const player = createPlayer(0, 0, 0, start);
      const wasRight = right(start);
      applyLook(player, 40, 0);
      expect(dot(forward(player.yaw), wasRight)).toBeGreaterThan(0);
    }
  });

  it('looks up when the mouse moves up, and down when it moves down', () => {
    const player = createPlayer(0, 0, 0);
    applyLook(player, 0, -50);
    expect(player.pitch).toBeGreaterThan(0);
    applyLook(player, 0, 100);
    expect(player.pitch).toBeLessThan(0);
  });

  it('scales by sensitivity, and treats 1 as the shipped feel', () => {
    const slow = createPlayer(0, 0, 0);
    const fast = createPlayer(0, 0, 0);
    applyLook(slow, 100, 0, 1);
    applyLook(fast, 100, 0, 2);
    expect(slow.yaw).toBeCloseTo(100 * LOOK_RADIANS_PER_PIXEL, 10);
    expect(fast.yaw).toBeCloseTo(slow.yaw * 2, 10);
  });

  it('refuses a negative sensitivity rather than inverting the axis', () => {
    // A negative multiplier would silently reintroduce exactly the bug this file
    // exists for, from a settings value rather than from code.
    const player = createPlayer(0, 0, 0);
    applyLook(player, 100, 0, -3);
    expect(player.yaw).toBe(0);
  });

  it('never lets pitch reach the angle that flips the view over', () => {
    const player = createPlayer(0, 0, 0);
    applyLook(player, 0, -100_000);
    expect(player.pitch).toBeLessThan(Math.PI / 2);
    expect(player.pitch).toBeGreaterThan(Math.PI / 2 - 0.01);
    applyLook(player, 0, 200_000);
    expect(player.pitch).toBeGreaterThan(-Math.PI / 2);
  });
});
