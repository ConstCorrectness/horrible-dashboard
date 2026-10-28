/**
 * The Library section's state, outside any component: the catalog, what is
 * installed and what it fits, the worker — and the one download in flight.
 *
 * Module-level like the suite's other stores, so the digest and `interp.library`
 * read the same thing whether the section is open, hidden or never visited — and so
 * a download keeps its progress bar when the user looks at another tool meanwhile.
 *
 * A download is two steps on purpose: `askDownload` puts the choice and its size in
 * front of the user, and only `confirmDownload` sends it, carrying that size as the
 * consent (`confirmBytes`) the backend checks against the Hub.
 */
import { useSyncExternalStore } from 'react';

import { ApiError } from '../../../api';
import {
  deleteArtifact,
  downloadArtifact,
  getArtifacts,
  getCatalog,
  getChoices,
  getWorker,
  installEnv,
  startWorker,
  stopWorker,
  workerOp,
  type ArtifactsReply,
  type CatalogEntry,
  type Choice,
  type WorkerInfo,
  type WorkerReply,
} from './api';

export interface ChoiceList {
  loading: boolean;
  choices: Choice[];
  error: string | null;
}

export interface LibraryState {
  catalog: CatalogEntry[];
  library: ArtifactsReply | null;
  worker: WorkerInfo | null;
  /** Per catalog entry, its choices once browsed. */
  choices: Record<string, ChoiceList>;
  /** Shown, not yet confirmed. */
  pending: { entryId: string; choice: string; bytes: number; files: number } | null;
  download: {
    entryId: string;
    choice: string;
    total: number;
    completed: number;
    file: string;
    error: string | null;
    done: boolean;
  } | null;
  /** The worker op in flight, and the last reply — the three-state verdict, shown. */
  busy: string | null;
  lastReply: { op: string; reply: WorkerReply } | null;
  error: string | null;
}

let state: LibraryState = {
  catalog: [],
  library: null,
  worker: null,
  choices: {},
  pending: null,
  download: null,
  busy: null,
  lastReply: null,
  error: null,
};
const listeners = new Set<() => void>();

