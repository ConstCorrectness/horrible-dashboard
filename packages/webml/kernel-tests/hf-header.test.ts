/**
 * The 6.0 exit check against a real model: read a Hub GGUF's header in the browser
 * over Range requests, and check its layout fills the file.
 *
 * Opt-in, because it needs the network:
 *
 *   WEBML_HF_HEADER=unsloth/Qwen3-0.6B-GGUF/Qwen3-0.6B-Q4_K_M.gguf pnpm test:kernels
 */
import { describe, expect, inject, it } from 'vitest';

import { layoutProblems, readGgufHeader } from '../src/gguf/parse';
import { HttpSource, hfResolveUrl } from '../src/gguf/source';

declare module 'vitest' {
  interface ProvidedContext {
    hfHeader: string;
  }
}

const spec = inject('hfHeader');

describe.skipIf(!spec)('a real Hub GGUF', () => {
  it('parses from Range reads, and its tensors fill the data section', async () => {
    const parts = spec.split('/');
    const repo = parts.slice(0, 2).join('/');
    const file = parts.slice(2).join('/');
    const src = new HttpSource(hfResolveUrl(repo, file));
    const h = await readGgufHeader(src);

    expect(h.tensors.length).toBeGreaterThan(0);
    expect(typeof h.metadata['general.architecture']).toBe('string');
    expect(layoutProblems(h)).toEqual([]);

    // Known from Content-Range. If the CDN stops exposing it to script, this fails
    // here, and the picker has to pass the tree API's size instead.
    const size = src.size;
    expect(size, 'Content-Range not readable cross-origin').not.toBeNull();
    const last = h.tensors.reduce((a, b) => (b.offset > a.offset ? b : a));
    const end = h.dataOffset + last.offset + (last.bytes ?? 0);
    expect(size! - end).toBeGreaterThanOrEqual(0);
    expect(size! - end).toBeLessThan(h.alignment);
  });
});
