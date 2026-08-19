import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { accountStore, refreshAccount } from '../account-store';
import {
  connectorById,
  connectorsStore,
  onConnectRequested,
  refreshConnectors,
  requestConnect,
} from '../connectors/store';

/** One scripted JSON response for every request. */
function respond(body: unknown) {
  vi.stubGlobal('fetch', () =>
    Promise.resolve({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    } as unknown as Response),
  );
}

function failRequests() {
  vi.stubGlobal('fetch', () => Promise.reject(new Error('backend down')));
}

beforeEach(() => {
  accountStore.reset();
  connectorsStore.reset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('accountStore', () => {
  it('publishes the signed-in account to every subscriber', async () => {
    respond({
      signed_in: true,
      account_id: 'acc-1',
      display_name: 'octocat',
      username: 'OCTO',
      server_url: 'wss://games.example',
    });

    const seen: number[] = [];
    const offA = accountStore.subscribe(() => seen.push(1));
    const offB = accountStore.subscribe(() => seen.push(2));

    await refreshAccount();

    expect(accountStore.getState()).toMatchObject({
      signedIn: true,
      server: 'wss://games.example',
      phase: 'ready',
      account: { id: 'acc-1', display_name: 'octocat', handle: 'OCTO' },
    });
    // Both surfaces moved on the same fetch — the point of sharing the store.
    expect(seen).toContain(1);
    expect(seen).toContain(2);
    offA();
    offB();
  });

  it('a backend blip does not present as a sign-out', async () => {
    // Otherwise a momentary failure throws a signed-in user back to a sign-in
    // screen, and the sign-in they then do is against a session they already had.
    respond({ signed_in: true, account_id: 'acc-1', display_name: 'octocat' });
    await refreshAccount();

    failRequests();
    await refreshAccount();

    expect(accountStore.getState().phase).toBe('unavailable');
    expect(accountStore.getState().signedIn).toBe(true);
    expect(accountStore.getState().account?.display_name).toBe('octocat');
  });

  it('shares one request between concurrent callers', async () => {
    const fetchSpy = vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        headers: new Headers({ 'content-type': 'application/json' }),
        json: () => Promise.resolve({ signed_in: false }),
        text: () => Promise.resolve('{"signed_in":false}'),
      } as unknown as Response),
    );
    vi.stubGlobal('fetch', fetchSpy);

    await Promise.all([refreshAccount(), refreshAccount(), refreshAccount()]);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});

describe('connectorsStore', () => {
  it('caches the list and looks connectors up by id', async () => {
    respond({
      connectors: [
        { id: 'github', label: 'GitHub', connected: true, scopes: [], granted_scopes: [] },
        { id: 'google', label: 'Google', connected: false, scopes: [], granted_scopes: [] },
      ],
    });

    await refreshConnectors();

    expect(connectorsStore.getState().phase).toBe('ready');
    expect(connectorById('github')?.connected).toBe(true);
    expect(connectorById('google')?.connected).toBe(false);
    // Not "false": an unknown id and a disconnected one are different answers.
    expect(connectorById('nope')).toBeUndefined();
  });

  it('reports unavailable rather than an empty list when the backend is down', async () => {
    failRequests();
    await refreshConnectors();
    expect(connectorsStore.getState().phase).toBe('unavailable');
  });
});

describe('requestConnect', () => {
  it('reaches subscribers and stops after unsubscribe', () => {
    const asked: string[] = [];
    const off = onConnectRequested((id) => asked.push(id));
    requestConnect('github');
    off();
    requestConnect('google');
    expect(asked).toEqual(['github']);
  });

  it('is safe with no listener attached', () => {
    // A pane may ask before the home view has ever mounted; that must not throw.
    expect(() => requestConnect('github')).not.toThrow();
  });
});
