/**
 * The 2D layout of one layer's MLP neurons: a near-square grid filled row-major.
 *
 * `cols = ceil(sqrt(ffn))` and `rows = ceil(ffn / cols)`, so the last row may be
 * partial (Qwen3-4B's ffn 9728 is 99×99 with 73 cells of the last row used). Cells
 * past `ffn - 1` do not exist: `indexOf` returns null there and `adjacent` never
 * lands on one.
 */

export interface Grid {
  /** Neurons per layer (the MLP intermediate size). */
  ffn: number;
  cols: number;
  rows: number;
}

export interface Cell {
  row: number;
  col: number;
}

export function gridOf(ffn: number): Grid {
  if (!Number.isInteger(ffn) || ffn < 1)
    throw new Error(`gridOf: ffn must be a positive integer, got ${ffn}`);
  const cols = Math.ceil(Math.sqrt(ffn));
  return { ffn, cols, rows: Math.ceil(ffn / cols) };
}

export function cellOf(index: number, grid: Grid): Cell {
  return { row: Math.floor(index / grid.cols), col: index % grid.cols };
}

/** The neuron at a cell, or null when the cell is off the grid or past `ffn - 1`. */
export function indexOf(row: number, col: number, grid: Grid): number | null {
  if (!Number.isInteger(row) || !Number.isInteger(col)) return null;
  if (row < 0 || col < 0 || row >= grid.rows || col >= grid.cols) return null;
  const index = row * grid.cols + col;
  return index < grid.ffn ? index : null;
}

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));

/**
 * Move `dx` columns and `dy` rows from `index`, clamped to the grid's edges. Stepping
 * into the empty tail of a partial last row lands on the last neuron instead.
 */
export function adjacent(index: number, dx: number, dy: number, grid: Grid): number {
  const { row, col } = cellOf(clamp(index, 0, grid.ffn - 1), grid);
  const r = clamp(row + dy, 0, grid.rows - 1);
  const c = clamp(col + dx, 0, grid.cols - 1);
  return Math.min(r * grid.cols + c, grid.ffn - 1);
}
