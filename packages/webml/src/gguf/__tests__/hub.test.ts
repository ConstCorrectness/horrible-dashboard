import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { inspectHubGguf, listRepoGgufs } from '../hub';
import { ggufModelId, parseGgufModelId } from '../store';

const tinyLlama = new Uint8Array(
  readFileSync(new URL('../../../kernel-tests/fixtures/tiny-llama-mixed.gguf', import.meta.url)),
);
const tinyQwen3 = new Uint8Array(
  readFileSync(new URL('./fixtures/tiny-qwen3.gguf', import.meta.url)),
);

/** Serves Range requests for `files` by path suffix, like the Hub's resolve URLs. */
function hub(files: Record<string, Uint8Array>): typeof fetch {
  return (async (url: string, init?: RequestInit) => {
    const name = Object.keys(files).find((f) => url.endsWith(f));
    if (!name) return new Response('not found', { status: 404 });
    const bytes = files[name];
    const [, a, b] = /bytes=(\d+)-(\d+)/.exec((init?.headers as Record<string, string>).Range)!;
    const start = Number(a);
    const end = Math.min(Number(b), bytes.length - 1);
    return new Response(bytes.slice(start, end + 1), {
      status: 206,
      headers: { 'content-range': `bytes ${start}-${end}/${bytes.length}` },
    });
  }) as typeof fetch;
}

describe('GGUF model ids', () => {
  it('round-trip repo and file, including a file in a folder', () => {
    const id = ggufModelId('org/repo', 'Q4_K_M/model-Q4_K_M.gguf');
    expect(id).toBe('gguf:org/repo/Q4_K_M/model-Q4_K_M.gguf');
    expect(parseGgufModelId(id)).toEqual({ repo: 'org/repo', file: 'Q4_K_M/model-Q4_K_M.gguf' });
  });

  it('refuse ids that are not a Hub GGUF path', () => {
    for (const bad of [
      'org/repo',
      'gguf:org/repo',
      'gguf:org/repo/model.bin',
      'gguf:org/../x.gguf',
    ]) {
      expect(parseGgufModelId(bad), bad).toBeNull();
    }
  });
});

describe('listRepoGgufs', () => {
  it("lists a repo's .gguf files with their sizes, marking projectors", async () => {
    const fetcher = (async () =>
      Response.json([
        { type: 'file', path: 'README.md', size: 10 },
        { type: 'file', path: 'm-Q8_0.gguf', size: 135, lfs: { size: 386_000_000 } },
        { type: 'file', path: 'mmproj-m-f16.gguf', size: 200 },
        { type: 'directory', path: 'sub' },
      ])) as unknown as typeof fetch;
    expect(await listRepoGgufs('org/m', fetcher)).toEqual([
      { path: 'm-Q8_0.gguf', size: 386_000_000, isProjector: false },
      { path: 'mmproj-m-f16.gguf', size: 200, isProjector: true },
    ]);
  });
});

describe('inspectHubGguf', () => {
  it('reads a runnable file from its header alone', async () => {
    const got = await inspectHubGguf('org/tiny', 'tiny.gguf', hub({ 'tiny.gguf': tinyLlama }));
    expect(got).toEqual({
      id: 'gguf:org/tiny/tiny.gguf',
      ok: true,
      reasons: [],
      arch: 'llama',
      name: 'tiny-llama-mixed',
      // Its F16 tensors (attn_v, ffn_down) outweigh the Q8_0 and Q4_0 ones.
      quant: 'F16',
      contextLength: 64,
      size: tinyLlama.length,
      thinking: false,
      tools: false,
    });
  });

  it('says why a file cannot run', async () => {
    const got = await inspectHubGguf('org/q', 'q.gguf', hub({ 'q.gguf': tinyQwen3 }));
    expect(got.ok).toBe(false);
    expect(got.reasons).toContain('architecture "qwen3" is not supported (supported: llama)');
  });
});
