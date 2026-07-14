/**
 * Typed client for the `browser` backend module (`/api/browser`). Reader mode is
 * a server-side fetch+extract (SSRF-guarded) so sites that refuse iframing can
 * still be read inline; history and bookmarks are a small server-side catalog so
 * they persist across reloads and machines.
 */
import { apiDelete, apiGet, apiPost } from '../../api';

/** Extracted readable article from a URL the page itself may refuse to frame. */
export interface ReaderArticle {
  url: string;
  title: string;
  author: string | null;
  text: string;
}

export interface HistoryEntry {
  id: string;
  url: string;
  title: string;
  visited_at: string;
}

export interface Bookmark {
  id: string;
  url: string;
  title: string;
  tags: string[];
  added_at: string;
}

/** Availability of the real headless-Chromium engine (`full` browser mode). */
export interface EngineStatus {
  enabled: boolean; // HORRIBLE_ENABLE_SERVER_BROWSER=1 on the backend
  installed: boolean; // the browser-engine (playwright) extra is importable
}

export function engineStatus(): Promise<EngineStatus> {
  return apiGet<EngineStatus>('/browser/engine');
}

export function readerMode(url: string): Promise<ReaderArticle> {
  return apiGet<ReaderArticle>(`/browser/read?url=${encodeURIComponent(url)}`);
}

export function listHistory(limit = 100): Promise<{ entries: HistoryEntry[] }> {
  return apiGet<{ entries: HistoryEntry[] }>(`/browser/history?limit=${limit}`);
}

export function recordHistory(url: string, title: string): Promise<HistoryEntry> {
  return apiPost<HistoryEntry>('/browser/history', { url, title });
}

export function clearHistory(): Promise<{ ok: boolean }> {
  return apiDelete<{ ok: boolean }>('/browser/history');
}

export function listBookmarks(): Promise<{ bookmarks: Bookmark[] }> {
  return apiGet<{ bookmarks: Bookmark[] }>('/browser/bookmarks');
}

export function addBookmark(url: string, title: string, tags: string[] = []): Promise<Bookmark> {
  return apiPost<Bookmark>('/browser/bookmarks', { url, title, tags });
}

export function removeBookmark(id: string): Promise<{ ok: boolean }> {
  return apiDelete<{ ok: boolean }>(`/browser/bookmarks/${encodeURIComponent(id)}`);
}
