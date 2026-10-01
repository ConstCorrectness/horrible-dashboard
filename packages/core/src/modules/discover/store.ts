/**
 * Discover's state, held outside the component.
 *
 * Inactive panes unmount, and so does a section you switch away from. Kept in a
 * component, every switch back would refetch and lose the query, the scroll
 * position's anchor (the selection) and the "load more" pages already paid for. Here
 * each (source, kind) view keeps all of it for the life of the app, and coming back
 * to a section costs nothing.
 *
 * Every request carries a per-view sequence number and a reply that has been
 * overtaken is dropped — otherwise a slow search for "ll" resolving after the fast
 * one for "llama" puts the wrong results under the right query.
 */
import { useSyncExternalStore } from 'react';

import { ApiError } from '../../api';
import {
  getDetail,
  getSources,
  listItems,
  type DiscoverDetail,
  type DiscoverItem,
  type DiscoverPage,
  type KindSpec,
  type SourceSpec,
} from './api';

/** How long typing pauses before a search goes out. Enter skips the wait. */
export const SEARCH_DEBOUNCE_MS = 300;

export interface ViewState {
  /** What's in the search box. */
  draft: string;
  /** The query the shown results are for. */
  q: string;
  sort: string;
  /** Only filters the user has touched. An untouched filter shows its spec default
   *  and isn't sent, so the backend's default applies. */
  filters: Record<string, string>;
  /** The newest page's metadata (status, label, cursor). */
  page: DiscoverPage | null;
  /** Every page's items, concatenated. */
  items: DiscoverItem[];
  loading: boolean;
  loadingMore: boolean;
  /** The request itself failed (backend unreachable) — distinct from an upstream
   *  status, which arrives inside a successful page. */
  error: string | null;
  selected: string | null;
}

export interface DetailState {
  status: 'loading' | 'ready' | 'error';
  data?: DiscoverDetail;
  error?: string;
}

export interface DiscoverState {
  specs: SourceSpec[] | null;
  specsError: string | null;
  views: Record<string, ViewState>;
  /** The kind tab each section last showed. */
  kinds: Record<string, string>;
  details: Record<string, DetailState>;
}

const EMPTY_VIEW: ViewState = {
  draft: '',
  q: '',
  sort: '',
  filters: {},
  page: null,
  items: [],
  loading: false,
  loadingMore: false,
  error: null,
  selected: null,
};

let state: DiscoverState = { specs: null, specsError: null, views: {}, kinds: {}, details: {} };
const listeners = new Set<() => void>();
const seqs = new Map<string, number>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();
let specsPromise: Promise<void> | null = null;

