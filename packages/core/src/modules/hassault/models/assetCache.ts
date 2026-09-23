/**
 * Client-side 3D Asset Cache using Web CacheStorage (Cache API).
 *
 * Provides persistent offline caching in browsers without re-downloading
 * multi-megabyte GLBs across sessions.
 */

import manifest from '../assets.manifest.json';

// CacheStorage entries are never revalidated, so an entry must be keyed by
// something that changes when the bytes do. That used to be the manifest
// *version* alone, and the maps were rebuilt under v3 without a bump: every
// browser that had opened one kept drawing the old geometry. Now an entry is
// keyed by the asset's own sha256, and only manifest assets — the ones that have
// a sha256 — are cached here at all. Everything else (the `hd_*.glb` maps, which
// are committed files rather than release assets) goes through the ordinary HTTP
// cache, which revalidates.
const CACHE_PREFIX = 'horrible-assets-v';
const CACHE_NAME = `${CACHE_PREFIX}${manifest.version}`;
const blobMap = new Map<string, string>();
let staleCachesPurged = false;
let staleEntriesPurged = false;

const PUBLIC_PREFIX = 'apps/web/public/';

/** `/<file>` → the sha256 the manifest pins for it. */
const SHA_BY_URL = new Map<string, string>(
  Object.values(manifest.assets as Record<string, { destination: string; sha256: string }>)
    .filter((a) => a.destination.startsWith(PUBLIC_PREFIX))
    .map((a) => [`/${a.destination.slice(PUBLIC_PREFIX.length)}`, a.sha256]),
);

/** The CacheStorage key for a URL, or `null` if it must not be cached here. */
export function assetCacheKey(url: string): string | null {
  const sha = SHA_BY_URL.get(url);
  return sha ? `${url}?sha256=${sha}` : null;
}

async function purgeStaleCaches(): Promise<void> {
  if (staleCachesPurged) return;
  staleCachesPurged = true;
  const names = await window.caches.keys();
  await Promise.all(
    names
      .filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
      .map((name) => window.caches.delete(name)),
  );
}

/** Drop entries this build would never ask for: other hashes, and uncacheable URLs. */
async function purgeStaleEntries(cache: Cache): Promise<void> {
  const wanted = new Set([...SHA_BY_URL.keys()].map((u) => assetCacheKey(u)));
  const keys = await cache.keys();
  await Promise.all(
    keys
      .filter((req) => {
        const u = new URL(req.url);
        return !wanted.has(`${u.pathname}${u.search}`);
      })
      .map((req) => cache.delete(req)),
  );
}

/**
 * Resolve an asset URL, pulling from or populating the browser's CacheStorage
 * when available. Falls back gracefully to direct URL resolution in desktop / SSR.
 */
export async function getCachedAssetUrl(url: string): Promise<string> {
  if (blobMap.has(url)) {
    return blobMap.get(url)!;
  }

  if (typeof window === 'undefined' || !('caches' in window)) {
    return url;
  }

  try {
    void purgeStaleCaches().catch(() => {});
    const cache = await window.caches.open(CACHE_NAME);
    if (!staleEntriesPurged) {
      staleEntriesPurged = true;
      void purgeStaleEntries(cache).catch(() => {});
    }
    const key = assetCacheKey(url);
    if (!key) return url;

    const matched = await cache.match(key);
    if (matched) {
      const blob = await matched.blob();
      const blobUrl = URL.createObjectURL(blob);
      blobMap.set(url, blobUrl);
      return blobUrl;
    }

    // Not cached yet: fetch and cache
    const response = await fetch(url);
    if (response.ok) {
      void cache.put(key, response.clone()).catch(() => {});
      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);
      blobMap.set(url, blobUrl);
      return blobUrl;
    }
  } catch {
    // If CacheStorage fails (e.g. file:// or restricted iframe), fall through to raw URL
  }

  return url;
}
