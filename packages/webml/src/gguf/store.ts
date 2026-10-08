/**
 * GGUF weights on this device: the origin private file system (OPFS), one file per
 * model in `webml-gguf/`, with a JSON sidecar saying where it came from and
 * whether it is complete.
 *
 * OPFS rather than Cache Storage (docs/architecture/webml-gguf-engine.mdx,
 * decision 1): a GGUF is one file of up to a few GB, and the loader reads it a
 * tensor at a time. A `File` from OPFS serves those ranges without holding the
 * file in memory; a cached `Response` would have to be read whole.
 *
 * Downloads resume: an interrupted one leaves its bytes and an incomplete
 * sidecar, and the next attempt asks the server for the rest.
 *
 * Works in a window and in a worker (everything here is the async OPFS API).
 */

export const GGUF_DIR = 'webml-gguf';
const ID_PREFIX = 'gguf:';

/** `gguf:<owner>/<repo>/<path/in/repo.gguf>` — the model id GGUF files go by everywhere. */
export function ggufModelId(repo: string, file: string): string {
  return `${ID_PREFIX}${repo}/${file}`;
}

export function isGgufModelId(id: string): boolean {
  return id.startsWith(ID_PREFIX);
}

/** The Hub repo and file of a GGUF model id, or null for anything else. */
export function parseGgufModelId(id: string): { repo: string; file: string } | null {
  if (!isGgufModelId(id)) return null;
  const parts = id.slice(ID_PREFIX.length).split('/');
  if (parts.length < 3 || parts.some((p) => !p || p === '.' || p === '..')) return null;
  if (!parts.at(-1)!.toLowerCase().endsWith('.gguf')) return null;
  return { repo: parts.slice(0, 2).join('/'), file: parts.slice(2).join('/') };
}

export interface StoredGguf {
  id: string;
  /** Bytes on disk. */
  bytes: number;
  /** Bytes the finished file has, once known. */
  total: number | null;
  complete: boolean;
}

interface Sidecar {
  id: string;
  url: string;
  total: number | null;
  complete: boolean;
}

async function directory(): Promise<FileSystemDirectoryHandle> {
  const root = await navigator.storage.getDirectory();
  return root.getDirectoryHandle(GGUF_DIR, { create: true });
}

/** OPFS names cannot contain `/`; the id, escaped, is the name. */
const fileName = (id: string) => `${encodeURIComponent(id)}.gguf`;
const sidecarName = (id: string) => `${encodeURIComponent(id)}.json`;

async function readSidecar(dir: FileSystemDirectoryHandle, id: string): Promise<Sidecar | null> {
  try {
    const file = await (await dir.getFileHandle(sidecarName(id))).getFile();
    return JSON.parse(await file.text()) as Sidecar;
  } catch {
    return null;
  }
}

async function writeSidecar(dir: FileSystemDirectoryHandle, s: Sidecar): Promise<void> {
  const w = await (await dir.getFileHandle(sidecarName(s.id), { create: true })).createWritable();
  await w.write(JSON.stringify(s));
  await w.close();
}

export async function listGgufs(): Promise<StoredGguf[]> {
  const dir = await directory();
  const out: StoredGguf[] = [];
  for await (const [name] of dir as unknown as AsyncIterable<[string, FileSystemHandle]>) {
    if (!name.endsWith('.json')) continue;
    const id = decodeURIComponent(name.slice(0, -'.json'.length));
    const meta = await readSidecar(dir, id);
    if (!meta) continue;
    let bytes = 0;
    try {
      bytes = (await (await dir.getFileHandle(fileName(id))).getFile()).size;
    } catch {
      // Sidecar without data: an aborted first request. Listed so it can be deleted.
    }
    out.push({ id, bytes, total: meta.total, complete: meta.complete });
  }
  return out.sort((a, b) => b.bytes - a.bytes);
}

/** The complete file for `id`, or null if it is absent or still partial. */
export async function openGguf(id: string): Promise<File | null> {
  const dir = await directory();
  const meta = await readSidecar(dir, id);
  if (!meta?.complete) return null;
  try {
    return await (await dir.getFileHandle(fileName(id))).getFile();
  } catch {
    return null;
  }
}

export async function deleteGguf(id: string): Promise<boolean> {
  const dir = await directory();
  let removed = false;
  for (const name of [fileName(id), sidecarName(id)]) {
    try {
      await dir.removeEntry(name);
      removed = true;
    } catch {
      // Already gone.
    }
  }
  return removed;
}

export interface DownloadOptions {
  /** The file's size when known in advance (the Hub's tree API lists it). */
  size?: number;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  fetch?: typeof fetch;
  onProgress?: (loaded: number, total: number | null) => void;
}

/**
 * Download `url` into the store as `id`, resuming a partial download, and return
 * the complete file. Refuses up front when the origin's storage quota cannot hold
 * the rest.
 */
export async function downloadGguf(
  id: string,
  url: string,
  options: DownloadOptions = {},
): Promise<File> {
  const done = await openGguf(id);
  if (done) return done;
  const dir = await directory();
  const handle = await dir.getFileHandle(fileName(id), { create: true });
  const have = (await handle.getFile()).size;
  let total = options.size ?? (await readSidecar(dir, id))?.total ?? null;
  await writeSidecar(dir, { id, url, total, complete: false });

  if (total !== null && have < total) await checkQuota(total - have);

  const fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
  const res = await fetcher(url, {
    headers: { ...options.headers, ...(have > 0 ? { Range: `bytes=${have}-` } : {}) },
    signal: options.signal,
  });
  let start: number;
  if (res.status === 206) {
    start = have;
    const m = /\/(\d+)\s*$/.exec(res.headers.get('content-range') ?? '');
    if (m) total = Number(m[1]);
  } else if (res.status === 200) {
    start = 0; // the server sent it all again: start over
    const length = Number(res.headers.get('content-length'));
    if (length > 0) total = length;
  } else if (res.status === 416 && total !== null && have === total) {
    start = have; // already had every byte; only the sidecar was behind
  } else {
    await res.body?.cancel();
    throw new Error(`${url}: HTTP ${res.status}`);
  }
  await writeSidecar(dir, { id, url, total, complete: false });

  const writable = await handle.createWritable({ keepExistingData: start > 0 });
  let loaded = start;
  try {
    await writable.seek(start);
    if (res.status !== 416 && res.body) {
      const reader = res.body.getReader();
      for (;;) {
        const { done: end, value } = await reader.read();
        if (end) break;
        await writable.write(value);
        loaded += value.length;
        options.onProgress?.(loaded, total);
      }
    }
    await writable.close();
  } catch (err) {
    // Keep what arrived: closing commits it, and the next attempt resumes from there.
    await writable.close().catch(() => writable.abort().catch(() => undefined));
    throw err;
  }

  if (total !== null && loaded !== total) {
    throw new Error(`${url}: got ${loaded} of ${total} bytes; try again to resume`);
  }
  await writeSidecar(dir, { id, url, total: loaded, complete: true });
  return (await handle.getFile()) as File;
}

async function checkQuota(needed: number): Promise<void> {
  const estimate = await navigator.storage.estimate?.();
  if (!estimate?.quota) return;
  const free = estimate.quota - (estimate.usage ?? 0);
  if (free < needed) {
    const gb = (n: number) => `${(n / 1e9).toFixed(2)} GB`;
    throw new Error(
      `not enough storage for this model: it needs ${gb(needed)}, ${gb(free)} is free`,
    );
  }
}
