/**
 * The NDJSON reader behind the native client's Install button.
 *
 * The thing under test is chunk boundaries. A stream is split wherever the
 * network happens to split it, never on line boundaries, so a naive
 * `JSON.parse` per chunk works perfectly against a fast local backend and
 * silently drops progress — or throws — against a slow one. Reproducing that by
 * hand is the only way to find it, because the happy path is indistinguishable.
 */
import { describe, expect, it, vi, afterEach } from 'vitest';

import { installNativeClient, type ClientInstallEvent } from '../api';

/** A response whose body arrives in exactly these pieces. */
function streamOf(chunks: string[]): Response {
  const encoder = new TextEncoder();
  let i = 0;
  const body = {
    getReader: () => ({
      read: async () =>
        i < chunks.length
          ? { done: false, value: encoder.encode(chunks[i++]) }
          : { done: true, value: undefined },
    }),
  };
  return { ok: true, status: 200, body } as unknown as Response;
}

function stub(chunks: string[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => streamOf(chunks)),
  );
}

afterEach(() => vi.unstubAllGlobals());

const LINES = [
  '{"status":"resolving","version":"0.2.0"}',
  '{"status":"downloading","total":100,"completed":50}',
  '{"status":"downloading","total":100,"completed":100}',
  '{"status":"verifying"}',
  '{"status":"done","verified":true}',
];

describe('installNativeClient', () => {
  it('reports every event when each line arrives whole', async () => {
    stub([LINES.join('\n') + '\n']);
    const seen: ClientInstallEvent[] = [];
    const done = await installNativeClient((e) => seen.push(e));

    expect(seen).toHaveLength(5);
    expect(done.status).toBe('done');
    expect(done.verified).toBe(true);
  });

  it('survives chunks that split a line down the middle', async () => {
    // The realistic case: the boundary lands inside a JSON object, and inside a
    // string literal at that.
    const whole = LINES.join('\n') + '\n';
    const chunks: string[] = [];
    for (let i = 0; i < whole.length; i += 7) chunks.push(whole.slice(i, i + 7));
    stub(chunks);

    const seen: ClientInstallEvent[] = [];
    const done = await installNativeClient((e) => seen.push(e));

    expect(seen).toHaveLength(5);
    expect(seen[2]).toEqual({ status: 'downloading', total: 100, completed: 100 });
    expect(done.status).toBe('done');
  });

  it('reads a final line that arrives without a trailing newline', async () => {
    stub([LINES.join('\n')]);
    const seen: ClientInstallEvent[] = [];
    const done = await installNativeClient((e) => seen.push(e));

    expect(seen).toHaveLength(5);
    expect(done.status).toBe('done');
  });

  it('returns the error event rather than throwing', async () => {
    stub(['{"status":"resolving"}\n{"error":"no release is published for v0.2.0"}\n']);
    const done = await installNativeClient(() => {});

    expect(done.error).toContain('no release is published');
  });

  it('reports a stream that stops mid-download instead of resolving quietly', async () => {
    // A truncated install that says nothing is exactly the state the backend
    // deletes its directory to avoid leaving behind; the caller must not be told
    // it finished.
    stub(['{"status":"downloading","total":100,"completed":10}\n']);
    const done = await installNativeClient(() => {});

    expect(done.error).toBe('the install ended without finishing');
  });
});
