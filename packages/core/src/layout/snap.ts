/**
 * Window geometry: snap zones, rescaling, clamping and bulk arrangements.
 *
 * Pure and DOM-free, for the same reason the rest of this directory is: the drag
 * handler that draws the snap preview and the agent tool that snaps a window without
 * a pointer must land on **identical** rects. A zone computed in the pointer handler
 * and a rect computed in the reducer would drift the moment either was tweaked, and
 * the symptom — the preview showing one thing and the drop doing another — is the
 * kind of bug nobody reports precisely.
 *
 * See docs/architecture/desktop-shell.mdx.
 */
import type { SnapZone, WindowRect, WindowState } from './types';

export interface SnapConfig {
  /** Pointer distance from an edge that arms a half snap. */
  edge: number;
  /** Distance from a corner that arms a quarter snap; must exceed `edge`. */
  corner: number;
  /** Gap left between snapped windows and the surface edge. */
  gap: number;
}

export const DEFAULT_SNAP: SnapConfig = { edge: 12, corner: 72, gap: 0 };

/** Smallest a window may be dragged to. Below this the chrome stops being usable. */
export const MIN_WINDOW_SIZE = { w: 240, h: 140 };

/** How much of a window's titlebar must stay on the surface. See `clampRect`. */
export const TITLEBAR_KEEP = { w: 120, h: 28 };

export interface Size {
  w: number;
  h: number;
}

/**
 * The snap zone a pointer at `p` is over, or null.
 *
 * Corners are tested before edges: inside a corner box the pointer is within `edge`
 * of *two* sides at once, and whichever edge test ran first would win arbitrarily.
 */
export function snapZoneAt(
  p: { x: number; y: number },
  viewport: Size,
  cfg: SnapConfig = DEFAULT_SNAP,
): SnapZone | null {
  const { edge, corner } = cfg;
  const nearLeft = p.x <= edge;
  const nearRight = p.x >= viewport.w - edge;
  const nearTop = p.y <= edge;
  const nearBottom = p.y >= viewport.h - edge;
  const cornerLeft = p.x <= corner;
  const cornerRight = p.x >= viewport.w - corner;
  const cornerTop = p.y <= corner;
  const cornerBottom = p.y >= viewport.h - corner;

  // Corners first — see above.
  if ((nearLeft && cornerTop) || (nearTop && cornerLeft)) return 'tl';
  if ((nearRight && cornerTop) || (nearTop && cornerRight)) return 'tr';
  if ((nearLeft && cornerBottom) || (nearBottom && cornerLeft)) return 'bl';
  if ((nearRight && cornerBottom) || (nearBottom && cornerRight)) return 'br';

  // The top edge maximizes rather than snapping to a top half: that is the near
  // universal convention, and a "top half" is the one snap nobody reaches for.
  if (nearTop) return 'max';
  if (nearLeft) return 'left';
  if (nearRight) return 'right';
  if (nearBottom) return 'bottom';
  return null;
}

/**
 * The rect a zone occupies. Halves and quarters tile the surface exactly (the two
 * halves of an odd width differ by a pixel rather than overlapping or leaving a seam).
 */
export function rectForZone(
  zone: SnapZone,
  viewport: Size,
  cfg: SnapConfig = DEFAULT_SNAP,
): WindowRect {
  const g = cfg.gap;
  const x0 = g;
  const y0 = g;
  const fullW = Math.max(0, viewport.w - g * 2);
  const fullH = Math.max(0, viewport.h - g * 2);
  const halfW = Math.floor((fullW - g) / 2);
  const halfH = Math.floor((fullH - g) / 2);
  // The far half absorbs the odd pixel so the two always sum to the full extent.
  const restW = fullW - halfW - g;
  const restH = fullH - halfH - g;
  const midX = x0 + halfW + g;
  const midY = y0 + halfH + g;

  switch (zone) {
    case 'max':
      return { x: x0, y: y0, w: fullW, h: fullH };
    case 'left':
      return { x: x0, y: y0, w: halfW, h: fullH };
    case 'right':
      return { x: midX, y: y0, w: restW, h: fullH };
    case 'top':
      return { x: x0, y: y0, w: fullW, h: halfH };
    case 'bottom':
      return { x: x0, y: midY, w: fullW, h: restH };
    case 'tl':
      return { x: x0, y: y0, w: halfW, h: halfH };
    case 'tr':
      return { x: midX, y: y0, w: restW, h: halfH };
    case 'bl':
      return { x: x0, y: midY, w: halfW, h: restH };
    case 'br':
      return { x: midX, y: midY, w: restW, h: restH };
  }
}

