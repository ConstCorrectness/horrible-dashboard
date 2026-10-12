/**
 * Shared state for the Knowledge pane: the filtered catalog, the selected item's
 * tree, collections, and live import progress. Lives outside the component so a
 * pane remount (workspace switch) keeps its place.
 *
 * Three `/ws` channels feed it:
 * - `kb` — an item changed server-side (a site import filing a lesson) or was removed;
 * - `library` — a source's ingest status moved, which is an item's `index_status`;
 * - `docviewer` — crawl progress for an import, keyed by doc-set id.
 */
import { subscribeChannel, type WsMessage } from '../../ws';
import {
  getItem,
  getTree,
  listCollections,
  listItems,
  search,
  summary as fetchSummary,
  type Collection,
  type Item,
  type ListFilters,
  type Passage,
  type Summary,
  type TreeNode,
} from './api';

export interface ImportProgress {
  set_id: string;
  status: string;
  captured: number;
  failed: number;
  queued: number;
  current_url?: string | null;
  error?: string | null;
}

let version = 0;
const listeners = new Set<() => void>();

function emit(): void {
  version += 1;
  for (const l of listeners) l();
}

export function subscribeKb(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function kbVersion(): number {
  return version;
}

// --- state ---------------------------------------------------------------------

let filters: ListFilters = {};
let order: string[] = [];
const items = new Map<string, Item>();
let total = 0;
let collections: Collection[] = [];
let stats: Summary | null = null;
let selectedId: string | null = null;
let tree: TreeNode | null = null;
let loading = false;
let error: string | null = null;
const imports = new Map<string, ImportProgress>();
let passages: Passage[] | null = null;
let searchQuery = '';
let searching = false;

export function getFilters(): ListFilters {
  return filters;
}
export function getItems(): Item[] {
  return order.map((id) => items.get(id)).filter((i): i is Item => Boolean(i));
}
export function getTotal(): number {
  return total;
}
export function getCollections(): Collection[] {
  return collections;
}
export function getSummary(): Summary | null {
  return stats;
}
export function getSelected(): Item | null {
  if (!selectedId) return null;
  return items.get(selectedId) ?? (tree?.item.id === selectedId ? tree.item : null);
}
export function getSelectedTree(): TreeNode | null {
  return tree;
}
export function isLoading(): boolean {
  return loading;
}
export function getError(): string | null {
  return error;
}
export function importFor(item: Item | null): ImportProgress | null {
  return item?.docset_id ? (imports.get(item.docset_id) ?? null) : null;
}
export function getSearch(): { passages: Passage[] | null; query: string; searching: boolean } {
  return { passages, query: searchQuery, searching };
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

// --- loading -------------------------------------------------------------------

/** Reload the list, collections and counts. `quiet` skips the loading state: a
 *  background resync after a catalog event should not flash "loading". */
export async function refresh(quiet = false): Promise<void> {
  if (!quiet) {
    loading = true;
    emit();
  }
  try {
    const [list, cols, sum] = await Promise.all([
      listItems(filters),
      listCollections(),
      fetchSummary(),
    ]);
    order = list.items.map((i) => i.id);
    for (const item of list.items) items.set(item.id, item);
    total = list.total;
    collections = cols.collections;
    stats = sum;
    error = null;
  } catch (e) {
    error = message(e);
  } finally {
    loading = false;
    emit();
  }
}

export async function setFilters(next: ListFilters): Promise<void> {
  filters = next;
  await refresh();
}

export async function select(id: string | null): Promise<void> {
  selectedId = id;
  tree = null;
  emit();
  if (!id) return;
  await reloadTree();
}

async function reloadTree(): Promise<void> {
  const id = selectedId;
  if (!id) return;
  try {
    const next = await getTree(id);
    if (selectedId !== id) return;
    tree = next;
    items.set(next.item.id, next.item);
  } catch (e) {
    error = message(e);
  }
  emit();
}

/** Put a freshly-returned item back into the store (after a save or patch). */
export function upsert(item: Item): void {
  const known = items.has(item.id);
  items.set(item.id, item);
  const topLevel = !item.parent_id;
  if (!known && topLevel && !filters.parent_id) order = [item.id, ...order];
  if (tree && inTree(tree, item.id)) replaceInTree(tree, item);
  emit();
}

export function remove(ids: string[]): void {
  for (const id of ids) items.delete(id);
  order = order.filter((id) => !ids.includes(id));
  if (selectedId && ids.includes(selectedId)) {
    selectedId = null;
    tree = null;
  } else if (tree) {
    scheduleTreeReload();
  }
  emit();
}

function inTree(node: TreeNode, id: string): boolean {
  return node.item.id === id || node.children.some((c) => inTree(c, id));
}

function replaceInTree(node: TreeNode, item: Item): void {
  if (node.item.id === item.id) {
    node.item = item;
    return;
  }
  for (const child of node.children) replaceInTree(child, item);
}

// --- search --------------------------------------------------------------------

export async function runSearch(text: string): Promise<void> {
  const q = text.trim();
  if (!q) {
    clearSearch();
    return;
  }
  searching = true;
  searchQuery = q;
  emit();
  try {
    const res = await search({
      query: q,
      collections: filters.collection ? [filters.collection] : undefined,
      limit: 12,
    });
    passages = res.passages;
    error = null;
  } catch (e) {
    error = message(e);
    passages = [];
  } finally {
    searching = false;
    emit();
  }
}

export function clearSearch(): void {
  passages = null;
  searchQuery = '';
  emit();
}

// --- live updates --------------------------------------------------------------

let treeTimer: ReturnType<typeof setTimeout> | null = null;

/** An import files pages in bursts; coalesce them into one tree fetch. */
function scheduleTreeReload(): void {
  if (treeTimer) return;
  treeTimer = setTimeout(() => {
    treeTimer = null;
    void reloadTree();
  }, 400);
}

const pendingSources = new Set<string>();
let sourceTimer: ReturnType<typeof setTimeout> | null = null;

function itemBySource(sourceId: string): Item | null {
  for (const item of items.values()) if (item.source_id === sourceId) return item;
  return null;
}

function scheduleSourceRefresh(sourceId: string): void {
  pendingSources.add(sourceId);
  if (sourceTimer) return;
  sourceTimer = setTimeout(() => {
    sourceTimer = null;
    const ids = [...pendingSources];
    pendingSources.clear();
    for (const id of ids) {
      const item = itemBySource(id);
      if (item)
        getItem(item.id)
          .then(upsert)
          .catch(() => {});
    }
  }, 300);
}

// A catalog event changes more than its own row: the counts, the collections, a
// course's progress (a lesson was marked done) and whether the item still matches the
// filters. Throttled, not debounced, so an import's steady stream of events cannot
// postpone the resync indefinitely.
let resyncTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleResync(): void {
  if (resyncTimer) return;
  resyncTimer = setTimeout(() => {
    resyncTimer = null;
    void refresh(true);
  }, 500);
}

let watching = false;

/** Subscribe once to the three channels. Idempotent — safe from every mount. */
export function initKbWatch(): void {
  if (watching) return;
  watching = true;
  subscribeChannel('kb', (msg: WsMessage) => {
    if (msg.event === 'item') {
      const item = msg.data as Item | undefined;
      if (!item?.id) return;
      const known = items.has(item.id);
      if (known || !item.parent_id) upsert(item);
      if (tree && (inTree(tree, item.id) || (item.parent_id && inTree(tree, item.parent_id)))) {
        scheduleTreeReload();
      }
      scheduleResync();
    } else if (msg.event === 'removed') {
      const ids = (msg.data as { ids?: string[] } | undefined)?.ids ?? [];
      if (ids.length) remove(ids);
      scheduleResync();
    }
  });
  subscribeChannel('library', (msg: WsMessage) => {
    if (msg.event !== 'source') return;
    const source = msg.data as { id?: string } | undefined;
    if (source?.id && itemBySource(source.id)) scheduleSourceRefresh(source.id);
  });
  subscribeChannel('docviewer', (msg: WsMessage) => {
    if (msg.event !== 'progress') return;
    const progress = msg.data as ImportProgress | undefined;
    if (!progress?.set_id) return;
    if (progress.status === 'crawling' || progress.status === 'indexing') {
      imports.set(progress.set_id, progress);
    } else imports.delete(progress.set_id);
    emit();
  });
}
