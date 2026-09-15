/**
 * Client-side 3D Asset Cache using Web CacheStorage (Cache API).
 *
 * Provides persistent offline caching in browsers without re-downloading
 * multi-megabyte GLBs across sessions.
 */

import manifest from '../assets.manifest.json';

// Keyed by the manifest version: entries are keyed by URL and never revalidated,
// so a GLB rebuilt under the same filename would otherwise be served stale forever.
const CACHE_PREFIX = 'horrible-assets-v';
const CACHE_NAME = `${CACHE_PREFIX}${manifest.version}`;
const blobMap = new Map<string, string>();
let staleCachesPurged = false;

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
    const matched = await cache.match(url);
    if (matched) {
      const blob = await matched.blob();
      const blobUrl = URL.createObjectURL(blob);
      blobMap.set(url, blobUrl);
      return blobUrl;
    }

    // Not cached yet: fetch and cache
    const response = await fetch(url);
    if (response.ok) {
      void cache.put(url, response.clone()).catch(() => {});
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
