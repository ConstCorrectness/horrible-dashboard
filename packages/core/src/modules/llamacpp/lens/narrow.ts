/**
 * Choosing which layers and positions the lens grid should ask for.
 *
 * `getLensGrid` has always accepted `layers` and `positions`, and `LensSection`
 * has always passed neither. On a small model that is fine. On a deep one it is
 * not: the grid is a `<table>` with one row per layer and one column per token, so
 * a 262-layer model over a 200-token prompt is 52,400 cells — a request that takes
 * seconds, a table that scrolls forever, and a picture in which nothing can be
 * found. The narrowing existed on the wire the whole time.
 *
 * ## Why a stride rather than a window
 *
 * The interesting thing about a logit lens is *where along the depth* a prediction
 * settles. A window over layers 0-32 of a 262-layer model answers a different
 * question — it shows a sixth of the model in full detail and hides where the
 * answer actually appeared. A stride keeps the full span and samples it, so the
 * knee in the curve is still visible; you then narrow to a window once you can see
 * roughly where to look. Both are offered, and the stride is the default.
 *
 * The **last layer is always kept**. It is the model's actual output, and a stride
 * that happened to skip it would draw a lens whose final row is a prediction the
 * model never made.
 */

/** Rows past this and the table stops being readable before it stops being fast. */
export const DEFAULT_MAX_ROWS = 48;

/**
 * Every `step`-th layer of `all`, always including the first and the last.
 *
 * Returns `[]` when nothing needs narrowing — an empty `layers` means "all of
 * them" to `getLensGrid`, so the common case sends no parameter at all rather
 * than a list naming every layer.
 */
export function strideLayers(all: number[], step: number): number[] {
  if (step <= 1 || all.length === 0) return [];
  const kept: number[] = [];
  for (let i = 0; i < all.length; i += step) kept.push(all[i]);
  const last = all[all.length - 1];
  if (kept[kept.length - 1] !== last) kept.push(last);
  return kept;
}

/**
 * The stride that brings `count` layers under `maxRows`.
 *
 * Used to pick a *default* for a model deep enough to need one, so opening the
 * lens on a 262-layer trace shows something readable rather than something that
 * has to be fixed before it can be looked at.
 */
export function suggestedStride(count: number, maxRows = DEFAULT_MAX_ROWS): number {
  if (count <= maxRows || maxRows <= 0) return 1;
  return Math.ceil(count / maxRows);
}

/** An inclusive range, clamped to what exists. `[]` when it covers everything. */
export function rangeOf(all: number[], from: number, to: number): number[] {
  if (all.length === 0) return [];
  const lo = Math.min(from, to);
  const hi = Math.max(from, to);
  const kept = all.filter((v) => v >= lo && v <= hi);
  return kept.length === all.length ? [] : kept;
}

/**
 * The layers to request, from a range and a stride together.
 *
 * The order matters and is easy to get backwards: the range is applied **first**,
 * then the stride samples within it. Striding first and then clipping would give a
 * different number of rows depending on where the window sat, so widening the
 * window could make the picture coarser.
 */
export function narrowLayers(
  all: number[],
  options: { from?: number; to?: number; step?: number } = {},
): number[] {
  const { from, to, step = 1 } = options;
  const windowed = from === undefined || to === undefined ? all : rangeOf(all, from, to);
  const base = windowed.length === 0 ? all : windowed;
  const strided = strideLayers(base, step);
  const chosen = strided.length === 0 ? base : strided;
  return chosen.length === all.length ? [] : chosen;
}
