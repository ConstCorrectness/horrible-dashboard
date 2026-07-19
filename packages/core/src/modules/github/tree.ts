/**
 * Turning GitHub's **flat** tree response into the nested, expandable rows the viewer
 * renders.
 *
 * `git/trees?recursive=1` returns every path in the repo as a flat list
 * (`src`, `src/lib`, `src/lib/app.py`) with no nesting and no guaranteed order. The
 * viewer needs "the visible rows, given which folders are open", which is the same
 * shape the file explorer's store produces — so the row type matches
 * `modules/files/store.ts` deliberately, and both feed the same kind of virtualized
 * list.
 *
 * Doing this client-side is what makes expanding a folder instant: the whole tree
 * arrived in one request, so opening a node is a re-filter, not a fetch.
 */
import type { TreeEntry } from './api';

export interface TreeRow {
  name: string;
  path: string;
  kind: 'file' | 'dir';
  depth: number;
}

/** Children of each directory path (`''` is the repo root), sorted dirs-first. */
export type TreeIndex = Map<string, TreeEntry[]>;

function parentOf(path: string): string {
  const idx = path.lastIndexOf('/');
  return idx < 0 ? '' : path.slice(0, idx);
}

export function basename(path: string): string {
  const idx = path.lastIndexOf('/');
  return idx < 0 ? path : path.slice(idx + 1);
}

/**
 * Group a flat entry list by parent directory.
 *
 * Directories are synthesized when missing: the `contents` fallback returns only one
 * level, and a `tree` response for a sparse repo can list `a/b/c.py` without ever
 * naming `a/b`. Without this, such a file would be unreachable in the UI.
 */
export function buildIndex(entries: TreeEntry[]): TreeIndex {
  const byPath = new Map<string, TreeEntry>();
  for (const entry of entries) byPath.set(entry.path, entry);

  for (const entry of entries) {
    let parent = parentOf(entry.path);
    while (parent && !byPath.has(parent)) {
      byPath.set(parent, { path: parent, kind: 'dir', size: null });
      parent = parentOf(parent);
    }
  }

  const index: TreeIndex = new Map();
  for (const entry of byPath.values()) {
    const parent = parentOf(entry.path);
    const siblings = index.get(parent);
    if (siblings) siblings.push(entry);
    else index.set(parent, [entry]);
  }

  for (const siblings of index.values()) {
    siblings.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === 'dir' ? -1 : 1;
      return basename(a.path).localeCompare(basename(b.path));
    });
  }
  return index;
}

/** The flattened rows to render, given which directories are expanded. */
export function visibleRows(index: TreeIndex, expanded: ReadonlySet<string>): TreeRow[] {
  const rows: TreeRow[] = [];

  const walk = (dir: string, depth: number) => {
    for (const entry of index.get(dir) ?? []) {
      rows.push({ name: basename(entry.path), path: entry.path, kind: entry.kind, depth });
      if (entry.kind === 'dir' && expanded.has(entry.path)) walk(entry.path, depth + 1);
    }
  };

  walk('', 0);
  return rows;
}

/** Every ancestor directory of a path, outermost first. */
export function ancestorsOf(path: string): string[] {
  const parts = path.split('/');
  parts.pop();
  const out: string[] = [];
  let current = '';
  for (const part of parts) {
    current = current ? `${current}/${part}` : part;
    out.push(current);
  }
  return out;
}
