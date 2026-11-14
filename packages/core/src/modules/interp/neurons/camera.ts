/**
 * Camera motion and picking for the neuron stack, free of three.js so it can be unit
 * tested: every parameter eases toward its target on its own critically damped
 * spring, and a click is resolved by intersecting the ray with a layer's plane
 * analytically (no Raycaster over ~350k points).
 */

import { type Grid, indexOf } from './grid';

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

export interface SpringState {
  value: number;
  velocity: number;
}

/**
 * One step of a critically damped spring toward `target` (exact solution, so it is
 * stable at any step). `dt` is clamped to [0, 0.05] so a stalled frame — a
 * backgrounded tab resuming — does not teleport the camera.
 */
export function spring(
  value: number,
  velocity: number,
  target: number,
  dt: number,
  k = 11,
): SpringState {
  const step = Math.min(0.05, Math.max(0, Number.isFinite(dt) ? dt : 0));
  const w = value - target;
  const x = Math.exp(-k * step);
  const m = (velocity + k * w) * step;
  return { value: target + (w + m) * x, velocity: (velocity - k * m) * x };
}

export interface CameraParams {
  /** Focused layer; fractional while traversing between plates. */
  layer: number;
  zoom: number;
  /** Pan in grid cells from the plate's centre. */
  panX: number;
  panY: number;
  /** Extra separation between plates. */
  spread: number;
  /** Orbit angles in radians. */
  yaw: number;
  pitch: number;
}

export const CAMERA_KEYS = [
  'layer',
  'zoom',
  'panX',
  'panY',
  'spread',
  'yaw',
  'pitch',
] as const satisfies readonly (keyof CameraParams)[];

export const DEFAULT_CAMERA: Readonly<CameraParams> = Object.freeze({
  layer: 0,
  zoom: 1,
  panX: 0,
  panY: 0,
  spread: 0,
  yaw: 0.76,
  pitch: 0.18,
});

export const CAMERA_LIMITS = {
  zoom: [0.7, 10],
  yaw: [0.3, 1.5],
  pitch: [-0.55, 0.55],
} as const;

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));

/**
 * Clamp a camera into its legal range. Pan is bounded to ±half the grid when `grid`
 * is given, and `layer` to [0, layers − 1] when `layers` is.
 */
export function clampCamera(
  c: CameraParams,
  bounds: { grid?: Pick<Grid, 'cols' | 'rows'>; layers?: number } = {},
): CameraParams {
  const out: CameraParams = {
    ...c,
    zoom: clamp(c.zoom, ...CAMERA_LIMITS.zoom),
    yaw: clamp(c.yaw, ...CAMERA_LIMITS.yaw),
    pitch: clamp(c.pitch, ...CAMERA_LIMITS.pitch),
  };
  if (bounds.grid) {
    out.panX = clamp(c.panX, -bounds.grid.cols / 2, bounds.grid.cols / 2);
    out.panY = clamp(c.panY, -bounds.grid.rows / 2, bounds.grid.rows / 2);
  }
  if (bounds.layers !== undefined) out.layer = clamp(c.layer, 0, Math.max(0, bounds.layers - 1));
  return out;
}

export type CameraVelocity = Record<(typeof CAMERA_KEYS)[number], number>;

export const ZERO_VELOCITY: Readonly<CameraVelocity> = Object.freeze({
  layer: 0,
  zoom: 0,
  panX: 0,
  panY: 0,
  spread: 0,
  yaw: 0,
  pitch: 0,
});

export interface CameraStep {
  camera: CameraParams;
  velocity: CameraVelocity;
  /** False once every key has settled; the camera is then exactly the target. */
  moving: boolean;
}

/** Below this distance and speed a key counts as settled and snaps to its target. */
const SETTLE = 1e-4;

/** Advance every camera key one spring step toward `target`. */
export function stepCamera(
  current: CameraParams,
  velocity: CameraVelocity,
  target: CameraParams,
  dt: number,
): CameraStep {
  const camera = { ...current };
  const vel = { ...velocity };
  let moving = false;
  for (const key of CAMERA_KEYS) {
    const s = spring(current[key], velocity[key], target[key], dt);
    if (Math.abs(s.value - target[key]) < SETTLE && Math.abs(s.velocity) < SETTLE) {
      camera[key] = target[key];
      vel[key] = 0;
    } else {
      camera[key] = s.value;
      vel[key] = s.velocity;
      moving = true;
    }
  }
  return { camera, velocity: vel, moving };
}

/**
 * A layer plate in world space. Cell (row, col) has its centre at
 * `origin + colAxis·col·spacing + rowAxis·row·spacing`; `colAxis` and `rowAxis` are
 * unit vectors and orthogonal.
 */
export interface LayerPlane {
  origin: Vec3;
  colAxis: Vec3;
  rowAxis: Vec3;
  spacing: number;
}

const sub = (a: Vec3, b: Vec3): Vec3 => ({ x: a.x - b.x, y: a.y - b.y, z: a.z - b.z });
const dot = (a: Vec3, b: Vec3): number => a.x * b.x + a.y * b.y + a.z * b.z;
const cross = (a: Vec3, b: Vec3): Vec3 => ({
  x: a.y * b.z - a.z * b.y,
  y: a.z * b.x - a.x * b.z,
  z: a.x * b.y - a.y * b.x,
});

/** World-space centre of a neuron's cell — the inverse of `pickCell`. */
export function cellCenter(index: number, plane: LayerPlane, grid: Grid): Vec3 {
  const col = (index % grid.cols) * plane.spacing;
  const row = Math.floor(index / grid.cols) * plane.spacing;
  return {
    x: plane.origin.x + plane.colAxis.x * col + plane.rowAxis.x * row,
    y: plane.origin.y + plane.colAxis.y * col + plane.rowAxis.y * row,
    z: plane.origin.z + plane.colAxis.z * col + plane.rowAxis.z * row,
  };
}

/**
 * The neuron whose cell a ray hits on `plane`, or null when the ray is parallel to
 * the plane, the plane is behind the ray, or the hit is outside the grid (including
 * the empty tail of a partial last row).
 */
export function pickCell(
  rayOrigin: Vec3,
  rayDir: Vec3,
  plane: LayerPlane,
  grid: Grid,
): number | null {
  if (!(plane.spacing > 0)) return null;
  const normal = cross(plane.colAxis, plane.rowAxis);
  const denom = dot(rayDir, normal);
  if (Math.abs(denom) < 1e-9) return null;
  const t = dot(sub(plane.origin, rayOrigin), normal) / denom;
  if (!(t >= 0)) return null;
  const hit = {
    x: rayOrigin.x + rayDir.x * t,
    y: rayOrigin.y + rayDir.y * t,
    z: rayOrigin.z + rayDir.z * t,
  };
  const local = sub(hit, plane.origin);
  const col = Math.round(dot(local, plane.colAxis) / plane.spacing);
  const row = Math.round(dot(local, plane.rowAxis) / plane.spacing);
  return indexOf(row, col, grid);
}
