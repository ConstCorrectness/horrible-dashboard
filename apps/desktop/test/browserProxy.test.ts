import http from 'node:http';
import net from 'node:net';

import { afterEach, describe, expect, it } from 'vitest';

import {
  type BrowserProxy,
  LocalAccess,
  type Lookup,
  embeddedV4,
  isPublicAddress,
  requestAllowed,
  resolveChecked,
  splitAuthority,
  startBrowserProxy,
} from '../src/browserProxy';

describe('address classification', () => {
  it('refuses everything that is not the public internet', () => {
    for (const ip of [
      '127.0.0.1',
      '10.1.2.3',
      '172.16.0.1',
      '192.168.1.1',
      '169.254.169.254',
      '100.64.0.1', // CGNAT / Tailscale
      '100.127.255.255',
      '0.0.0.0',
      '224.0.0.1',
      '255.255.255.255',
      '::1',
      '::',
      'fd00::1',
      'fe80::1',
      'fe80::1%12',
      'ff02::1',
      '::ffff:127.0.0.1',
      '::ffff:7f00:1',
      '64:ff9b::a00:1', // NAT64 of 10.0.0.1
      '2002:c0a8:101::1', // 6to4 of 192.168.1.1
    ]) {
      expect(isPublicAddress(ip), ip).toBe(false);
    }
  });

  it('allows public addresses in both families', () => {
    for (const ip of [
      '8.8.8.8',
      '1.1.1.1',
      '100.128.0.1',
      '2606:4700:4700::1111',
      '::ffff:8.8.8.8',
    ]) {
      expect(isPublicAddress(ip), ip).toBe(true);
    }
  });

  it('is never fooled by a name', () => {
    expect(isPublicAddress('localhost')).toBe(false);
    expect(isPublicAddress('example.com')).toBe(false);
  });

  it('finds the IPv4 address inside IPv6 spellings', () => {
    expect(embeddedV4('::ffff:10.0.0.1')).toBe('10.0.0.1');
    expect(embeddedV4('64:ff9b::808:808')).toBe('8.8.8.8');
    expect(embeddedV4('2001:db8::1')).toBeNull();
  });

  it('splits CONNECT authorities', () => {
    expect(splitAuthority('example.com:443')).toEqual({ host: 'example.com', port: 443 });
    expect(splitAuthority('[::1]:5173')).toEqual({ host: '::1', port: 5173 });
    expect(splitAuthority('example.com')).toBeNull();
    expect(splitAuthority('a:0')).toBeNull();
  });
});

const fixed =
  (table: Record<string, string[]>): Lookup =>
  async (host) =>
    (table[host] ?? []).map((address) => ({ address, family: net.isIP(address) }));

