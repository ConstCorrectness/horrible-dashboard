/**
 * Shared store for the file explorer. Holds the whole tree model — workspace
 * roots, per-directory expansion, the loaded-children cache, multi-selection, and
 * the inline-rename target — so the view is a thin consumer and the state survives
 * a dockview remount (inactive panes unmount). The live file-watch `files` channel
 * (initFilesWatch) re-lists expanded directories on disk changes. See
 * docs/modules/file-explorer.md.
 */
import { subscribeChannel } from '../../ws';
import {
  gitStatus,
  listDir,
  listRoots,
  parentDir,
  type FileEntry,
  type GitStatusKind,
  type RootInfo,
} from './api';

export interface Selection {
  path: string;
  kind: 'file' | 'dir';
}

// --- reactive core (useSyncExternalStore) ---
let version = 0;
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

// --- tree model ---
let roots: RootInfo[] = [];
let rootsError: string | null = null;
const expanded = new Set<string>();
const children = new Map<string, FileEntry[]>();
const loading = new Set<string>();
/** Remembers each known path's kind, for selection/command lookups by path. */
const kindOf = new Map<string, 'file' | 'dir'>();

export function getRoots(): RootInfo[] {
  return roots;
}
export function getRootsError(): string | null {
  return rootsError;
}
export function isExpanded(path: string): boolean {
  return expanded.has(path);
}
export function isLoading(path: string): boolean {
  return loading.has(path);
}
export function kindFor(path: string): 'file' | 'dir' {
  return kindOf.get(path) ?? 'file';
}

async function loadDir(path: string): Promise<void> {
  loading.add(path);
  emit();
  try {
    const r = await listDir(path);
    children.set(path, r.entries);
    for (const e of r.entries) kindOf.set(e.path, e.kind);
  } catch {
    if (!children.has(path)) children.set(path, []);
  } finally {
    loading.delete(path);
    emit();
  }
}

export async function loadRoots(): Promise<void> {
  try {
    roots = await listRoots();
    rootsError = null;
    for (const r of roots) kindOf.set(r.path, 'dir');
  } catch (e) {
    rootsError = e instanceof Error ? e.message : String(e);
  }
  emit();
}

export function expand(path: string): void {
  if (expanded.has(path)) return;
  expanded.add(path);
  emit();
  if (!children.has(path)) void loadDir(path);
}

export function collapse(path: string): void {
  if (expanded.delete(path)) emit();
}

export function toggleExpanded(path: string): void {
  if (expanded.has(path)) collapse(path);
  else expand(path);
}

/** Re-list the roots and every expanded directory in place (after a watch event
 * or an explicit refresh). Keeps existing rows visible until the new listing
 * arrives, so there's no collapse/flash. */
export function reloadExpanded(): void {
  void loadRoots();
  for (const dir of expanded) void loadDir(dir);
}

/** Back-compat name used by the CRUD commands + agent tools after a mutation. */
export function refreshTree(): void {
  reloadExpanded();
}

/** Retained for compatibility; the view now re-renders off `filesVersion`. */
export function getTreeRefresh(): number {
  return version;
}

// --- the flattened, render-ready row list ---
export interface Row {
  name: string;
  path: string;
  kind: 'file' | 'dir';
  depth: number;
}

export function visibleRows(): Row[] {
  const out: Row[] = [];
  const walk = (entries: { name: string; path: string; kind: 'file' | 'dir' }[], depth: number) => {
    for (const e of entries) {
      out.push({ name: e.name, path: e.path, kind: e.kind, depth });
      if (e.kind === 'dir' && expanded.has(e.path)) {
        const ch = children.get(e.path);
        if (ch) walk(ch, depth + 1);
      }
    }
  };
  walk(
    roots.map((r) => ({ name: r.name || r.path, path: r.path, kind: 'dir' as const })),
    0,
  );
  return out;
}

// --- selection (multi) ---
let selectedPaths = new Set<string>();
let activePath: string | null = null;
let anchorPath: string | null = null;

export function getSelectedPaths(): Set<string> {
  return selectedPaths;
}
export function getActivePath(): string | null {
  return activePath;
}

/** The active (single) selection, for the CRUD commands. */
export function getSelection(): Selection | null {
  return activePath ? { path: activePath, kind: kindFor(activePath) } : null;
}

