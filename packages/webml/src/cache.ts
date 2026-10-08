/**
 * The downloaded weights, as transformers.js keeps them: one Cache Storage cache
 * (`transformers-cache`) whose keys are the Hub URLs
 * `https://huggingface.co/<owner>/<name>/resolve/<revision>/<file>`.
 *
 * Grouped back into models here so the UI can show what is on disk and delete it.
 * Sizes come from `Content-Length` — reading a body to measure it would pull a
 * gigabyte into memory — so a response stored without one counts as unknown (0).
 */

export const CACHE_NAME = 'transformers-cache';

export interface CachedModel {
  id: string;
  files: number;
  bytes: number;
}

/** `https://huggingface.co/a/b/resolve/main/onnx/x.onnx` → `a/b`; null for anything else. */
export function modelIdFromUrl(url: string): string | null {
  let path: string;
  try {
    path = new URL(url).pathname;
  } catch {
    return null;
  }
  const m = /^\/([^/]+)\/([^/]+)\/resolve\//.exec(path);
  return m ? `${decodeURIComponent(m[1])}/${decodeURIComponent(m[2])}` : null;
}

function storage(): CacheStorage | null {
  try {
    return typeof caches === 'undefined' ? null : caches;
  } catch {
    // An opaque origin throws on access instead of being undefined.
    return null;
  }
}

export async function listCachedModels(
  store: CacheStorage | null = storage(),
): Promise<CachedModel[]> {
  if (!store || !(await store.has(CACHE_NAME))) return [];
  const cache = await store.open(CACHE_NAME);
  const byId = new Map<string, CachedModel>();
  for (const request of await cache.keys()) {
    const id = modelIdFromUrl(request.url);
    if (!id) continue;
    const entry = byId.get(id) ?? { id, files: 0, bytes: 0 };
    const response = await cache.match(request);
    entry.files += 1;
    entry.bytes += Number(response?.headers.get('content-length') ?? 0) || 0;
    byId.set(id, entry);
  }
  return [...byId.values()].sort((a, b) => b.bytes - a.bytes);
}

/** Drop every cached file of one model. Returns how many were removed. */
export async function deleteCachedModel(
  id: string,
  store: CacheStorage | null = storage(),
): Promise<number> {
  if (!store || !(await store.has(CACHE_NAME))) return 0;
  const cache = await store.open(CACHE_NAME);
  let removed = 0;
  for (const request of await cache.keys()) {
    if (modelIdFromUrl(request.url) === id && (await cache.delete(request))) removed++;
  }
  return removed;
}

export async function isModelCached(
  id: string,
  store: CacheStorage | null = storage(),
): Promise<boolean> {
  return (await listCachedModels(store)).some((m) => m.id === id);
}