function emit(next: DiscoverState): void {
  state = next;
  for (const l of listeners) l();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getState(): DiscoverState {
  return state;
}

export function useDiscover(): DiscoverState {
  return useSyncExternalStore(subscribe, getState, getState);
}

/** Test seam: forget everything. */
export function resetDiscover(): void {
  for (const t of timers.values()) clearTimeout(t);
  timers.clear();
  seqs.clear();
  specsPromise = null;
  state = { specs: null, specsError: null, views: {}, kinds: {}, details: {} };
}

export const viewKey = (source: string, kind: string) => `${source}:${kind}`;
export const detailKey = (source: string, kind: string, id: string) => `${source}:${kind}:${id}`;

export function viewOf(s: DiscoverState, source: string, kind: string): ViewState {
  return s.views[viewKey(source, kind)] ?? EMPTY_VIEW;
}

function patchView(source: string, kind: string, patch: Partial<ViewState>): void {
  const key = viewKey(source, kind);
  emit({
    ...state,
    views: { ...state.views, [key]: { ...viewOf(state, source, kind), ...patch } },
  });
}

export function kindSpecOf(s: DiscoverState, source: string, kind: string): KindSpec | undefined {
  return s.specs?.find((x) => x.id === source)?.kinds.find((k) => k.id === kind);
}

/** The sort a view is using: its own, else the spec's default. */
export function effectiveSort(s: DiscoverState, source: string, kind: string): string {
  return viewOf(s, source, kind).sort || kindSpecOf(s, source, kind)?.default_sort || '';
}

/** A filter's shown value: the user's choice, else the spec's default. */
export function effectiveFilter(
  s: DiscoverState,
  source: string,
  kind: string,
  filterId: string,
): string {
  const chosen = viewOf(s, source, kind).filters[filterId];
  if (chosen !== undefined) return chosen;
  return kindSpecOf(s, source, kind)?.filters.find((f) => f.id === filterId)?.default ?? '';
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function ensureSpecs(fresh = false): Promise<void> {
  if (specsPromise && !fresh) return specsPromise;
  specsPromise = getSources().then(
    (res) => emit({ ...state, specs: res.sources, specsError: null }),
    (error: unknown) => {
      specsPromise = null;
      emit({ ...state, specsError: message(error) });
    },
  );
  return specsPromise;
}

/** Fetch a view's first page (or the next one with `append`). */
export async function load(
  source: string,
  kind: string,
  { fresh = false, append = false }: { fresh?: boolean; append?: boolean } = {},
): Promise<void> {
  const key = viewKey(source, kind);
  const ticket = (seqs.get(key) ?? 0) + 1;
  seqs.set(key, ticket);
  const view = viewOf(state, source, kind);
  const cursor = append ? view.page?.cursor_next : null;
  if (append && !cursor) return;
  patchView(source, kind, append ? { loadingMore: true } : { loading: true, error: null });
  try {
    const page = await listItems(source, kind, {
      q: view.q,
      sort: view.sort,
      filters: view.filters,
      cursor,
      fresh,
    });
    if (seqs.get(key) !== ticket) return; // overtaken by a newer request
    const current = viewOf(state, source, kind);
    const items = append ? dedupe([...current.items, ...page.items]) : page.items;
    patchView(source, kind, {
      page,
      items,
      loading: false,
      loadingMore: false,
      error: null,
      // Keep a selection that's still in the list; otherwise leave the detail
      // empty rather than jumping to an item the user never picked.
      selected: items.some((i) => i.id === current.selected) ? current.selected : null,
    });
  } catch (error) {
    if (seqs.get(key) !== ticket) return;
    patchView(source, kind, { loading: false, loadingMore: false, error: message(error) });
  }
}

function dedupe(items: DiscoverItem[]): DiscoverItem[] {
  const seen = new Set<string>();
  return items.filter((i) => (seen.has(i.id) ? false : (seen.add(i.id), true)));
}

/** Load a view the first time it's shown. A no-op once it has results or is loading. */
export function ensureLoaded(source: string, kind: string): void {
  const view = viewOf(state, source, kind);
  if (view.page || view.loading || view.error) return;
  void load(source, kind);
}

function commit(source: string, kind: string): void {
  const key = viewKey(source, kind);
  const pending = timers.get(key);
  if (pending) clearTimeout(pending);
  timers.delete(key);
  const view = viewOf(state, source, kind);
  const q = view.draft.trim();
  if (q === view.q && view.page) return;
  patchView(source, kind, { q, selected: null });
  void load(source, kind);
}

/** Typing: update the box now, search after a pause. */
export function setDraft(source: string, kind: string, draft: string): void {
  patchView(source, kind, { draft });
  const key = viewKey(source, kind);
  const pending = timers.get(key);
  if (pending) clearTimeout(pending);
  timers.set(
    key,
    setTimeout(() => commit(source, kind), SEARCH_DEBOUNCE_MS),
  );
}

/** Enter: search now. */
export function submit(source: string, kind: string): void {
  commit(source, kind);
}

export function setSort(source: string, kind: string, sort: string): void {
  patchView(source, kind, { sort });
  void load(source, kind);
}

export function setFilter(source: string, kind: string, filterId: string, value: string): void {
  const view = viewOf(state, source, kind);
  patchView(source, kind, { filters: { ...view.filters, [filterId]: value }, selected: null });
  void load(source, kind);
}

export function loadMore(source: string, kind: string): void {
  void load(source, kind, { append: true });
}

export function setActiveKind(section: string, kind: string): void {
  emit({ ...state, kinds: { ...state.kinds, [section]: kind } });
}

export async function select(
  source: string,
  kind: string,
  id: string | null,
  { fresh = false }: { fresh?: boolean } = {},
): Promise<void> {
  patchView(source, kind, { selected: id });
  if (!id) return;
  const key = detailKey(source, kind, id);
  const existing = state.details[key];
  if (existing && existing.status !== 'error' && !fresh) return;
  emit({
    ...state,
    details: { ...state.details, [key]: { status: 'loading', data: existing?.data } },
  });
  try {
    const data = await getDetail(source, kind, id, fresh);
    emit({ ...state, details: { ...state.details, [key]: { status: 'ready', data } } });
  } catch (error) {
    const text = error instanceof ApiError ? error.message : message(error);
    emit({ ...state, details: { ...state.details, [key]: { status: 'error', error: text } } });
  }
}

/** Refetch a view bypassing the backend cache, and its open detail. */
export function refresh(source: string, kind: string): void {
  void load(source, kind, { fresh: true });
  const selected = viewOf(state, source, kind).selected;
  if (selected) void select(source, kind, selected, { fresh: true });
}

/** Search a section from outside the pane (the `discover.search` command). */
export function searchFor(source: string, kind: string, q: string): void {
  patchView(source, kind, { draft: q });
  commit(source, kind);
}
