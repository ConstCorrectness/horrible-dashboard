/** The OPFS weight store, in a real browser: download, resume, list, delete. */
import { beforeEach, describe, expect, it } from 'vitest';

import { deleteGguf, downloadGguf, listGgufs, openGguf } from '../src/gguf/store';
import f32Url from './fixtures/tiny-llama-f32.gguf?url';
import { fileServer, fixtureBytes } from './harness';

const ID = 'gguf:test/tiny/tiny-llama-f32.gguf';

async function same(file: File, bytes: Uint8Array): Promise<boolean> {
  const got = new Uint8Array(await file.arrayBuffer());
  return got.length === bytes.length && got.every((b, i) => b === bytes[i]);
}

describe('GGUF store', () => {
  beforeEach(async () => {
    for (const g of await listGgufs()) await deleteGguf(g.id);
  });

  it('downloads into OPFS and lists the complete file', async () => {
    const bytes = await fixtureBytes(f32Url);
    const { fetcher } = fileServer(bytes);
    const progress: number[] = [];
    const file = await downloadGguf(ID, 'https://hub/x', {
      fetch: fetcher,
      onProgress: (n) => progress.push(n),
    });
    expect(await same(file, bytes)).toBe(true);
    expect(progress.at(-1)).toBe(bytes.length);
    expect(await listGgufs()).toEqual([
      { id: ID, bytes: bytes.length, total: bytes.length, complete: true },
    ]);
    expect(await openGguf(ID)).not.toBeNull();
  });

  it('resumes an interrupted download from the bytes it already has', async () => {
    const bytes = await fixtureBytes(f32Url);
    const { fetcher, requests } = fileServer(bytes, { cutAfter: 100_000 });
    await expect(downloadGguf(ID, 'https://hub/x', { fetch: fetcher })).rejects.toThrow(
      /connection reset/,
    );
    // Partial: listed as incomplete, not openable.
    const [partial] = await listGgufs();
    expect(partial).toMatchObject({ id: ID, complete: false, bytes: 100_000 });
    expect(await openGguf(ID)).toBeNull();

    const file = await downloadGguf(ID, 'https://hub/x', { fetch: fetcher });
    expect(requests).toEqual([null, 'bytes=100000-']);
    expect(await same(file, bytes)).toBe(true);
  });

  it('does not download again once complete, and deletes', async () => {
    const bytes = await fixtureBytes(f32Url);
    const { fetcher, requests } = fileServer(bytes);
    await downloadGguf(ID, 'https://hub/x', { fetch: fetcher });
    await downloadGguf(ID, 'https://hub/x', { fetch: fetcher });
    expect(requests).toHaveLength(1);
    expect(await deleteGguf(ID)).toBe(true);
    expect(await listGgufs()).toEqual([]);
  });
});
