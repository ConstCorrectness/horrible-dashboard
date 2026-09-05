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

/**
 * Every snap zone, as data — the one list, and the only place a new zone is added.
 *
 * It used to be spelled out three more times: a `Set` in `serialize.ts`, an array in
 * `window-placement.ts` and another in the agent's `tool-exec.ts`. Each is a *filter*
 * that drops anything it does not recognize, so a zone added to the type and to
 * `rectForZone` but missed in one of them fails in the quietest possible way — the
 * snap works, and then the window comes back somewhere else after a reload, or the
 * agent is told a zone it can see in the docs is not a zone.
 */
export const SNAP_ZONES: readonly SnapZone[] = [
  'left',
  'right',
  'top',
  'bottom',
  'tl',
  'tr',
  'bl',
  'br',
  'center',
  'third-l',
  'third-c',
  'third-r',
  'max',
];

/** Narrow an untrusted string to a zone. Never guesses. */
export function isSnapZone(value: unknown): value is SnapZone {
  return typeof value === 'string' && (SNAP_ZONES as readonly string[]).includes(value);
}

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
 *
 * This returns edges, corners and `max` only — never `center` or a `third-*`. Those
 * are keyboard and palette zones. There is no unambiguous pointer gesture for "the
 * middle third" that does not also redefine what dragging near an edge means, and the
 * gesture below is tuned; a zone reachable only deliberately is better than an edge
 * drag that sometimes yields a third.
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
 * The rect a zone occupies. Halves, quarters and thirds tile the surface exactly (the
 * two halves of an odd width differ by a pixel rather than overlapping or leaving a
 * seam; the last third absorbs whatever the first two floored away).
 *
 * `center` does not tile — it is a centred box, deliberately inset, for the one window
 * you want to read rather than arrange.
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
  // Thirds, same rule one step further: the first two floor, the last takes the
  // remainder, so the three always sum to `fullW` exactly at any width.
  const thirdW = Math.floor((fullW - g * 2) / 3);
  const lastThirdW = fullW - thirdW * 2 - g * 2;
  const third2X = x0 + thirdW + g;
  const third3X = third2X + thirdW + g;

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
    case 'third-l':
      return { x: x0, y: y0, w: thirdW, h: fullH };
    case 'third-c':
      return { x: third2X, y: y0, w: thirdW, h: fullH };
    case 'third-r':
      return { x: third3X, y: y0, w: lastThirdW, h: fullH };
    case 'center': {
      // Two thirds of each axis, centred. Clamped so that on a surface smaller than
      // twice the minimum this still yields a usable window rather than a sliver:
      // `center` is the "read this one" zone, and a zone that can produce something
      // undraggable is worse than one that just stops shrinking.
      const w = Math.min(fullW, Math.max(MIN_WINDOW_SIZE.w, Math.round(fullW * (2 / 3))));
      const h = Math.min(fullH, Math.max(MIN_WINDOW_SIZE.h, Math.round(fullH * (2 / 3))));
      return {
        x: x0 + Math.max(0, Math.round((fullW - w) / 2)),
        y: y0 + Math.max(0, Math.round((fullH - h) / 2)),
        w,
        h,
      };
    }
  }
}

/**
 * The zone that fills the rest of the surface when `zone` is taken, or null.
 *
 * Halves only, and deliberately so. A quarter leaves an L-shaped remainder that no
 * single zone describes; a third leaves two; `center` leaves a frame; `max` leaves
 * nothing. Returning a "closest" zone for those would move a second window somewhere
 * the user did not point at, which is worse than not moving it — the whole value of
 * the fill is that the result is the split you were already looking at.
 */
export function complementZone(zone: SnapZone): SnapZone | null {
  switch (zone) {
    case 'left':
      return 'right';
    case 'right':
      return 'left';
    case 'top':
      return 'bottom';
    case 'bottom':
      return 'top';
    default:
      return null;
  }
}

/** How much of `a` lies inside `b`, as a fraction of `a`'s area (0 when either is empty). */
export function overlapFraction(a: WindowRect, b: WindowRect): number {
  const area = a.w * a.h;
  if (area <= 0) return 0;
  const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  if (w <= 0 || h <= 0) return 0;
  return (w * h) / area;
}

/**
 * The window snap-assist would move into `complementZone(zone)`, or null.
 *
 * Lives here, beside `rectForZone`, for the same reason everything else in this file
 * does: the drag preview draws the second rect only when a fill will actually happen,
 * and the reducer decides whether one does. Two copies of "is there a candidate"
 * would drift into a preview that promises a split and a drop that delivers one
 * window on top of another.
 */
export function fillTarget(
  windows: readonly WindowState[],
  excludeId: string,
  zone: SnapZone,
  viewport: Size,
  cfg: SnapConfig = DEFAULT_SNAP,
): WindowState | null {
  const other = complementZone(zone);
  if (!other) return null;
  const taken = rectForZone(zone, viewport, cfg);
  return (
    windows
      .filter((w) => w.id !== excludeId && w.mode !== 'minimized')
      // Already in the complement: nothing to do, and re-snapping it would
      // overwrite the `restoreRect` it holds for its own un-snap.
      .filter((w) => w.snap !== other)
      // Covering the half just taken — maximized, snapped to the same zone, or
      // simply overlapping it enough that it is now hidden behind the new window.
      .filter(
        (w) => w.mode === 'maximized' || w.snap === zone || overlapFraction(w.rect, taken) >= 0.5,
      )
      .sort((a, b) => b.z - a.z)[0] ?? null
  );
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
