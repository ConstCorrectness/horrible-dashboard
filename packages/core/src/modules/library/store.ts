/**
 * Shared store for the Library panel: the current library, the sources catalog, and
 * transient search results. Ingestion status arrives live on the `library` `/ws`
 * channel (see the backend broadcaster) and is upserted by source id, so the list
 * animates queued → … → ready without polling. State lives here (not in the
 * component) so it survives a dockview remount. See docs/modules/library.mdx.
 */
import { subscribeChannel, type WsMessage } from '../../ws';
import {
  deleteSource as apiDeleteSource,
  ingestSource,
  librarySearch,
  listLibraries,
  listSources,
  type IngestRequest,
  type LibraryInfo,
  type SearchGroup,
  type SourceModel,
} from './api';

// --- reactive core (useSyncExternalStore) ---
let version = 0;
const listeners = new Set<() => void>();

function emit(): void {
  version += 1;
  for (const l of listeners) l();
}

export function subscribeLibrary(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function libraryVersion(): number {
  return version;
}

// --- state ---
let currentLibrary = 'default';
const sources = new Map<string, SourceModel>();
let libraries: LibraryInfo[] = [];
let searchResults: SearchGroup[] | null = null;
let searchQuery = '';
let searching = false;
let error: string | null = null;

export function getCurrentLibrary(): string {
  return currentLibrary;
}

export function getLibraries(): LibraryInfo[] {
  return libraries;
}

/** Sources in the current library, newest first. */
export function getSources(): SourceModel[] {
  return [...sources.values()]
    .filter((s) => s.library === currentLibrary)
    .sort((a, b) => (a.added_at < b.added_at ? 1 : -1));
}

export function getSearch(): {
  results: SearchGroup[] | null;
  query: string;
  searching: boolean;
} {
  return { results: searchResults, query: searchQuery, searching };
}

export function getError(): string | null {
  return error;
}

function upsert(source: SourceModel): void {
  sources.set(source.id, source);
}

export async function setCurrentLibrary(name: string): Promise<void> {
  currentLibrary = name;
  searchResults = null;
  emit();
  await refresh();
}

export async function refresh(): Promise<void> {
  try {
    const [{ sources: rows }, { libraries: libs }] = await Promise.all([
      listSources(currentLibrary),
      listLibraries(),
    ]);
    for (const s of rows) upsert(s);
    libraries = libs;
    error = null;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }
  emit();
}

export async function addSource(req: IngestRequest): Promise<void> {
  const source = await ingestSource({ ...req, library: currentLibrary });
  upsert(source); // show it as `queued` immediately; ws will drive its status
  emit();
  void refreshLibraries();
}

export async function removeSource(id: string): Promise<void> {
  await apiDeleteSource(id);
  sources.delete(id);
  emit();
  void refreshLibraries();
}

export async function runSearch(text: string, limit = 8): Promise<void> {
  const query = text.trim();
  if (!query) return;
  searching = true;
  searchQuery = query;
  emit();
  try {
    const res = await librarySearch(currentLibrary, query, limit);
    searchResults = res.groups;
    error = null;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
    searchResults = [];
  } finally {
    searching = false;
    emit();
  }
}

export function clearSearch(): void {
  searchResults = null;
  searchQuery = '';
  emit();
}

async function refreshLibraries(): Promise<void> {
  try {
    libraries = (await listLibraries()).libraries;
    emit();
  } catch {
    /* non-fatal — the list refreshes on the next load */
  }
}

// --- live status channel ---
let watchStarted = false;

/** Subscribe once to the `library` channel; upsert source snapshots as they change.
 * Idempotent — safe to call from every panel mount. */
export function initLibraryWatch(): void {
  if (watchStarted) return;
  watchStarted = true;
  subscribeChannel('library', (msg: WsMessage) => {
    if (msg.event !== 'source') return;
    const source = msg.data as SourceModel | undefined;
    if (!source || !source.id) return;
    upsert(source);
    emit();
  });
}
