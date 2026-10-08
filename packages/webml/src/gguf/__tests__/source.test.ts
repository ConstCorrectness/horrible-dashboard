import { describe, expect, it } from 'vitest';

import { readGgufHeader } from '../parse';
import { HttpSource, hfResolveUrl } from '../source';
import { gguf } from './write';

/** A fetch that serves `file` with Range support, like the Hub's CDN does. */
function rangeServer(
  file: Uint8Array,
  opts: { exposeRange?: boolean; ignoreRange?: boolean } = {},
) {
  const calls: string[] = [];
  const fetcher = (async (_url: string, init?: RequestInit) => {
    const range = (init?.headers as Record<string, string>).Range;
    calls.push(range);
    if (opts.ignoreRange) return new Response(file.slice(), { status: 200 });
    const [, a, b] = /bytes=(\d+)-(\d+)/.exec(range)!;
    const start = Number(a);
    if (start >= file.length) return new Response(null, { status: 416 });
    const end = Math.min(Number(b), file.length - 1);
    const headers: Record<string, string> = {};
    if (opts.exposeRange !== false)
      headers['content-range'] = `bytes ${start}-${end}/${file.length}`;
    return new Response(file.slice(start, end + 1), { status: 206, headers });
  }) as typeof fetch;
  return { fetcher, calls };
}

const file = gguf({
  kvs: 0,
  tensors: [{ name: 'w', shape: [8], type: 0, offset: 0 }],
  dataBytes: 32,
});

describe('HttpSource', () => {
  it('reads a header over Range requests and learns the size from Content-Range', async () => {
    const { fetcher, calls } = rangeServer(file);
    const src = new HttpSource('https://example/x.gguf', { fetch: fetcher });
    expect(src.size).toBeNull();
    const h = await readGgufHeader(src);
    expect(h.tensors[0].name).toBe('w');
    expect(src.size).toBe(file.length);
    expect(calls[0]).toBe('bytes=0-2097151');
  });

  it('keeps the caller-given size when Content-Range is not exposed', async () => {
    const { fetcher } = rangeServer(file, { exposeRange: false });
    const src = new HttpSource('https://example/x.gguf', { fetch: fetcher, size: 1234 });
    await src.read(0, 8);
    expect(src.size).toBe(1234);
  });

  it('returns nothing past the end, and refuses a server that ignores Range', async () => {
    const src = new HttpSource('https://example/x.gguf', { fetch: rangeServer(file).fetcher });
    expect((await src.read(file.length + 10, 4)).length).toBe(0);
    const ignoring = new HttpSource('https://example/x.gguf', {
      fetch: rangeServer(file, { ignoreRange: true }).fetcher,
    });
    await expect(ignoring.read(0, 4)).rejects.toThrow(/ignored the Range header/);
  });

  it('builds Hub resolve URLs', () => {
    expect(hfResolveUrl('Qwen/Qwen3-0.6B-GGUF', 'Qwen3-0.6B-Q8_0.gguf')).toBe(
      'https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q8_0.gguf',
    );
    expect(hfResolveUrl('a/b', 'sub dir/x#1.gguf', 'refs/pr/1')).toBe(
      'https://huggingface.co/a/b/resolve/refs%2Fpr%2F1/sub%20dir/x%231.gguf',
    );
  });
});
