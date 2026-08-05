/**
 * File-tree actions that are not the view's own business.
 *
 * `deleteSelection` used to be a closure inside `FileTree`, which was fine while
 * the only caller was the menu that component also rendered. The context menu is
 * now assembled from providers declared in the module manifest, so an action has
 * to be reachable without a component instance — and since the selection has
 * always lived in the store rather than in React state, nothing here needed the
 * component in the first place.
 */
import { dialogs } from '../../dialogs';
import { deleteEntry } from './api';
import { getActivePath, getSelectedPaths, kindFor, refreshTree, setSelection } from './store';

/** The paths a selection-wide action applies to: the selection, else the active row. */
export function selectionPaths(): string[] {
  const selected = getSelectedPaths();
  if (selected.size) return [...selected];
  const active = getActivePath();
  return active ? [active] : [];
}

/** Confirm, then delete every selected entry. No-op on an empty selection. */
export async function deleteSelection(): Promise<void> {
  const paths = selectionPaths();
  if (paths.length === 0) return;
  const label = paths.length === 1 ? paths[0] : `${paths.length} items`;
  const ok = await dialogs.confirm({
    title: 'Delete',
    message: `Delete ${label}? This can't be undone.`,
    confirmLabel: 'Delete',
    danger: true,
  });
  if (!ok) return;
  for (const p of paths) {
    try {
      await deleteEntry(p, kindFor(p) === 'dir');
    } catch {
      /* surfaced by the watch re-list; skip */
    }
  }
  setSelection(null);
  refreshTree();
}
