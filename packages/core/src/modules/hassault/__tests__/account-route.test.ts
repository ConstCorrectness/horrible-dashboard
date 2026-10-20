/**
 * The account calls' two routes: through the node (the dashboard, the native
 * client) or straight to the game server with a bearer token (the standalone
 * browser game, which has no node). The paths differ between the two — the
 * node's `/hassault/skins/inventory` is the server's `/hassault/account/inventory`
 * — so a call that missed the switch would 404 on the server, not fail loudly.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../../../api';
import {
  equipSkin,
  executeTradeUp,
  getSkinInventory,
  getWallet,
  listMarket,
  listSkinCatalog,
  offerTrade,
  routeAccountToGameServer,
} from '../api';

interface Call {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}

function stubFetch(answer: (url: string) => { status: number; body: unknown }): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    calls.push({
      url,
      method: init?.method ?? 'GET',
      headers: { ...((init?.headers as Record<string, string>) ?? {}) },
      body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
    });
    const { status, body } = answer(url);
    return new Response(JSON.stringify(body), { status });
  });
  return calls;
}

afterEach(() => {
  routeAccountToGameServer(null);
  vi.unstubAllGlobals();
});

describe('account calls routed to the game server', () => {
  it('uses the server paths and the current token', async () => {
    let token: string | null = 'tok-1';
    routeAccountToGameServer(() => token);
    const calls = stubFetch(() => ({ status: 200, body: [] }));

    await getSkinInventory();
    token = 'tok-2';
    await getWallet();

    expect(calls.map((c) => c.url)).toEqual([
      '/api/hassault/account/inventory',
      '/api/hassault/account/wallet',
    ]);
    // Read per call, so a sign-in after boot needs no re-binding.
    expect(calls[0]!.headers.Authorization).toBe('Bearer tok-1');
    expect(calls[1]!.headers.Authorization).toBe('Bearer tok-2');
  });

  it('keeps equip query params and names the trade-up body', async () => {
    routeAccountToGameServer(() => 't');
    const calls = stubFetch(() => ({ status: 200, body: { ok: true } }));

    await equipSkin('inst 1', 'ct');
    await executeTradeUp(['a', 'b']);
    await offerTrade({ to: 'p', give: ['a'], want: [] });

    expect(calls[0]).toMatchObject({
      url: '/api/hassault/account/equip?instance_id=inst%201&side=ct',
      method: 'POST',
    });
    expect(calls[1]).toMatchObject({
      url: '/api/hassault/account/tradeup',
      body: { instanceIds: ['a', 'b'] },
    });
    expect(calls[2]).toMatchObject({ url: '/api/hassault/account/trades', method: 'POST' });
  });

  it('reads the market on its public path', async () => {
    routeAccountToGameServer(() => 't');
    const calls = stubFetch(() => ({ status: 200, body: [] }));
    await listMarket('ak_x');
    expect(calls[0]!.url).toBe('/api/hassault/market?skin_id=ak_x');
  });

  it('surfaces the server detail as an ApiError with its status', async () => {
    routeAccountToGameServer(() => null);
    stubFetch(() => ({ status: 401, body: { detail: 'sign in first' } }));
    const err = await getSkinInventory().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 401, message: 'sign in first' });
  });

  it('builds the catalog from the cases on a server without one', async () => {
    routeAccountToGameServer(() => 't');
    const skin = (id: string) => ({ id, name: id, weaponId: 'ak' });
    stubFetch((url) =>
      url.endsWith('/skins/catalog')
        ? { status: 404, body: { detail: 'Not Found' } }
        : {
            status: 200,
            body: [
              { id: 'c1', contents: [skin('a'), skin('b')] },
              { id: 'c2', contents: [skin('b'), skin('c')] },
            ],
          },
    );
    const catalog = await listSkinCatalog();
    expect(catalog.map((s) => s.id)).toEqual(['a', 'b', 'c']);
  });
});

describe('account calls through the node (the default)', () => {
  it('keeps the node paths and sends no bearer header', async () => {
    const calls = stubFetch(() => ({ status: 200, body: [] }));
    await getSkinInventory();
    expect(calls[0]!.url).toBe('/api/hassault/skins/inventory');
    expect(calls[0]!.headers.Authorization).toBeUndefined();
  });
});
