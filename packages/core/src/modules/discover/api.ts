import { apiGet, apiPost } from '../../api';

/** Mirrors `backend/modules/discover/models.py`. */
export type DiscoverStatus = 'ok' | 'needs_connect' | 'rate_limited' | 'degraded' | 'error';
export type Tone = 'ok' | 'warn' | 'fail' | 'info' | 'idle';

export interface Metric {
  key: string;
  label: string;
  /** `null` = upstream didn't say. Rendered as "—", never as 0. */
  value: number | null;
  unit: 'count' | 'bytes' | 'percent' | 'score';
}

export interface Badge {
  label: string;
  tone: Tone;
  title?: string | null;
}

export interface Fact {
  label: string;
  value: string;
}

export interface DiscoverLink {
  label: string;
  url: string;
}

export interface DiscoverItem {
  source: string;
  kind: string;
  id: string;
  title: string;
  subtitle: string;
  description: string;
  url: string | null;
  author: string | null;
  created_at: string | null;
  updated_at: string | null;
  thumbnail: string | null;
  tags: string[];
  badges: Badge[];
  metrics: Metric[];
  facts: Fact[];
}

export interface DiscoverPage {
  source: string;
  kind: string;
  items: DiscoverItem[];
  cursor_next: string | null;
  total: number | null;
  feed_label: string;
  /** Unix seconds. */
  fetched_at: number;
  stale: boolean;
  status: DiscoverStatus;
  message: string | null;
  retry_after: number | null;
}

export interface DiscoverDetail {
  item: DiscoverItem;
  body: string | null;
  body_format: 'markdown' | 'text';
  facts: Fact[];
  links: DiscoverLink[];
  files: string[];
}

export interface Option {
  value: string;
  label: string;
}

export interface FilterSpec {
  id: string;
  label: string;
  options: Option[];
  default: string;
}

export interface KindSpec {
  id: string;
  label: string;
  sorts: Option[];
  default_sort: string;
  filters: FilterSpec[];
  searchable: boolean;
  search_placeholder: string;
}

export interface SourceSpec {
  id: string;
  label: string;
  kinds: KindSpec[];
  requires_auth: boolean;
  connected: boolean;
  auth_hint: string | null;
}

export interface ListParams {
  q?: string;
  sort?: string;
  filters?: Record<string, string>;
  cursor?: string | null;
  fresh?: boolean;
}

/** Query string for a list call. Filters travel as `f.<id>=<value>`; an empty value
 *  is sent (it means "any", which differs from "not sent" = the source's default). */
export function listQuery(kind: string, params: ListParams): string {
  const qs = new URLSearchParams({ kind });
  if (params.q) qs.set('q', params.q);
  if (params.sort) qs.set('sort', params.sort);
  if (params.cursor) qs.set('cursor', params.cursor);
  if (params.fresh) qs.set('fresh', 'true');
  for (const [id, value] of Object.entries(params.filters ?? {})) qs.set(`f.${id}`, value);
  return qs.toString();
}

export function getSources(): Promise<{ sources: SourceSpec[] }> {
  return apiGet('/discover/sources');
}

export function listItems(source: string, kind: string, params: ListParams): Promise<DiscoverPage> {
  return apiGet(`/discover/${encodeURIComponent(source)}/list?${listQuery(kind, params)}`);
}

export function getDetail(
  source: string,
  kind: string,
  id: string,
  fresh = false,
): Promise<DiscoverDetail> {
  const qs = new URLSearchParams({ kind, id });
  if (fresh) qs.set('fresh', 'true');
  return apiGet(`/discover/${encodeURIComponent(source)}/item?${qs.toString()}`);
}

export function installSkill(id: string): Promise<{ name: string; files: number }> {
  return apiPost('/discover/skills/install', { id });
}

/** The arXiv module's download route: PDF into the artifact store + library. */
export function saveArxivPaper(
  arxivId: string,
  library: string,
): Promise<{ artifact: { id: string }; source: { id: string; title: string } }> {
  return apiPost('/arxiv/download', { arxiv_id: arxivId, library });
}

/** The doc viewer's crawl: a seed URL becomes a searchable, offline doc set. */
export function captureDocSet(seedUrl: string, title: string): Promise<{ id: string }> {
  return apiPost('/docviewer/sets', { seed_url: seedUrl, title });
}