function set(patch: Partial<LibraryState>): void {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export const libraryStore = {
  get: (): LibraryState => state,
  subscribe(l: () => void): () => void {
    listeners.add(l);
    return () => listeners.delete(l);
  },
};

export function useLibrary(): LibraryState {
  return useSyncExternalStore(libraryStore.subscribe, libraryStore.get);
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// ── reads ────────────────────────────────────────────────────────────────────

export async function refreshLibrary(): Promise<void> {
  const [catalog, library, worker] = await Promise.allSettled([
    state.catalog.length ? Promise.resolve({ entries: state.catalog }) : getCatalog(),
    getArtifacts(),
    getWorker(),
  ]);
  set({
    catalog: catalog.status === 'fulfilled' ? catalog.value.entries : state.catalog,
    library: library.status === 'fulfilled' ? library.value : state.library,
    worker: worker.status === 'fulfilled' ? worker.value : state.worker,
    error:
      [catalog, library, worker]
        .filter((r): r is PromiseRejectedResult => r.status === 'rejected')
        .map((r) => message(r.reason))[0] ?? null,
  });
  if (state.worker?.env.state === 'installing') schedulePoll();
}

export async function browse(entryId: string): Promise<void> {
  set({ choices: { ...state.choices, [entryId]: { loading: true, choices: [], error: null } } });
  try {
    const reply = await getChoices(entryId);
    set({
      choices: {
        ...state.choices,
        [entryId]: { loading: false, choices: reply.choices, error: reply.error ?? null },
      },
    });
  } catch (err) {
    set({
      choices: {
        ...state.choices,
        [entryId]: { loading: false, choices: [], error: message(err) },
      },
    });
  }
}

// ── download: shown, then confirmed ──────────────────────────────────────────

export function askDownload(entryId: string, choice: Choice): void {
  set({
    pending: { entryId, choice: choice.id, bytes: choice.bytes, files: choice.files.length },
  });
}

export function cancelDownload(): void {
  set({ pending: null });
}

export async function confirmDownload(): Promise<void> {
  const pending = state.pending;
  if (!pending || (state.download && !state.download.done && !state.download.error)) return;
  set({
    pending: null,
    download: {
      entryId: pending.entryId,
      choice: pending.choice,
      total: pending.bytes,
      completed: 0,
      file: '',
      error: null,
      done: false,
    },
  });
  const patch = (p: Partial<NonNullable<LibraryState['download']>>) => {
    if (state.download) set({ download: { ...state.download, ...p } });
  };
  try {
    await downloadArtifact(pending.entryId, pending.choice, pending.bytes, (p) => {
      if (p.error) patch({ error: p.error });
      else if (p.status === 'done') patch({ done: true, completed: state.download?.total ?? 0 });
      else
        patch({ completed: p.completed ?? 0, total: p.total ?? pending.bytes, file: p.file ?? '' });
    });
  } catch (err) {
    patch({
      error:
        err instanceof ApiError && err.status === 409
          ? 'The size on the Hub changed since it was shown. Browse again and confirm the new size.'
          : message(err),
    });
  }
  await Promise.all([refreshLibrary(), browse(pending.entryId)]);
}

export async function removeArtifact(id: string): Promise<void> {
  try {
    await deleteArtifact(id);
  } catch (err) {
    set({ error: message(err) });
  }
  await refreshLibrary();
}

// ── the worker ───────────────────────────────────────────────────────────────

let pollTimer: ReturnType<typeof setTimeout> | null = null;

/** Follow an env install until it settles — bounded by the install, not a heartbeat. */
function schedulePoll(): void {
  if (pollTimer) return;
  pollTimer = setTimeout(() => {
    pollTimer = null;
    void refreshLibrary();
  }, 1500);
}

export async function installGroup(group: string): Promise<void> {
  try {
    await installEnv(group);
  } catch (err) {
    set({ error: message(err) });
  }
  await refreshLibrary();
}

async function run<T>(op: string, fn: () => Promise<T>): Promise<T | null> {
  if (state.busy) return null;
  set({ busy: op, error: null });
  try {
    return await fn();
  } catch (err) {
    set({ error: message(err) });
    return null;
  } finally {
    set({ busy: null });
    await refreshLibrary();
  }
}

export function start(): Promise<unknown> {
  return run('start', async () => {
    const reply = await startWorker();
    if (!reply.ok) set({ error: reply.error ?? 'the worker did not start' });
    return reply;
  });
}

export function stop(): Promise<unknown> {
  return run('stop', stopWorker);
}

/** Run one worker op and keep its verdict for the section to show. */
export function op(name: string, args: Record<string, unknown> = {}): Promise<WorkerReply | null> {
  return run(name, async () => {
    const reply = await workerOp(name, args);
    set({ lastReply: { op: name, reply } });
    return reply;
  });
}

// ── the digest ───────────────────────────────────────────────────────────────

/** A line for the suite digest: what is installed, what fits, what the worker holds. */
export function libraryDigest(current: LibraryState = state): string | null {
  const parts: string[] = [];
  const lib = current.library;
  if (lib) {
    const fits = lib.artifacts.filter((a) => a.compat.some((c) => c.state === 'compatible'));
    parts.push(
      lib.artifacts.length
        ? `${lib.artifacts.length} artifact${lib.artifacts.length === 1 ? '' : 's'} (${fits.length} fit a loaded model)`
        : 'no artifacts',
    );
  }
  const w = current.worker;
  if (w) {
    const loaded = w.status?.loaded;
    parts.push(
      w.state === 'ready'
        ? `worker ready${loaded ? `, holding ${loaded.repo}` : ', no model'}`
        : `worker ${w.state.replace('_', ' ')}`,
    );
  }
  return parts.length ? parts.join('; ') : null;
}
