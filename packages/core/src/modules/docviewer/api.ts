/**
 * Typed client for the documentation viewer backend (`/api/docviewer/*`).
 *
 * Mirrors `backend/modules/docviewer/models.py`. Backs both the pane and the
 * module's agent tools, so neither has to import the other.
 */
import { apiDelete, apiGet, apiPost } from '../../api';

export type SetStatus = 'queued' | 'crawling' | 'ready' | 'failed';
export type PageStatus = 'pending' | 'captured' | 'failed';

export interface DocSet {
  id: string;
  title: string;
  seed_url: string;
  prefix: string;
  library: string;
  status: SetStatus;
  error?: string | null;
  page_count: number;
  max_pages: number;
  created_at: string;
  last_crawled_at?: string | null;
}

export interface DocPage {
  id: string;
  set_id: string;
  url: string;
  title: string;
  status: PageStatus;
  error?: string | null;
  artifact_id?: string | null;
  source_id?: string | null;
  parent_id?: string | null;
  depth: number;
  ordinal: number;
  bytes: number;
}

export interface CrawlProgress {
  set_id: string;
  status: SetStatus;
  captured: number;
  failed: number;
  queued: number;
  current_url?: string | null;
  error?: string | null;
}

export interface CreateSetRequest {
  seed_url: string;
  title?: string;
  prefix?: string;
  library?: string;
  max_pages?: number;
  max_depth?: number;
}

export interface SearchHit {
  page_id?: string | null;
  url?: string | null;
  title: string;
  snippet: string;
  score?: number | null;
}

export function listSets(): Promise<{ sets: DocSet[] }> {
  return apiGet('/docviewer/sets');
}

export function getSet(setId: string): Promise<DocSet> {
  return apiGet(`/docviewer/sets/${setId}`);
}

export function createSet(req: CreateSetRequest): Promise<DocSet> {
  return apiPost('/docviewer/sets', req);
}

export function recrawlSet(setId: string): Promise<CrawlProgress> {
  return apiPost(`/docviewer/sets/${setId}/recrawl`, {});
}

export function deleteSet(setId: string): Promise<{ ok: boolean }> {
  return apiDelete(`/docviewer/sets/${setId}`);
}

export function listPages(setId: string): Promise<{ pages: DocPage[] }> {
  return apiGet(`/docviewer/sets/${setId}/pages`);
}

export function searchSet(
  setId: string,
  query: string,
  limit = 10,
): Promise<{ hits: SearchHit[] }> {
  return apiPost(`/docviewer/sets/${setId}/search`, { query, limit });
}

/**
 * Where a captured page's bytes live.
 *
 * Deliberately the docviewer route rather than `/api/artifacts/<id>`: archives link
 * to each other by page id, because an artifact id is a hash of the bytes and two
 * pages that link to each other could never compute one. Using the same address here
 * means a link followed inside the frame and a page opened from the sidebar resolve
 * to exactly the same URL, so the frame's history stays coherent.
 */
export function pageContentUrl(pageId: string): string {
  return `/api/docviewer/pages/${pageId}/content`;
}
