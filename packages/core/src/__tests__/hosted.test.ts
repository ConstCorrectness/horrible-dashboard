import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  _resetHostedForTests,
  createActivityReporter,
  hubSignedOut,
  isHosted,
  probeHub,
  reportActivity,
  socketUrl,
  waitForInstance,
} from '../hosted';
import { initBackendOrigin, initWsOrigin } from '../origin';

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

const ALICE = { id: 'acc-alice', handle: 'alice', display_name: 'Alice' };

afterEach(() => {
  _resetHostedForTests();
  initBackendOrigin(null);
  initWsOrigin(null);
  vi.unstubAllGlobals();
});

describe('probeHub', () => {
  it('reads a 401 JSON answer as signed out', async () => {
    const probe = await probeHub(async () => json(401, { signed_in: false }));
    expect(probe).toEqual({ mode: 'signedOut' });
    expect(isHosted()).toBe(false);
    // ws.ts reads this to stay closed on the login screen.
    expect(hubSignedOut()).toBe(true);
  });

  it('reads a signed-in answer and remembers the account', async () => {
    const probe = await probeHub(async () => json(200, { signed_in: true, account: ALICE }));
    expect(probe).toEqual({ mode: 'signedIn', account: ALICE });
    expect(isHosted()).toBe(true);
  });

  it('treats a plain backend 404 as local', async () => {
    const probe = await probeHub(async () => json(404, { detail: 'Not Found' }));
    expect(probe).toEqual({ mode: 'local' });
  });

  it("treats Vite's index.html fallback (200, not JSON) as local", async () => {
    const probe = await probeHub(
      async () =>
        new Response('<!doctype html>', { status: 200, headers: { 'content-type': 'text/html' } }),
    );
    expect(probe).toEqual({ mode: 'local' });
    expect(isHosted()).toBe(false);
  });

  it('treats an unreachable backend as local', async () => {
    const probe = await probeHub(async () => {
      throw new TypeError('failed to fetch');
    });
    expect(probe).toEqual({ mode: 'local' });
  });
});

describe('waitForInstance', () => {
  const noSleep = async () => {};

  it('polls through 503 starting until healthy', async () => {
    const answers = [json(503, { starting: true }), json(503, { starting: true }), json(200, {})];
    const fetchImpl = vi.fn(async () => answers.shift() ?? json(200, {}));
    await expect(waitForInstance(60_000, fetchImpl, noSleep)).resolves.toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  it('gives up at once when the session has ended', async () => {
    const fetchImpl = vi.fn(async () => json(401, {}));
    await expect(waitForInstance(60_000, fetchImpl, noSleep)).resolves.toBe(false);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('gives up after the timeout', async () => {
    const fetchImpl = vi.fn(async () => json(503, { starting: true }));
    await expect(waitForInstance(0, fetchImpl, noSleep)).resolves.toBe(false);
  });
});

describe('socketUrl', () => {
  it('is the plain URL when not hosted', async () => {
    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:5173', protocol: 'http:', host: 'localhost:5173' },
    });
    const fetchImpl = vi.fn();
    await expect(socketUrl('/ws', fetchImpl)).resolves.toBe('ws://localhost:5173/ws');
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('is the plain URL when hosted on the same origin (the cookie rides the upgrade)', async () => {
    vi.stubGlobal('window', {
      location: { origin: 'https://dash.example', protocol: 'https:', host: 'dash.example' },
    });
    await probeHub(async () => json(200, { signed_in: true, account: ALICE }));
    const fetchImpl = vi.fn();
    await expect(socketUrl('/ws', fetchImpl)).resolves.toBe('wss://dash.example/ws');
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('adds a single-use ticket when the socket goes to another origin', async () => {
    vi.stubGlobal('window', {
      location: { origin: 'https://dash.vercel.app', protocol: 'https:', host: 'dash.vercel.app' },
    });
    initWsOrigin('https://hub.example');
    await probeHub(async () => json(200, { signed_in: true, account: ALICE }));
    const fetchImpl = vi.fn(async () => json(200, { ticket: 'a b' }));
    await expect(socketUrl('/ws', fetchImpl)).resolves.toBe('wss://hub.example/ws?ticket=a%20b');
    // Fetched same-origin, where the rewrite to the hub carries the cookie.
    expect(fetchImpl).toHaveBeenCalledWith('/hub/ws-ticket', {
      method: 'POST',
      credentials: 'include',
    });
  });
});

describe('activity reporter', () => {
  const flush = () => new Promise((r) => setTimeout(r, 0));

  it('posts at most once per interval however much input arrives', async () => {
    let t = 0;
    const post = vi.fn(async () => ({ resumed: false }));
    const reporter = createActivityReporter({ post, now: () => t, intervalMs: 60_000 });
    for (let i = 0; i < 50; i++) reporter.onInput();
    await flush();
    expect(post).toHaveBeenCalledTimes(1);
    t = 59_999;
    reporter.onInput();
    await flush();
    expect(post).toHaveBeenCalledTimes(1);
    t = 60_000;
    reporter.onInput();
    await flush();
    expect(post).toHaveBeenCalledTimes(2);
  });

  it('says so when input woke a paused instance', async () => {
    const onResumed = vi.fn();
    const reporter = createActivityReporter({
      post: async () => ({ resumed: true }),
      onResumed,
      now: () => 0,
    });
    reporter.onInput();
    await flush();
    expect(onResumed).toHaveBeenCalledTimes(1);
  });

  it('retries on the next input when a report was not delivered', async () => {
    let fail = true;
    const post = vi.fn(async () => {
      if (fail) throw new TypeError('offline');
      return { resumed: false };
    });
    const reporter = createActivityReporter({ post, now: () => 1000 });
    reporter.onInput();
    await flush();
    fail = false;
    reporter.onInput();
    await flush();
    expect(post).toHaveBeenCalledTimes(2);
  });

  it('reportActivity posts to the hub and tolerates a plain backend', async () => {
    const fetchImpl = vi.fn(async () => json(200, { resumed: true }));
    await expect(reportActivity(fetchImpl)).resolves.toEqual({ resumed: true });
    expect(fetchImpl).toHaveBeenCalledWith('/hub/activity', {
      method: 'POST',
      credentials: 'include',
    });
    await expect(reportActivity(async () => json(404, {}))).resolves.toBeNull();
  });
});
