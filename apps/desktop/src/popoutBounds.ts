/**
 * Pure rules for pop-out windows: which `window.open` frame names are pop-outs,
 * and where a remembered window may reappear. No Electron here, so both are tested
 * without a display (test/popoutBounds.test.ts).
 */
import type { Bounds } from './urls.js';

/** The frame-name prefix the page opens a pop-out under (`PopoutHost.tsx`). */
export const POPOUT_FRAME_PREFIX = 'horrible-popout:';

/** The smallest pop-out worth reopening; anything smaller comes back at this. */
export const MIN_POPOUT = { width: 320, height: 200 };

/**
 * The pop-out id a frame name carries, or null when the name is anything else.
 * The id is the layout's (`p<n>`), so it is short and plain; a name outside that
 * shape is refused rather than trusted, since it decides whether a window opens.
 */
export function popoutIdOf(frameName: string): string | null {
  if (!frameName.startsWith(POPOUT_FRAME_PREFIX)) return null;
  const id = frameName.slice(POPOUT_FRAME_PREFIX.length);
  return /^[A-Za-z0-9_-]{1,64}$/.test(id) ? id : null;
}

function overlap(a: Bounds, b: Bounds): number {
  const w = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  return w > 0 && h > 0 ? w * h : 0;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(Math.max(v, lo), Math.max(lo, hi));
}

/**
 * Fit remembered bounds onto the monitors there are now.
 *
 * A layout saved with a pop-out on a second monitor is reopened on a laptop alone;
 * Windows would place that window off every screen, where it can't be seen or
 * dragged back. So: the work area it overlaps most (else the first, the primary),
 * shrunk to fit inside it, then moved inside it. `workAreas` empty means "unknown",
 * and the bounds pass through.
 */
export function clampToWorkArea(b: Bounds, workAreas: Bounds[]): Bounds {
  if (workAreas.length === 0) return b;
  let area = workAreas[0];
  let best = 0;
  for (const candidate of workAreas) {
    const o = overlap(b, candidate);
    if (o > best) {
      best = o;
      area = candidate;
    }
  }
  const width = clamp(Math.round(b.width), MIN_POPOUT.width, area.width);
  const height = clamp(Math.round(b.height), MIN_POPOUT.height, area.height);
  return {
    x: clamp(Math.round(b.x), area.x, area.x + area.width - width),
    y: clamp(Math.round(b.y), area.y, area.y + area.height - height),
    width,
    height,
  };
}
