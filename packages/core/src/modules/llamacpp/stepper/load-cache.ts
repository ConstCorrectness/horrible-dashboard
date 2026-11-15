/**
 * Results the stepper has already read, by the key its view loads under.
 *
 * Every key names a recorded trace plus a node/layer/pass inside it, and a
 * recorded trace never changes, so a read is good for the session. Without this,
 * each step back across the timeline re-fetched the slice and showed "Reading…" /
 * "Unembedding…" for a node already seen a moment earlier — which read as the
 * window failing to load.
 *
 * Bounded (least recently used out) because a matrix slice can be large and a
 * long scrub visits hundreds of nodes.
 */
const LIMIT = 200;
const results = new Map<string, unknown>();

export function cachedLoad<T>(key: string): T | undefined {
  if (!results.has(key)) return undefined;
  const value = results.get(key) as T;
  // Re-insert: Map keeps insertion order, so the oldest entry is always first.
  results.delete(key);
  results.set(key, value);
  return value;
}

export function rememberLoad<T>(key: string, value: T): void {
  results.delete(key);
  results.set(key, value);
  while (results.size > LIMIT) {
    const oldest = results.keys().next().value as string;
    results.delete(oldest);
  }
}

/**
 * The last error a key failed with. Kept apart from results because an error may
 * be transient: it is shown at once on a revisit (block 0's "input not recorded"
 * otherwise flashed "Reading…" on every visit) but the read is still retried.
 */
const failures = new Map<string, string>();

export function lastFailure(key: string): string | undefined {
  return failures.get(key);
}

export function rememberFailure(key: string, error: string): void {
  failures.delete(key);
  failures.set(key, error);
  while (failures.size > LIMIT) failures.delete(failures.keys().next().value as string);
}

export function forgetFailure(key: string): void {
  failures.delete(key);
}

/** Tests only. */
export function clearLoadCache(): void {
  results.clear();
  failures.clear();
}
