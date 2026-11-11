import { describe, expect, it } from 'vitest';

import { browserAgentTools } from '../agentTools';
import { isLocalUrl } from '../local-url';

describe('local URLs by name', () => {
  it('knows this machine and the private network', () => {
    for (const url of [
      'http://localhost:5173/',
      'http://app.localhost/',
      'http://127.0.0.1:8000/api',
      'http://10.0.0.5/',
      'http://192.168.1.1/admin',
      'http://172.20.1.1/',
      'http://100.100.1.1/', // CGNAT / Tailscale
      'http://169.254.169.254/latest/meta-data',
      'http://[::1]:3000/',
      'http://[fd00::1]/',
      'http://[::ffff:127.0.0.1]/',
      'http://router/',
      'http://nas.local/',
      'http://printer.home.arpa/',
      'http://localhost./',
    ]) {
      expect(isLocalUrl(url), url).toBe(true);
    }
  });

  it('leaves the public web alone', () => {
    for (const url of [
      'https://example.com/',
      'https://8.8.8.8/',
      'https://100.128.0.1/',
      'https://[2606:4700:4700::1111]/',
      'https://localhost.example.com/',
      'not a url',
    ]) {
      expect(isLocalUrl(url), url).toBe(false);
    }
  });
});

describe('browser.open gate', () => {
  const open = browserAgentTools.find((t) => t.name === 'browser.open')!;

  it('asks about a local address under its own specifier', async () => {
    await expect(Promise.resolve(open.gate!({ url: 'http://localhost:5173/' }))).resolves.toEqual({
      sideEffect: true,
      specifier: 'local:http://localhost:5173/',
      rule: null,
    });
  });

  it('keeps the plain URL specifier for the web', async () => {
    await expect(Promise.resolve(open.gate!({ url: 'https://example.com/' }))).resolves.toEqual({
      sideEffect: true,
      specifier: 'https://example.com/',
    });
  });
});