export function setSelection(next: Selection | null): void {
  if (!next) {
    selectedPaths = new Set();
    activePath = anchorPath = null;
  } else {
    kindOf.set(next.path, next.kind);
    selectedPaths = new Set([next.path]);
    activePath = anchorPath = next.path;
  }
  emit();
}

export function selectSingle(path: string): void {
  selectedPaths = new Set([path]);
  activePath = anchorPath = path;
  emit();
}

/** Ctrl/Cmd-click: toggle one path in/out of the selection. */
export function toggleSelect(path: string): void {
  const next = new Set(selectedPaths);
  if (next.has(path)) next.delete(path);
  else next.add(path);
  selectedPaths = next;
  activePath = anchorPath = path;
  emit();
}

/** Shift-click: select the contiguous range from the anchor to `path` along the
 * given visible order. */
export function selectRange(path: string, order: string[]): void {
  if (!anchorPath) return selectSingle(path);
  const a = order.indexOf(anchorPath);
  const b = order.indexOf(path);
  if (a < 0 || b < 0) return selectSingle(path);
  const [lo, hi] = a < b ? [a, b] : [b, a];
  selectedPaths = new Set(order.slice(lo, hi + 1));
  activePath = path;
  emit();
}

// --- inline rename ---
let renaming: string | null = null;

export function getRenaming(): string | null {
  return renaming;
}
export function startRename(path: string): void {
  renaming = path;
  emit();
}
export function cancelRename(): void {
  if (renaming !== null) {
    renaming = null;
    emit();
  }
}

// --- reveal a path (expand ancestors + select) ---
let revealTarget: string | null = null;

export function setRevealTarget(path: string | null): void {
  revealTarget = path;
  emit();
}
export function getRevealTarget(): string | null {
  return revealTarget;
}

// --- git decorations ---
// path → status for changed files, plus the set of directories that *contain* a
// change (so folders can show an indicator like VS Code), plus the active branch.
const gitByPath = new Map<string, GitStatusKind>();
const gitDirty = new Set<string>();
let gitBranch: string | null = null;

export function gitStatusFor(path: string): GitStatusKind | undefined {
  return gitByPath.get(path);
}
export function gitDirChanged(path: string): boolean {
  return gitDirty.has(path);
}
export function getGitBranch(): string | null {
  return gitBranch;
}
export function gitChangeCount(): number {
  return gitByPath.size;
}

/** Re-fetch working-tree status for every repo root and rebuild the decoration
 * maps. Called on mount and after watch events (debounced with the re-list). */
export async function reloadGit(): Promise<void> {
  const next = new Map<string, GitStatusKind>();
  const dirty = new Set<string>();
  let branch: string | null = null;
  await Promise.all(
    roots.map(async (r) => {
      try {
        const s = await gitStatus(r.path);
        if (!s.is_repo) return;
        if (branch === null) branch = s.branch;
        for (const e of s.entries) {
          next.set(e.path, e.status);
          // Mark every ancestor directory up to (and including) the root.
          let cur = e.path;
          for (let i = 0; i < 64; i++) {
            const p = parentDir(cur);
            if (p === cur) break;
            dirty.add(p);
            cur = p;
            if (p === r.path) break;
          }
        }
      } catch {
        /* not a repo / git missing — leave it undecorated */
      }
    }),
  );
  gitByPath.clear();
  for (const [k, v] of next) gitByPath.set(k, v);
  gitDirty.clear();
  for (const d of dirty) gitDirty.add(d);
  gitBranch = branch;
  emit();
}

// --- live file-watch channel ---
export interface FileChange {
  type: 'added' | 'modified' | 'deleted';
  path: string;
  parent: string;
}

let watchStarted = false;
let watchDebounce: ReturnType<typeof setTimeout> | null = null;

/**
 * Subscribe to the backend file-watch `files` channel once and re-list the tree
 * when the workspace changes on disk (debounced to coalesce bursts). Re-listing
 * only touches expanded directories and preserves expansion + selection, so this
 * is cheap. Idempotent — safe to call from every FileTree mount.
 */
export function initFilesWatch(): void {
  if (watchStarted) return;
  watchStarted = true;
  subscribeChannel('files', (msg) => {
    if (msg.event !== 'change') return;
    if (watchDebounce) clearTimeout(watchDebounce);
    watchDebounce = setTimeout(() => {
      reloadExpanded();
      void reloadGit();
    }, 120);
  });
}
