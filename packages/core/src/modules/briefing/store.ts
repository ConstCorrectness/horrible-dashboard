/**
 * The briefing's client-side state: two independent sections, loaded lazily.
 *
 * Nothing fetches at boot. The first Spotlight open asks, and every open after
 * that reuses the answer until it is `FRESH_MS` old — Spotlight opens dozens of
 * times an hour, and the backend's own cache is the thing that protects the
 * upstream APIs, not this. The two sections load separately so a slow source
 * never holds the other back.
 */
import { useSyncExternalStore } from 'react';

import { fetchNews, fetchPapers, type BriefingPaper, type BriefingStory } from './api';

export const FRESH_MS = 10 * 60 * 1000;

export interface BriefingSection<T> {
  status: 'idle' | 'loading' | 'ready' | 'error';
  items: T[];
  /** Epoch ms of the upstream fetch the items came from. */
  fetchedAt: number | null;
  /** The backend's refresh failed and these are its last good items. */
  stale: boolean;
  error: string | null;
  /** Epoch ms this client last asked, to decide when to ask again. */
  loadedAt: number | null;
}

export interface BriefingState {
  papers: BriefingSection<BriefingPaper>;
  news: BriefingSection<BriefingStory>;
}

function empty<T>(): BriefingSection<T> {
  return { status: 'idle', items: [], fetchedAt: null, stale: false, error: null, loadedAt: null };
}

let state: BriefingState = { papers: empty(), news: empty() };
const listeners = new Set<() => void>();

function set<K extends keyof BriefingState>(key: K, patch: Partial<BriefingState[K]>): void {
  state = { ...state, [key]: { ...state[key], ...patch } };
  for (const l of listeners) l();
}

export function getBriefing(): BriefingState {
  return state;
}

export function subscribeBriefing(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useBriefing(): BriefingState {
  return useSyncExternalStore(subscribeBriefing, getBriefing, getBriefing);
}

async function loadSection<K extends keyof BriefingState>(
  key: K,
  fetch: (
    refresh: boolean,
  ) => Promise<{ items: BriefingState[K]['items']; fetched_at: number; stale: boolean }>,
  force: boolean,
  now: number,
): Promise<void> {
  const current = state[key];
  if (current.status === 'loading') return;
  if (
    !force &&
    current.status === 'ready' &&
    current.loadedAt &&
    now - current.loadedAt < FRESH_MS
  ) {
    return;
  }
  set(key, { status: 'loading', error: null } as Partial<BriefingState[K]>);
  try {
    const res = await fetch(force);
    set(key, {
      status: 'ready',
      items: res.items,
      fetchedAt: res.fetched_at * 1000,
      stale: res.stale,
      error: null,
      loadedAt: now,
    } as Partial<BriefingState[K]>);
  } catch (err) {
    // Keep whatever was there: a refresh that failed should not blank the list.
    set(key, {
      status: 'error',
      error: err instanceof Error ? err.message : String(err),
      loadedAt: now,
    } as Partial<BriefingState[K]>);
  }
}

/** Load both sections, unless each is still fresh. `force` also refreshes upstream. */
export function loadBriefing({ force = false }: { force?: boolean } = {}): Promise<void> {
  const now = Date.now();
  return Promise.all([
    loadSection(
      'papers',
      (r) =>
        fetchPapers(r).then((b) => ({ items: b.papers, fetched_at: b.fetched_at, stale: b.stale })),
      force,
      now,
    ),
    loadSection(
      'news',
      (r) =>
        fetchNews(r).then((b) => ({ items: b.stories, fetched_at: b.fetched_at, stale: b.stale })),
      force,
      now,
    ),
  ]).then(() => undefined);
}

/** Tests only. */
export function resetBriefingForTests(): void {
  state = { papers: empty(), news: empty() };
  listeners.clear();
}
