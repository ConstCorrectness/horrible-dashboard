/**
 * Client for `/api/search/*`.
 *
 * Search itself is a POST even though it's a read: a query is user text, and a query
 * string is the one place it must not go — URLs land in logs, referrers, and the
 * observability panel's `target` field.
 */

export interface SearchHit {
  url: string;
  title: string;
  snippet: string;
  text?: string | null;
  score: number;
  providers: string[];
  published?: string | null;
  host: string;
}

export interface SearchResponse {
  query: string;
  hits: SearchHit[];
  rewrites: string[];
  providers_used: string[];
  notes: string[];
  cached: number;
  elapsed_ms: number;
}

export interface ProviderInfo {
  id: string;
  label: string;
  needs_key: boolean;
  configured: boolean;
  /** Why it can't run, when it can't. Empty when it can. */
  reason: string;
}

export interface ProvidersResponse {
  providers: ProviderInfo[];
  selected: string;
  active: string[];
}

export interface CrawlSeed {
  id: string;
  label: string;
  config: Record<string, unknown>;
  enabled: boolean;
  builtin: boolean;
  last_crawled_at?: string | null;
  last_status?: string | null;
  last_error?: string | null;
  pages: number;
}

export interface IndexStatus {
  collection: string;
  docs: number;
  embed_model?: string | null;
  dim?: number | null;
  reindex_needed: boolean;
}

export interface CrawlStatus {
  seeds: CrawlSeed[];
  index: IndexStatus;
}

/** Live crawl progress, pushed on the `crawl` channel. */
export interface CrawlProgress {
  seed_id: string;
  fetched: number;
  indexed: number;
  unchanged: number;
  not_modified: number;
  skipped: number;
  errors: number;
  chunks: number;
  notes: string[];
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export function search(
  query: string,
  opts: { depth?: 'quick' | 'deep'; limit?: number; site?: string; freshness?: string } = {},
): Promise<SearchResponse> {
  return json<SearchResponse>('/api/search/query', {
    method: 'POST',
    body: JSON.stringify({ query, depth: opts.depth ?? 'quick', ...opts }),
  });
}

export function listProviders(): Promise<ProvidersResponse> {
  return json<ProvidersResponse>('/api/search/providers');
}

export function crawlStatus(): Promise<CrawlStatus> {
  return json<CrawlStatus>('/api/search/crawl/status');
}

export function startCrawl(seedId?: string, force = false): Promise<{ queued: string[] }> {
  return json<{ queued: string[] }>('/api/search/crawl', {
    method: 'POST',
    body: JSON.stringify({ seed_id: seedId ?? null, force }),
  });
}

export function clearSearchCache(): Promise<{ results: number; pages: number }> {
  return json<{ results: number; pages: number }>('/api/search/cache/clear', {
    method: 'POST',
  });
}
