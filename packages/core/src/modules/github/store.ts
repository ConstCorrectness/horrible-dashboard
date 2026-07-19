/**
 * Viewer state, held **outside** the component so it survives a pane remount — the
 * same reason `modules/files/store.ts` is shaped this way (inactive document tabs
 * unmount, and re-fetching a whole repo tree on every tab switch would be brutal).
 *
 * Keyed per pane instance, because two `github.repo` panes are two independent
 * repositories. A pane's state is dropped when it closes.
 */
import {
  getTree,
  listBranches,
  listContents,
  splitRepo,
  type RepoSummary,
  type TreeEntry,
} from './api';
import { ancestorsOf, buildIndex, visibleRows, type TreeIndex, type TreeRow } from './tree';

export interface ViewerState {
  repo: RepoSummary | null;
  ref: string;
  branches: string[];
  index: TreeIndex;
  expanded: Set<string>;
  /** The repo was too large for a single tree fetch; directories load on demand. */
  lazy: boolean;
  loading: boolean;
  error: string | null;
  activePath: string | null;
}

function blank(): ViewerState {
  return {
    repo: null,
    ref: '',
    branches: [],
    index: new Map(),
    expanded: new Set(),
    lazy: false,
    loading: false,
    error: null,
    activePath: null,
  };
}

const states = new Map<string, ViewerState>();
const listeners = new Set<() => void>();
let version = 0;

function emit(): void {
  version += 1;
  for (const listener of listeners) listener();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function storeVersion(): number {
  return version;
}

export function getState(paneId: string): ViewerState {
  let state = states.get(paneId);
  if (!state) {
    state = blank();
    states.set(paneId, state);
  }
  return state;
}

export function disposeState(paneId: string): void {
  states.delete(paneId);
}

function patch(paneId: string, changes: Partial<ViewerState>): void {
  states.set(paneId, { ...getState(paneId), ...changes });
  emit();
}

export function rowsFor(paneId: string): TreeRow[] {
  const state = getState(paneId);
  return visibleRows(state.index, state.expanded);
}

/** Open a repository at a ref (defaults to its default branch). */
export async function openRepo(paneId: string, repo: RepoSummary, ref?: string): Promise<void> {
  const parts = splitRepo(repo.full_name);
  if (!parts) return;
  const targetRef = ref || repo.default_branch || 'main';

  patch(paneId, {
    repo,
    ref: targetRef,
    index: new Map(),
    // A ref switch invalidates every path, so expansion state can't carry over.
    expanded: new Set(),
    activePath: null,
    loading: true,
    error: null,
    lazy: false,
  });

  try {
    const [tree, branches] = await Promise.all([
      getTree(parts.owner, parts.repo, targetRef),
      listBranches(parts.owner, parts.repo).catch(() => [] as string[]),
    ]);
    patch(paneId, {
      index: buildIndex(tree.entries),
      branches,
      lazy: tree.truncated,
      loading: false,
    });
    // A truncated tree gave us nothing usable at the root — fetch that one level.
    if (tree.truncated) await loadDir(paneId, '');
  } catch (err) {
    patch(paneId, {
      loading: false,
      error: err instanceof Error ? err.message : String(err),
    });
  }
}

/** Fetch one directory. Only used in lazy mode; a whole-tree repo already has it. */
async function loadDir(paneId: string, dir: string): Promise<void> {
  const state = getState(paneId);
  const parts = state.repo && splitRepo(state.repo.full_name);
  if (!parts) return;
  try {
    const listing = await listContents(parts.owner, parts.repo, dir, state.ref);
    const merged: TreeEntry[] = [
      ...[...getState(paneId).index.values()].flat(),
      ...listing.entries,
    ];
    patch(paneId, { index: buildIndex(merged) });
  } catch (err) {
    patch(paneId, { error: err instanceof Error ? err.message : String(err) });
  }
}

export function toggleExpanded(paneId: string, path: string): void {
  const state = getState(paneId);
  const expanded = new Set(state.expanded);
  if (expanded.has(path)) {
    expanded.delete(path);
  } else {
    expanded.add(path);
    // In lazy mode the children may not have arrived yet.
    if (state.lazy && !state.index.has(path)) void loadDir(paneId, path);
  }
  patch(paneId, { expanded });
}

export function setActivePath(paneId: string, path: string | null): void {
  patch(paneId, { activePath: path });
}

/** Expand a path's ancestors so it's visible, and select it. */
export function reveal(paneId: string, path: string): void {
  const expanded = new Set(getState(paneId).expanded);
  for (const dir of ancestorsOf(path)) expanded.add(dir);
  patch(paneId, { expanded, activePath: path });
}

export function switchRef(paneId: string, ref: string): void {
  const state = getState(paneId);
  if (state.repo) void openRepo(paneId, state.repo, ref);
}

export function clearRepo(paneId: string): void {
  patch(paneId, blank());
}