/** Scale a rect from one surface size to another, proportionally on each axis. */
export function rescaleRect(rect: WindowRect, from: Size, to: Size): WindowRect {
  // A zero-sized origin carries no information to scale by — a surface that was
  // never measured, or measured while hidden. Pass the rect through untouched
  // rather than multiplying by Infinity and losing the layout.
  if (from.w <= 0 || from.h <= 0) return rect;
  const sx = to.w / from.w;
  const sy = to.h / from.h;
  return {
    x: Math.round(rect.x * sx),
    y: Math.round(rect.y * sy),
    w: Math.round(rect.w * sx),
    h: Math.round(rect.h * sy),
  };
}

/**
 * Keep a window reachable: at least `TITLEBAR_KEEP` of its titlebar on the surface,
 * never above the top edge, and never smaller than `MIN_WINDOW_SIZE`.
 *
 * The `y >= 0` clamp is not cosmetic. The app's own titlebar strip sits above the
 * surface and carries the Tauri drag region; a window allowed to sit at a negative y
 * would put its own titlebar behind it, where dragging moves the OS window instead
 * and the user has no way to get the window back.
 */
export function clampRect(
  rect: WindowRect,
  viewport: Size,
  min: Size = MIN_WINDOW_SIZE,
): WindowRect {
  const w = Math.max(min.w, Math.min(rect.w, Math.max(min.w, viewport.w)));
  const h = Math.max(min.h, Math.min(rect.h, Math.max(min.h, viewport.h)));
  const minX = -(w - TITLEBAR_KEEP.w);
  const maxX = viewport.w - TITLEBAR_KEEP.w;
  const maxY = viewport.h - TITLEBAR_KEEP.h;
  return {
    x: Math.round(Math.min(Math.max(rect.x, minX), Math.max(minX, maxX))),
    y: Math.round(Math.min(Math.max(rect.y, 0), Math.max(0, maxY))),
    w: Math.round(w),
    h: Math.round(h),
  };
}

/** Where the nth new window lands when nobody said. A diagonal cascade, wrapped. */
export function cascadeRect(index: number, viewport: Size): WindowRect {
  const w = Math.max(MIN_WINDOW_SIZE.w, Math.round(viewport.w * 0.52));
  const h = Math.max(MIN_WINDOW_SIZE.h, Math.round(viewport.h * 0.58));
  const step = 28;
  // Wrap before the cascade walks off the surface, rather than clamping every
  // window after the eighth onto the same spot.
  const room = Math.max(1, Math.floor((viewport.w - w - 40) / step) || 1);
  const n = ((index % room) + room) % room;
  return clampRect({ x: 40 + n * step, y: 32 + n * step, w, h }, viewport);
}

export type ArrangeStyle = 'grid' | 'cascade' | 'columns' | 'rows';

/**
 * Bulk arrangement, behind the agent's `desktop.arrange` verb and the desktop context
 * menu. Returns one rect per input window, in the order given.
 */
export function arrangeWindows(
  windows: readonly WindowState[],
  viewport: Size,
  style: ArrangeStyle,
  cfg: SnapConfig = DEFAULT_SNAP,
): WindowRect[] {
  const n = windows.length;
  if (n === 0) return [];
  if (style === 'cascade') return windows.map((_, i) => cascadeRect(i, viewport));

  const cols = style === 'columns' ? n : style === 'rows' ? 1 : Math.ceil(Math.sqrt(n));
  const rows = Math.ceil(n / cols);
  const g = cfg.gap;
  const cellW = Math.floor((viewport.w - g * (cols + 1)) / cols);
  const cellH = Math.floor((viewport.h - g * (rows + 1)) / rows);
  return windows.map((_, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    return {
      x: g + col * (cellW + g),
      y: g + row * (cellH + g),
      w: cellW,
      h: cellH,
    };
  });
}
