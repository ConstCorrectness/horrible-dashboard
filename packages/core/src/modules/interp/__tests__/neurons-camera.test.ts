import { describe, expect, it } from 'vitest';

import {
  type CameraParams,
  cellCenter,
  clampCamera,
  DEFAULT_CAMERA,
  type LayerPlane,
  pickCell,
  spring,
  stepCamera,
  ZERO_VELOCITY,
} from '../neurons/camera';
import { gridOf } from '../neurons/grid';

describe('spring', () => {
  it('settles on the target without overshoot', () => {
    let s = { value: 0, velocity: 0 };
    let max = 0;
    for (let i = 0; i < 120; i++) {
      s = spring(s.value, s.velocity, 1, 1 / 60);
      max = Math.max(max, s.value);
    }
    expect(s.value).toBeCloseTo(1, 4);
    expect(max).toBeLessThanOrEqual(1 + 1e-9);
  });

  it('clamps dt so a stalled frame is one 50 ms step', () => {
    expect(spring(0, 0, 1, 5)).toEqual(spring(0, 0, 1, 0.05));
    for (const dt of [-1, Number.NaN]) {
      const s = spring(0.3, 2, 1, dt);
      expect(s.value).toBeCloseTo(0.3, 12);
      expect(s.velocity).toBe(2);
    }
  });
});

describe('camera', () => {
  it('clamps zoom, yaw, pitch, pan and layer', () => {
    const wild: CameraParams = {
      layer: 40,
      zoom: 50,
      panX: 500,
      panY: -500,
      spread: 2,
      yaw: 3,
      pitch: -2,
    };
    expect(clampCamera(wild, { grid: { cols: 99, rows: 99 }, layers: 36 })).toEqual({
      layer: 35,
      zoom: 10,
      panX: 49.5,
      panY: -49.5,
      spread: 2,
      yaw: 1.5,
      pitch: -0.55,
    });
    expect(clampCamera({ ...DEFAULT_CAMERA, zoom: 0.1 }).zoom).toBe(0.7);
    expect(clampCamera(DEFAULT_CAMERA)).toEqual(DEFAULT_CAMERA);
  });

  it('springs every key to the target and stops moving', () => {
    const target: CameraParams = {
      layer: 12,
      zoom: 3,
      panX: 4,
      panY: -2,
      spread: 1,
      yaw: 1.2,
      pitch: -0.3,
    };
    let cam: CameraParams = { ...DEFAULT_CAMERA };
    let vel = { ...ZERO_VELOCITY };
    let frames = 0;
    for (;;) {
      const step = stepCamera(cam, vel, target, 1 / 60);
      cam = step.camera;
      vel = step.velocity;
      frames++;
      if (!step.moving) break;
      expect(frames).toBeLessThan(600);
    }
    expect(cam).toEqual(target);
    expect(Object.values(vel).every((v) => v === 0)).toBe(true);
    expect(stepCamera(cam, vel, target, 1 / 60).moving).toBe(false);
  });
});

describe('pickCell', () => {
  const grid = gridOf(9728);
  // A tilted plate: columns along x, rows along a diagonal in y/z.
  const r = Math.SQRT1_2;
  const plane: LayerPlane = {
    origin: { x: -5, y: 2, z: 1 },
    colAxis: { x: 1, y: 0, z: 0 },
    rowAxis: { x: 0, y: r, z: -r },
    spacing: 0.1,
  };
  const eye = { x: 3, y: 20, z: 15 };
  const toward = (p: { x: number; y: number; z: number }) => ({
    x: p.x - eye.x,
    y: p.y - eye.y,
    z: p.z - eye.z,
  });

  it('inverts cellCenter for every 97th neuron and the last one', () => {
    for (let i = 0; i < grid.ffn; i += 97)
      expect(pickCell(eye, toward(cellCenter(i, plane, grid)), plane, grid)).toBe(i);
    expect(pickCell(eye, toward(cellCenter(9727, plane, grid)), plane, grid)).toBe(9727);
  });

  it('snaps a hit within half a cell to that cell', () => {
    const c = cellCenter(150, plane, grid);
    const nudged = { x: c.x + 0.04, y: c.y + 0.04 * r, z: c.z - 0.04 * r };
    expect(pickCell(eye, toward(nudged), plane, grid)).toBe(150);
  });

  it('returns null off the grid, in the empty tail, parallel, or behind', () => {
    const before = { x: plane.origin.x - 0.2, y: plane.origin.y, z: plane.origin.z };
    expect(pickCell(eye, toward(before), plane, grid)).toBeNull();
    // Row 98 holds cols 0–25; col 50 is past ffn-1.
    const tail = cellCenter(98 * 99 + 26, plane, grid);
    const empty = { x: tail.x + 24 * plane.spacing, y: tail.y, z: tail.z };
    expect(pickCell(eye, toward(empty), plane, grid)).toBeNull();
    expect(pickCell(eye, { x: 1, y: 0, z: 0 }, plane, grid)).toBeNull();
    const c = cellCenter(10, plane, grid);
    const away = toward(c);
    expect(pickCell(eye, { x: -away.x, y: -away.y, z: -away.z }, plane, grid)).toBeNull();
  });
});
