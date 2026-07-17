import { afterEach, describe, expect, it, vi } from 'vitest';

import { pollUntilDone, type ConnectStep } from '../api';

function step(over: Partial<ConnectStep> = {}): ConnectStep {
  return {
    step: null,
    user_code: null,
    verification_uri: null,
    interval: null,
    expires_in: null,
    authorize_url: null,
    fields: [],
    connected: false,
    account: null,
    pending: false,
    error: null,
    ...over,
  };
}

/** Stubs `fetch` with a scripted sequence of poll responses. */
function scriptFetch(responses: (ConnectStep | Error)[]) {
  const calls = { n: 0 };
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      const next = responses[Math.min(calls.n, responses.length - 1)];
      calls.n += 1;
      if (next instanceof Error) throw next;
      return new Response(JSON.stringify(next), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }),
  );
  return calls;
}

// Time and sleep are injected so the polling loop runs instantly.
const fakeClock = () => {
  let t = 0;
  return {
    now: () => t,
    sleep: async (ms: number) => {
      t += ms;
    },
  };
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('pollUntilDone', () => {
  it('polls until the flow connects', async () => {
    const calls = scriptFetch([
      step({ pending: true }),
      step({ pending: true }),
      step({ connected: true }),
    ]);
    const clock = fakeClock();

    const result = await pollUntilDone('github', { intervalS: 1, ...clock });

    expect(result.connected).toBe(true);
    expect(calls.n).toBe(3);
  });

  it('returns a provider error immediately', async () => {
    scriptFetch([step({ error: 'access_denied' })]);
    const clock = fakeClock();

    const result = await pollUntilDone('github', { intervalS: 1, ...clock });

    expect(result.error).toBe('access_denied');
  });

  it('stops at expires_in rather than polling forever', async () => {
    // The backend forgets the flow at its TTL, so a client that kept going would spin
    // against a permanent error.
    const calls = scriptFetch([step({ pending: true })]);
    const clock = fakeClock();

    const result = await pollUntilDone('github', { intervalS: 5, expiresInS: 20, ...clock });

    expect(result.connected).toBe(false);
    expect(result.error).toContain('timed out');
    expect(calls.n).toBeLessThanOrEqual(4);
  });

  it('keeps trying through a transport failure', async () => {
    // The backend may be restarting mid-flow; that shouldn't kill the sign-in.
    const calls = scriptFetch([new Error('network down'), step({ connected: true })]);
    const clock = fakeClock();

    const result = await pollUntilDone('github', { intervalS: 1, ...clock });

    expect(result.connected).toBe(true);
    expect(calls.n).toBe(2);
  });

  it('gives up on a transport failure once the deadline passes', async () => {
    scriptFetch([new Error('network down')]);
    const clock = fakeClock();

    const result = await pollUntilDone('github', { intervalS: 5, expiresInS: 10, ...clock });

    expect(result.error).toContain('timed out');
  });

  it('stops when aborted', async () => {
    scriptFetch([step({ pending: true })]);
    const clock = fakeClock();
    const controller = new AbortController();
    controller.abort();

    const result = await pollUntilDone('github', {
      intervalS: 1,
      signal: controller.signal,
      ...clock,
    });

    expect(result.error).toBe('cancelled');
  });
});
