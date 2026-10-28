// The shared socket under the desktop shell waits for the backend's origin.
//
// Modules subscribe while the backend is still booting; dialing then aimed at the
// page's own origin (`ws://horrible/ws` under `app://`), failed, and grew the
// reconnect backoff for the whole boot — the socket opened seconds after the
// backend was ready. Caught by scripts/desktop-smoke.mjs against the real app.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

class FakeSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static urls: string[] = [];
  readyState = FakeSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((e: unknown) => void) | null = null;
  constructor(url: string) {
    FakeSocket.urls.push(url);
  }
  send(): void {}
  close(): void {}
}

beforeEach(() => {
  vi.resetModules();
  FakeSocket.urls = [];
  vi.stubGlobal('WebSocket', FakeSocket);
  vi.stubGlobal('window', {
    location: { protocol: 'app:', host: 'horrible' },
    horribleDesktop: { shell: 'electron', platform: 'linux', invoke: vi.fn(), listen: vi.fn() },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ws under the desktop shell', () => {
  it('does not dial until the backend origin is known, then dials it', async () => {
    const ws = await import('../ws');
    const origin = await import('../origin');
    ws.onSocketOpen(() => {});
    expect(FakeSocket.urls).toEqual([]);
    origin.initBackendOrigin('http://127.0.0.1:8123');
    expect(FakeSocket.urls).toEqual(['ws://127.0.0.1:8123/ws']);
  });

  it('opens nothing on origin arrival if nobody asked for the socket', async () => {
    await import('../ws');
    const origin = await import('../origin');
    origin.initBackendOrigin('http://127.0.0.1:8123');
    expect(FakeSocket.urls).toEqual([]);
  });
});