describe('local access', () => {
  it('refuses a name that resolves local, even beside a public address', async () => {
    const access = new LocalAccess();
    const lookup = fixed({
      'rebind.example': ['93.184.216.34', '127.0.0.1'],
      'ok.example': ['93.184.216.34'],
    });
    await expect(resolveChecked('rebind.example', access, lookup)).rejects.toThrow(/local address/);
    await expect(resolveChecked('ok.example', access, lookup)).resolves.toBe('93.184.216.34');
    await expect(
      resolveChecked('localhost', access, fixed({ localhost: ['::1'] })),
    ).rejects.toThrow();
    await expect(resolveChecked('192.168.0.1', access)).rejects.toThrow();
  });

  it('lets a granted host through, once claimed by a pane', async () => {
    let now = 0;
    const access = new LocalAccess(() => now);
    const lookup = fixed({ localhost: ['127.0.0.1'] });
    access.grant('http://localhost:5173/');
    // Not until a pane claims it: a grant alone opens nothing.
    await expect(resolveChecked('localhost', access, lookup)).rejects.toThrow();
    access.claim('pane-a', 'http://localhost:5173/app');
    await expect(resolveChecked('localhost', access, lookup)).resolves.toBe('127.0.0.1');
    // The grant was used up.
    access.claim('pane-b', 'http://localhost:5173/');
    expect(access.allows('pane-b', 'localhost')).toBe(false);
    // An expired grant opens nothing.
    access.grant('http://10.0.0.5/');
    now += 60_000;
    access.claim('pane-b', 'http://10.0.0.5/');
    expect(access.allows('pane-b', '10.0.0.5')).toBe(false);
    access.forget('pane-a');
    expect(access.allowsAny('localhost')).toBe(false);
  });

  it("keeps one pane's unlock from serving another pane or a page it moved to", () => {
    const access = new LocalAccess();
    access.grant('http://localhost:5173/');
    access.claim('a', 'http://localhost:5173/');
    const req = (pane: string, pageUrl: string | null, mainFrame = false) =>
      requestAllowed(access, { url: 'http://localhost:5173/api', pane, pageUrl, mainFrame });
    expect(req('a', 'http://localhost:5173/')).toBe(true);
    expect(req('a', 'https://evil.example/')).toBe(false);
    expect(req('a', 'https://evil.example/', true)).toBe(true);
    expect(req('b', 'http://localhost:5173/')).toBe(false);
    // A public host somebody typed is not fenced off from the other panes.
    access.grant('https://example.com/');
    access.claim('a', 'https://example.com/');
    expect(
      requestAllowed(access, {
        url: 'https://example.com/x',
        pane: 'b',
        pageUrl: null,
        mainFrame: false,
      }),
    ).toBe(true);
  });
});

describe('the proxy', () => {
  let proxy: BrowserProxy | null = null;
  let origin: http.Server | null = null;
  afterEach(async () => {
    await proxy?.close();
    await new Promise((r) => (origin ? origin.close(r) : r(null)));
    proxy = origin = null;
  });

  /** An origin server on loopback that answers with the Host header it got. */
  async function startOrigin(): Promise<number> {
    origin = http.createServer((req, res) => res.end(`host=${req.headers.host}`));
    await new Promise<void>((r) => origin!.listen(0, '127.0.0.1', r));
    return (origin.address() as net.AddressInfo).port;
  }

  function viaProxy(port: number, url: string): Promise<{ status: number; body: string }> {
    return new Promise((resolve, reject) => {
      const req = http.request({ host: '127.0.0.1', port, path: url, method: 'GET' }, (res) => {
        let body = '';
        res.on('data', (d) => (body += String(d)));
        res.on('end', () => resolve({ status: res.statusCode ?? 0, body }));
      });
      req.on('error', reject);
      req.end();
    });
  }

  function connectVia(port: number, authority: string): Promise<string> {
    return new Promise((resolve, reject) => {
      const s = net.connect(port, '127.0.0.1', () =>
        s.write(`CONNECT ${authority} HTTP/1.1\r\nHost: ${authority}\r\n\r\n`),
      );
      s.once('data', (d) => {
        resolve(String(d).split('\r\n')[0] ?? '');
        s.destroy();
      });
      s.on('error', reject);
    });
  }

  it('refuses a public-looking name that resolves to loopback, both ways in', async () => {
    const port = await startOrigin();
    const access = new LocalAccess();
    proxy = await startBrowserProxy(access, fixed({ 'rebind.example': ['127.0.0.1'] }));
    const plain = await viaProxy(proxy.port, `http://rebind.example:${port}/`);
    expect(plain.status).toBe(403);
    expect(await connectVia(proxy.port, `rebind.example:${port}`)).toMatch(/^HTTP\/1\.1 403/);
  });

  it('connects to the address it checked once the host is unlocked', async () => {
    const port = await startOrigin();
    const access = new LocalAccess();
    access.grant(`http://dev.test:${port}/`);
    access.claim('pane', `http://dev.test:${port}/`);
    proxy = await startBrowserProxy(access, fixed({ 'dev.test': ['127.0.0.1'] }));
    const plain = await viaProxy(proxy.port, `http://dev.test:${port}/`);
    expect(plain).toEqual({ status: 200, body: `host=dev.test:${port}` });
    expect(await connectVia(proxy.port, `dev.test:${port}`)).toMatch(/^HTTP\/1\.1 200/);
  });
});
