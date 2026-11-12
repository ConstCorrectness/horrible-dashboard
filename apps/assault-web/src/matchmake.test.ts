import { describe, expect, it } from 'vitest';

import { findMatch } from './matchmake';

const answer = (status: number, body: unknown) => async () =>
  new Response(JSON.stringify(body), { status });

describe('findMatch', () => {
  it('returns the room the server chose', async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const found = await findMatch(
      '/api/hassault/matchmake',
      { map: 'hd_dust2' },
      async (url, init) => {
        calls.push({ url, init });
        return new Response(
          JSON.stringify({
            id: 'ab12cd34',
            map: 'hd_dust2',
            mode: 'tdm',
            opened: true,
            playerCount: 0,
          }),
        );
      },
    );
    expect(found).toEqual({ id: 'ab12cd34', map: 'hd_dust2', mode: 'tdm', opened: true });
    expect(calls[0].init?.method).toBe('POST');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ map: 'hd_dust2', mode: '' });
  });

  it('is null, not an error, when the server cannot say', async () => {
    // An older server has no such route; a full one refuses.
    expect(await findMatch('/x', {}, answer(404, { detail: 'Not Found' }))).toBeNull();
    expect(await findMatch('/x', {}, answer(409, { detail: 'this server is full' }))).toBeNull();
    expect(
      await findMatch('/x', {}, async () => {
        throw new Error('offline');
      }),
    ).toBeNull();
  });

  it('does not trust an answer with no room in it', async () => {
    expect(await findMatch('/x', {}, answer(200, { map: 'hd_dust2' }))).toBeNull();
    expect(await findMatch('/x', {}, answer(200, { id: 'a' }))).toBeNull();
    expect(await findMatch('/x', {}, answer(200, null))).toBeNull();
    expect(await findMatch('/x', {}, answer(200, { id: 'a', map: 'hd_pit' }))).toEqual({
      id: 'a',
      map: 'hd_pit',
      mode: 'dm',
      opened: false,
    });
  });
});
