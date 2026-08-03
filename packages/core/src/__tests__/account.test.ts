import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { oauthSignIn, type SignInPrompt } from '../account';

/**
 * Scripts the node's sign-in endpoints. Keys are path suffixes; each value is
 * either the JSON body to return or an Error to reject with.
 */
function scriptFetch(routes: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: string) => {
      const url = String(input);
      const key = Object.keys(routes).find((k) => url.includes(k));
      if (!key) throw new Error(`unscripted request: ${url}`);
      const body = routes[key];
      if (body instanceof Error) return Promise.reject(body);
      // A real Headers: api.ts records every response into the I/O ring buffer
      // and iterates the headers to do it, so a hand-rolled `{get}` stub blows up
      // inside instrumentation rather than in the code under test.
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Headers({ 'content-type': 'application/json' }),
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      } as unknown as Response);
    }),
  );
}

/** No pop-up, and the platform refuses to open anything — the blocked case. */
function nothingOpens() {
  vi.stubGlobal('window', { open: () => null });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('oauthSignIn', () => {
  it('keeps the blocked prompt on screen after the attempt fails', async () => {
    // The regression: the prompt used to be cleared in a `finally`, so the moment
    // the flow timed out or errored the address vanished — taking away the one
    // thing the user had just been told to go and use.
    nothingOpens();
    scriptFetch({
      '/web/start': { authorize_url: 'https://github.com/login/oauth/authorize?x=1' },
      '/web/poll': { error: 'sign-in was refused' },
    });

    const prompts: (SignInPrompt | null)[] = [];
    const run = oauthSignIn('github', (p) => prompts.push(p)).catch(() => 'failed');

    await vi.advanceTimersByTimeAsync(3000);
    await run;

    expect(prompts.at(-1)).toMatchObject({
      url: 'https://github.com/login/oauth/authorize?x=1',
      blocked: true,
    });
    // Specifically: the last thing the caller heard was NOT "clear the prompt".
    expect(prompts.at(-1)).not.toBeNull();
  });

  it('clears the prompt on success', async () => {
    nothingOpens();
    scriptFetch({
      '/web/start': { authorize_url: 'https://github.com/login/oauth/authorize?x=1' },
      '/web/poll': { signed_in: true, account: { display_name: 'octocat' } },
    });

    const prompts: (SignInPrompt | null)[] = [];
    const run = oauthSignIn('github', (p) => prompts.push(p));

    await vi.advanceTimersByTimeAsync(3000);
    await expect(run).resolves.toBe('octocat');
    expect(prompts.at(-1)).toBeNull();
  });

  it('prefer:device skips the redirect flow entirely', async () => {
    // The escape hatch: device flow opens no window, so it must be reachable
    // deliberately, not only by the redirect flow happening to fail first.
    nothingOpens();
    scriptFetch({
      '/web/start': new Error('should not be called'),
      '/github/start': {
        device_code: 'dev-1',
        user_code: 'ABCD-1234',
        verification_uri: 'https://github.com/login/device',
        interval: 1,
      },
      '/github/poll': { signed_in: true, account: { display_name: 'octocat' } },
    });

    const prompts: (SignInPrompt | null)[] = [];
    const run = oauthSignIn('github', (p) => prompts.push(p), { prefer: 'device' });

    await vi.advanceTimersByTimeAsync(5000);
    await expect(run).resolves.toBe('octocat');
    expect(prompts[0]).toMatchObject({ code: 'ABCD-1234' });
  });

  it('falls back to the device flow when the redirect flow fails before navigating', async () => {
    nothingOpens();
    scriptFetch({
      '/web/start': { error: 'no web credentials' },
      '/github/start': {
        device_code: 'dev-1',
        user_code: 'WXYZ-9999',
        verification_uri: 'https://github.com/login/device',
        interval: 1,
      },
      '/github/poll': { signed_in: true, account: { display_name: 'octocat' } },
    });

    const prompts: (SignInPrompt | null)[] = [];
    const run = oauthSignIn('github', (p) => prompts.push(p));

    await vi.advanceTimersByTimeAsync(5000);
    await expect(run).resolves.toBe('octocat');
    expect(prompts[0]).toMatchObject({ code: 'WXYZ-9999' });
  });
});
