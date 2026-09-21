/**
 * Where Shift+Enter goes next.
 *
 * Jupyter's contract is two keys, not one: `Ctrl/Cmd+Enter` runs the cell and
 * leaves you in it, `Shift+Enter` runs it and **moves on**. Both panes here used
 * to bind the two keys to the same handler, so there was no way to walk a
 * notebook from the keyboard at all — you ran a cell, then reached for the mouse
 * to click into the next one, every time.
 *
 * The decision lives in its own module for one reason: the panes that need it are
 * CodeMirror hosts, which core's vitest cannot mount, so "is this the last cell"
 * would otherwise be untestable logic duplicated in two files. Both panes call
 * this; neither re-derives it.
 */
import type { NotebookCell } from './types';

export type Advance =
  /** Move to the cell already at `index`. */
  | { kind: 'focus'; index: number }
  /**
   * There is no next cell: insert a code one after `afterCellId` and land in it.
   * `index` is where it will be once the insert applies — which is what a caller
   * tracking focus by position needs, because a freshly inserted cell carries a
   * temporary id until the backend answers with the real one.
   */
  | { kind: 'insert'; afterCellId: string; index: number };

/**
 * Running the cell at `index`, where should focus end up?
 *
 * Appending at the end is deliberate and is what Jupyter does: Shift+Enter at the
 * bottom of a notebook is how you keep writing. An `index` that names no cell
 * (an empty notebook, a stale index after a delete) is treated as the end.
 */
export function advanceFrom(cells: NotebookCell[], index: number): Advance {
  if (index + 1 < cells.length) return { kind: 'focus', index: index + 1 };
  const last = cells[cells.length - 1];
  // An empty notebook has nothing to insert *after*; the caller appends.
  return { kind: 'insert', afterCellId: last?.id ?? '', index: cells.length };
}
