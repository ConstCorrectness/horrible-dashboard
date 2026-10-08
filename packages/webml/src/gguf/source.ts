/**
 * Byte sources for `readGgufHeader` and, later, tensor uploads: an HTTP endpoint that
 * honours `Range` (the Hub's `resolve/` URLs, this node's file route), a `Blob` (an
 * OPFS `File`), or bytes already in memory.
 */
import type { ByteSource } from './parse';

/** `https://huggingface.co/<repo>/resolve/<revision>/<file>`, path segments escaped. */
export function hfResolveUrl(repo: string, file: string, revision = 'main'): string {
  const path = (s: string) => s.split('/').map(encodeURIComponent).join('/');
  return `https://huggingface.co/${path(repo)}/resolve/${encodeURIComponent(revision)}/${path(file)}`;
}

export interface HttpSourceOptions {
  /** The size, when the caller already knows it (the Hub's tree API lists it). */
  size?: number;
  /** Sent with every request — an `Authorization` header for gated repos. */
  headers?: Record<string, string>;
  fetch?: typeof fetch;
  signal?: AbortSignal;
}

/**
 * Range reads over HTTP. A server that ignores `Range` and answers 200 is refused
 * rather than read: that answer is the whole file, and the file is gigabytes.
 *
 * The total size comes from `Content-Range` when the response exposes it to script
 * (a cross-origin CDN may not); otherwise it stays whatever the caller passed.
 */
export class HttpSource implements ByteSource {
  private known: number | null;
  private readonly fetcher: typeof fetch;

  constructor(
    readonly url: string,
    private readonly options: HttpSourceOptions = {},
  ) {
    this.known = options.size ?? null;
    this.fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  get size(): number | null {
    return this.known;
  }

  async read(offset: number, length: number): Promise<Uint8Array<ArrayBuffer>> {
    if (length <= 0) return new Uint8Array(0);
    if (this.known !== null && offset >= this.known) return new Uint8Array(0);
    const res = await this.fetcher(this.url, {
      headers: { ...this.options.headers, Range: `bytes=${offset}-${offset + length - 1}` },
      signal: this.options.signal,
    });
    // Past the end: a range that starts beyond the file is "not satisfiable".
    if (res.status === 416) {
      await res.body?.cancel();
      return new Uint8Array(0);
    }
    if (res.status === 200) {
      await res.body?.cancel();
      throw new Error(`${this.url} ignored the Range header (answered 200 with the whole file)`);
    }
    if (res.status !== 206) {
      await res.body?.cancel();
      throw new Error(`${this.url}: HTTP ${res.status}`);
    }
    const total = /\/(\d+)\s*$/.exec(res.headers.get('content-range') ?? '');
    if (total) this.known = Number(total[1]);
    const bytes = new Uint8Array(await res.arrayBuffer());
    // A server may send more than asked (some round up to a block); never pass that on.
    return bytes.length > length ? bytes.subarray(0, length) : bytes;
  }
}

/** A Blob or File — including the `File` an OPFS handle's `getFile()` returns. */
export function blobSource(blob: Blob): ByteSource {
  return {
    size: blob.size,
    read: async (offset, length) =>
      new Uint8Array(await blob.slice(offset, offset + length).arrayBuffer()),
  };
}

/** Bytes already in memory (tests, small fixtures). */
export function bytesSource(bytes: Uint8Array): ByteSource {
  return {
    size: bytes.length,
    read: async (offset, length) => bytes.slice(offset, offset + length),
  };
}
