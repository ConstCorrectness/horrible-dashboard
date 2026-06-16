/**
 * Small shared store for the file tree: the current selection (so commands like
 * `files.rename` know their target), a refresh counter (re-list after a mutation,
 * standing in for live watch events until B1b), and a reveal target (expand the
 * tree to a path, e.g. `files.revealActiveBuffer`).
 */
export interface Selection {
  path: string;
  kind: 'file' | 'dir';
}

let version = 0;
let selection: Selection | null = null;
let treeRefresh = 0;
let revealTarget: string | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  version += 1;
  for (const l of listeners) l();
}

export function subscribeFiles(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function filesVersion(): number {
  return version;
}

export function setSelection(next: Selection | null): void {
  selection = next;
  emit();
}

export function getSelection(): Selection | null {
  return selection;
}

/** Bump to force every expanded directory to re-list (after a mutation). */
export function refreshTree(): void {
  treeRefresh += 1;
  emit();
}

export function getTreeRefresh(): number {
  return treeRefresh;
}

/** Ask the tree to expand to and select a path. */
export function setRevealTarget(path: string | null): void {
  revealTarget = path;
  emit();
}

export function getRevealTarget(): string | null {
  return revealTarget;
}
