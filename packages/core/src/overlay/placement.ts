/**
 * Where to put a floating layer (context menu, documentation popup) so it stays
 * inside the window.
 *
 * Pure and unit-tested: every call site that has ever got this wrong got it wrong
 * by *estimating* — `left: clientX` with no clamp at all (the activity rail), or a
 * clamp against hardcoded guesses at the menu's size (`clientX - 200`, `clientY -
 * 260` in the file tree, which is right only while that menu has exactly the items
 * it had the day it was written). The fix is to take the real measured size as an
 * argument and keep the arithmetic in one place.
 *
 * The caller renders once to measure, then positions — see `useAnchoredPosition`
 * in packages/ui. Nothing here touches the DOM.
 */

/** A rect in viewport coordinates. A cursor position is a zero-size one. */
export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Viewport {
  width: number;
  height: number;
}

/** Which side of the anchor the layer is placed on. */
export type Side = 'top' | 'bottom' | 'left' | 'right';

/** How the layer lines up along the anchor's cross axis. */
export type Align = 'start' | 'center' | 'end';

export interface PlacementRequest {
  /** The thing being placed against: a cursor point, a button, a hovered token. */
  anchor: Rect;
  /** The layer's measured size. Estimates are what this module exists to avoid. */
  content: { width: number; height: number };
  viewport: Viewport;
  /** Preferred side; flipped only when the layer does not fit there. */
  side?: Side;
  align?: Align;
  /** Gap between anchor and layer, in px. Context menus want 0, popovers ~6. */
  offset?: number;
  /** Keep this much clear of every window edge. */
  padding?: number;
  /**
   * Whether the layer may shrink to fit. A menu can scroll; a tooltip would rather
   * flip than become 40px tall. When false the layer keeps its size and is clamped
   * to the edge instead, so it may overhang a viewport smaller than it is.
   */
  shrink?: boolean;
}

export interface Placement {
  left: number;
  top: number;
  /** The side actually used — `bottom` requested may come back `top`. */
  side: Side;
  /** Present only when `shrink` forced a limit; apply as CSS `max-height`. */
  maxHeight?: number;
  maxWidth?: number;
}

const isVertical = (side: Side): boolean => side === 'top' || side === 'bottom';

/** Space between the anchor and a given window edge, less the padding. */
function spaceOn(side: Side, anchor: Rect, viewport: Viewport, padding: number): number {
  switch (side) {
    case 'top':
      return anchor.y - padding;
    case 'bottom':
      return viewport.height - (anchor.y + anchor.height) - padding;
    case 'left':
      return anchor.x - padding;
    case 'right':
      return viewport.width - (anchor.x + anchor.width) - padding;
  }
}

const OPPOSITE: Record<Side, Side> = {
  top: 'bottom',
  bottom: 'top',
  left: 'right',
  right: 'left',
};

/**
 * Choose a side: the preferred one if the layer fits, else its opposite if *that*
 * fits, else whichever of the two has more room.
 *
 * Falling back to "more room" rather than to the preference matters for the case
 * that motivated this — a right-click near the bottom of the window, where neither
 * side fits a long menu. Keeping the preference there puts the menu's items off
 * the bottom edge where they cannot be clicked at all; the roomier side at least
 * shows most of them, and `shrink` then makes the rest reachable by scrolling.
 */
function chooseSide(req: Required<Pick<PlacementRequest, 'padding'>> & PlacementRequest): Side {
  const { anchor, content, viewport, padding } = req;
  const side = req.side ?? 'bottom';
  const offset = req.offset ?? 0;
  const need = (isVertical(side) ? content.height : content.width) + offset;

  const primary = spaceOn(side, anchor, viewport, padding);
  if (primary >= need) return side;
  const flipped = OPPOSITE[side];
  const secondary = spaceOn(flipped, anchor, viewport, padding);
  if (secondary >= need) return flipped;
  return secondary > primary ? flipped : side;
}

/** Clamp `value` into `[min, max]`, tolerating an inverted range (min wins). */
function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(value, max));
}

/** The cross-axis (start/center/end) coordinate before clamping. */
function alignOn(align: Align, anchorStart: number, anchorSize: number, size: number): number {
  if (align === 'center') return anchorStart + anchorSize / 2 - size / 2;
  if (align === 'end') return anchorStart + anchorSize - size;
  return anchorStart;
}

/**
 * Place a layer of `content` size against `anchor` inside `viewport`.
 *
 * Guarantees, in order: the layer never starts off-screen; it is clamped to the
 * padded viewport on both axes; and with `shrink` it is given a max size rather
 * than being allowed to overflow. A layer wider than the viewport pins to the
 * padded left edge — a partly-visible menu you can scroll beats one positioned at
 * a negative coordinate.
 *
 * `shrink` caps the layer to the space on the side it was placed on, not to the
 * whole window: a 1200px menu opened 400px down an 800px window gets the 390px
 * below the cursor, not a jump to the top of the screen. Growing it past its own
 * anchor would move the menu away from the thing it belongs to, which is worse
 * than scrolling — the anchor is the only cue about what the menu acts on.
 */
export function placeLayer(req: PlacementRequest): Placement {
  const padding = req.padding ?? 4;
  const offset = req.offset ?? 0;
  const align = req.align ?? 'start';
  const { anchor, content, viewport } = req;
  const side = chooseSide({ ...req, padding });

  const available = spaceOn(side, anchor, viewport, padding) - offset;
  const shrink = req.shrink ?? false;

  let left: number;
  let top: number;
  let maxHeight: number | undefined;
  let maxWidth: number | undefined;

  if (isVertical(side)) {
    const height = shrink ? Math.min(content.height, Math.max(available, 0)) : content.height;
    if (shrink && height < content.height) maxHeight = Math.max(height, 0);
    top = side === 'top' ? anchor.y - offset - height : anchor.y + anchor.height + offset;
    left = alignOn(align, anchor.x, anchor.width, content.width);
  } else {
    const width = shrink ? Math.min(content.width, Math.max(available, 0)) : content.width;
    if (shrink && width < content.width) maxWidth = Math.max(width, 0);
    left = side === 'left' ? anchor.x - offset - width : anchor.x + anchor.width + offset;
    top = alignOn(align, anchor.y, anchor.height, content.height);
  }

  // Clamp on both axes. The max bound is computed before the min so that a layer
  // taller or wider than the viewport lands at `padding` rather than at a negative
  // coordinate — `clamp` resolves the inverted range in favour of the minimum.
  left = clamp(left, padding, viewport.width - content.width - padding);
  const usedHeight = maxHeight ?? content.height;
  top = clamp(top, padding, viewport.height - usedHeight - padding);

  // A layer that still overflows the bottom (taller than the viewport, or an
  // unshrinkable one) gets a height cap so its tail stays reachable by scrolling
  // instead of being drawn past the edge.
  const overflow = top + usedHeight + padding - viewport.height;
  if (shrink && overflow > 0) maxHeight = Math.max(usedHeight - overflow, 0);

  return {
    left: Math.round(left),
    top: Math.round(top),
    side,
    ...(maxHeight !== undefined ? { maxHeight: Math.round(maxHeight) } : {}),
    ...(maxWidth !== undefined ? { maxWidth: Math.round(maxWidth) } : {}),
  };
}
