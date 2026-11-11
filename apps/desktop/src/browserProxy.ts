/**
 * The embedded browser's network fence: a loopback HTTP proxy that every request
 * from the `persist:browser` session goes through, and that refuses to connect to
 * an address on this machine or its network.
 *
 * ## Why a proxy
 *
 * A browser pane is a real browser, and since the agent drives it, a page it reads
 * can steer it: "now open http://192.168.1.1/admin". Chromium would happily load the
 * router's admin page, the dev server on :5173, or this app's own backend on :8000.
 * Checking the URL before a navigation is not enough: the name is resolved again
 * when the connection is made, so a hostile DNS server answers "public" to the check
 * and "127.0.0.1" to the connection (DNS rebinding). Here the proxy resolves the
 * name **once**, checks every address, and connects to the address it checked —
 * there is no second lookup to rebind.
 *
 * ## Local addresses the user asked for
 *
 * The user's own dev servers are a legitimate thing to browse. A local host is
 * allowed once a pane was *told* to go there by the app — the user typed it into
 * the address bar, or approved the agent's `browser.open` — and only for that pane
 * ({@link LocalAccess}). A page can never unlock one by navigating itself.
 *
 * Nothing in this file imports Electron, so the test suite runs it as plain Node.
 */
import dns from 'node:dns';
import http from 'node:http';
import net from 'node:net';

// --- address classification -------------------------------------------------

/**
 * Every range that is not the public internet: loopback, private, link-local,
 * CGNAT, multicast, documentation and reserved space, in both families. An address
 * outside all of them is "global" — what a web page may reach.
 */
const NON_GLOBAL = (() => {
  const list = new net.BlockList();
  const v4: [string, number][] = [
    ['0.0.0.0', 8], // "this network"
    ['10.0.0.0', 8],
    ['100.64.0.0', 10], // CGNAT — Tailscale lives here
    ['127.0.0.0', 8],
    ['169.254.0.0', 16], // link-local, cloud metadata (169.254.169.254)
    ['172.16.0.0', 12],
    ['192.0.0.0', 24],
    ['192.0.2.0', 24],
    ['192.88.99.0', 24],
    ['192.168.0.0', 16],
    ['198.18.0.0', 15],
    ['198.51.100.0', 24],
    ['203.0.113.0', 24],
    ['224.0.0.0', 4], // multicast
    ['240.0.0.0', 4], // reserved, and the broadcast address
  ];
  for (const [a, p] of v4) list.addSubnet(a, p, 'ipv4');
  const v6: [string, number][] = [
    ['::', 128], // unspecified
    ['::1', 128],
    ['100::', 64], // discard
    ['2001:db8::', 32], // documentation
    ['fc00::', 7], // unique local
    ['fe80::', 10], // link-local
    ['fec0::', 10], // old site-local
    ['ff00::', 8], // multicast
  ];
  for (const [a, p] of v6) list.addSubnet(a, p, 'ipv6');
  return list;
})();

/** Expand an IPv6 address into its eight 16-bit groups. */
function v6Groups(ip: string): number[] | null {
  let text = ip;
  // A dotted IPv4 tail (`::ffff:1.2.3.4`) becomes two groups.
  const dotted = /(\d+\.\d+\.\d+\.\d+)$/.exec(text);
  if (dotted?.[1]) {
    if (!net.isIPv4(dotted[1])) return null;
    const [a, b, c, d] = dotted[1].split('.').map(Number) as [number, number, number, number];
    text =
      text.slice(0, -dotted[1].length) +
      `${((a << 8) | b).toString(16)}:${((c << 8) | d).toString(16)}`;
  }
  const halves = text.split('::');
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(':') : [];
  const tail = halves.length === 2 && halves[1] ? halves[1].split(':') : [];
  const fill = halves.length === 2 ? 8 - head.length - tail.length : 0;
  const groups = [...head, ...Array<string>(Math.max(fill, 0)).fill('0'), ...tail].map((g) =>
    parseInt(g, 16),
  );
  return groups.length === 8 && groups.every((g) => g >= 0 && g <= 0xffff) ? groups : null;
}

const v4From = (hi: number, lo: number): string =>
  `${hi >> 8}.${hi & 0xff}.${lo >> 8}.${lo & 0xff}`;

/**
 * The IPv4 address an IPv6 address carries, when it is one of the forms that
 * reaches an IPv4 host: IPv4-mapped (`::ffff:a.b.c.d`), IPv4-compatible, NAT64
 * (`64:ff9b::/96`) and 6to4 (`2002::/16`). `::ffff:127.0.0.1` is loopback, however
 * global its IPv6 spelling looks.
 */
export function embeddedV4(ip: string): string | null {
  const g = v6Groups(ip);
  if (!g) return null;
  const zeros = (n: number) => g.slice(0, n).every((x) => x === 0);
  if (zeros(5) && g[5] === 0xffff) return v4From(g[6]!, g[7]!); // mapped
  if (zeros(6) && (g[6] !== 0 || g[7]! > 1)) return v4From(g[6]!, g[7]!); // compatible
  if (g[0] === 0x64 && g[1] === 0xff9b && g.slice(2, 6).every((x) => x === 0)) {
    return v4From(g[6]!, g[7]!); // NAT64
  }
  if (g[0] === 0x2002) return v4From(g[1]!, g[2]!); // 6to4
  return null;
}

/** Whether `ip` is an address on the public internet (a literal, not a name). */
export function isPublicAddress(ip: string): boolean {
  const family = net.isIP(ip);
  if (family === 4) return !NON_GLOBAL.check(ip, 'ipv4');
  if (family !== 6) return false;
  const bare = ip.replace(/%.*$/, ''); // zone id
  const inner = embeddedV4(bare);
  if (inner) return isPublicAddress(inner);
  return !NON_GLOBAL.check(bare, 'ipv6');
}

// --- who may reach a local host ---------------------------------------------

/** How long a grant waits for the navigation it was made for. */
const GRANT_TTL_MS = 30_000;

/** Lower-case, without IPv6 brackets or a trailing dot. */
export function normalizeHost(host: string): string {
  return host
    .trim()
    .toLowerCase()
    .replace(/^\[(.*)\]$/, '$1')
    .replace(/\.$/, '');
}

/** The host of an http(s) URL, normalized; null for anything else. */
export function hostOf(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
    return normalizeHost(u.hostname);
  } catch {
    return null;
  }
}

/** A name that means "this machine" without asking DNS. */
const isLoopbackName = (host: string) => host === 'localhost' || host.endsWith('.localhost');

/**
 * Which panes may reach which local hosts.
 *
 * The app grants a host (`grant`) just before it creates or navigates a pane there
 * at the user's say-so; the pane's create/navigate claims it (`claim`). A grant
 * nobody claims expires. Unlocks last as long as the pane.
 */
export class LocalAccess {
  private panes = new Map<string, Set<string>>();
  private grants = new Map<string, number>();
  /** Hosts the proxy saw resolve to a non-global address. */
  private resolvedLocal = new Set<string>();

  constructor(private readonly now: () => number = Date.now) {}

  /** The user asked for `url`: the next pane to be pointed at its host may reach it. */
  grant(url: string): void {
    const host = hostOf(url);
    if (host) this.grants.set(host, this.now() + GRANT_TTL_MS);
  }

  /** Pane `pane` is being pointed at `url`: take a pending grant for its host. */
  claim(pane: string, url: string): void {
    const host = hostOf(url);
    if (!host) return;
    const until = this.grants.get(host);
    if (until === undefined) return;
    this.grants.delete(host);
    if (until < this.now()) return;
    let hosts = this.panes.get(pane);
    if (!hosts) this.panes.set(pane, (hosts = new Set()));
    hosts.add(host);
  }

  /** The pane closed: its unlocks go with it. */
  forget(pane: string): void {
    this.panes.delete(pane);
  }

  /** Whether `pane` unlocked `host`. */
  allows(pane: string | undefined, host: string): boolean {
    return pane !== undefined && (this.panes.get(pane)?.has(normalizeHost(host)) ?? false);
  }

  /** Whether any pane unlocked `host` — the proxy cannot tell panes apart. */
  allowsAny(host: string): boolean {
    const h = normalizeHost(host);
    for (const hosts of this.panes.values()) if (hosts.has(h)) return true;
    return false;
  }

  /** The proxy found `host` local; requests to it are now checked per pane. */
  noteLocal(host: string): void {
    this.resolvedLocal.add(normalizeHost(host));
  }

  /**
   * Whether a request to `host` needs the per-pane check: a host some pane unlocked
   * that is (or resolved to) a local address. A public site a pane happened to be
   * typed into is not fenced off from the other panes.
   */
  fenced(host: string): boolean {
    const h = normalizeHost(host);
    if (!this.allowsAny(h)) return false;
    if (isLoopbackName(h) || this.resolvedLocal.has(h)) return true;
    return net.isIP(h) !== 0 && !isPublicAddress(h);
  }
}

/**
 * Whether a request made by pane `pane` (whose page is currently at `pageUrl`) to
 * `url` may go ahead, as far as local access goes. The proxy has already refused
 * local hosts nobody unlocked; this keeps one pane's unlock from serving the others,
 * and from serving a public page that pane moved on to.
 */
export function requestAllowed(
  access: LocalAccess,
  req: { url: string; pane: string | undefined; pageUrl: string | null; mainFrame: boolean },
): boolean {
  const host = hostOf(req.url);
  if (!host || !access.fenced(host)) return true;
  if (!access.allows(req.pane, host)) return false;
  if (req.mainFrame) return true;
  // A subresource: only while the page itself is on a host this pane unlocked.
  const page = req.pageUrl ? hostOf(req.pageUrl) : null;
  return page !== null && access.allows(req.pane, page);
}

// --- the proxy ---------------------------------------------------------------

export type Lookup = (host: string) => Promise<{ address: string; family: number }[]>;

const systemLookup: Lookup = (host) => dns.promises.lookup(host, { all: true, verbatim: true });

export class Refused extends Error {}

/**
 * Resolve `host` once and pick the address to connect to. Refuses a name with no
 * addresses, and one with **any** non-global address unless a pane unlocked it — a
 * name answering both public and private is a rebinding attempt or a split-horizon
 * name, and either way not something a page gets to choose between.
 */
export async function resolveChecked(
  host: string,
  access: LocalAccess,
  lookup: Lookup = systemLookup,
): Promise<string> {
  const h = normalizeHost(host);
  const addresses = net.isIP(h) ? [h] : (await lookup(h)).map((a) => a.address);
  if (addresses.length === 0) throw new Refused(`${h} has no address`);
  const local = addresses.filter((a) => !isPublicAddress(a));
  if (isLoopbackName(h) || local.length > 0) {
    access.noteLocal(h);
    if (!access.allowsAny(h)) {
      throw new Refused(`${h} is a local address; type it into the address bar to open it`);
    }
  }
  return addresses[0]!;
}

/** Split CONNECT's `host:port` (`[v6]:port` for IPv6). */
export function splitAuthority(authority: string): { host: string; port: number } | null {
  const m = /^\[([^\]]+)\]:(\d+)$/.exec(authority) ?? /^([^:]+):(\d+)$/.exec(authority);
  if (!m?.[1] || !m[2]) return null;
  const port = Number(m[2]);
  return port > 0 && port < 65536 ? { host: m[1], port } : null;
}

/** Hop-by-hop and proxy-only headers: never forwarded to the origin. */
const HOP = new Set([
  'connection',
  'keep-alive',
  'proxy-authorization',
  'proxy-authenticate',
  'proxy-connection',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
]);

export interface BrowserProxy {
  port: number;
  close(): Promise<void>;
}

/**
 * Start the proxy on a free loopback port. CONNECT carries HTTPS and WebSockets
 * (Chromium tunnels both); plain `http://` arrives as an absolute-URI request and is
 * forwarded. A refusal is a 403 — Chromium shows its tunnel/proxy error page.
 */
export function startBrowserProxy(
  access: LocalAccess,
  lookup: Lookup = systemLookup,
): Promise<BrowserProxy> {
  const server = http.createServer((req, res) => {
    void forward(req, res, access, lookup);
  });
  server.on('connect', (req: http.IncomingMessage, client: net.Socket, head: Buffer) => {
    void tunnel(req, client, head, access, lookup);
  });
  // A page that floods the proxy with idle sockets must not keep them forever.
  server.headersTimeout = 30_000;
  server.requestTimeout = 0; // long downloads and uploads are fine once started
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      if (!address || typeof address === 'string') return reject(new Error('no port'));
      server.off('error', reject);
      resolve({
        port: address.port,
        close: () =>
          new Promise<void>((done) => {
            server.closeAllConnections();
            server.close(() => done());
          }),
      });
    });
  });
}

async function tunnel(
  req: http.IncomingMessage,
  client: net.Socket,
  head: Buffer,
  access: LocalAccess,
  lookup: Lookup,
): Promise<void> {
  client.on('error', () => client.destroy());
  const target = splitAuthority(req.url ?? '');
  if (!target) return refuseSocket(client, 400, 'bad CONNECT target');
  let address: string;
  try {
    address = await resolveChecked(target.host, access, lookup);
  } catch (err) {
    return refuseSocket(client, err instanceof Refused ? 403 : 502, (err as Error).message);
  }
  const upstream = net.connect({ host: address, port: target.port });
  let established = false;
  upstream.once('connect', () => {
    established = true;
    client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
    if (head.length) upstream.write(head);
    upstream.pipe(client);
    client.pipe(upstream);
  });
  upstream.on('error', () => {
    // Before the 200 the client still expects a proxy answer; after it, a reset.
    if (!established) refuseSocket(client, 502, 'upstream unreachable');
    else client.destroy();
  });
  client.on('close', () => upstream.destroy());
  upstream.on('close', () => {
    if (established) client.destroy();
  });
}

function refuseSocket(client: net.Socket, status: number, reason: string): void {
  const text = status === 403 ? 'Forbidden' : status === 400 ? 'Bad Request' : 'Bad Gateway';
  const body = `${reason}\n`;
  client.end(
    `HTTP/1.1 ${status} ${text}\r\nContent-Type: text/plain\r\nContent-Length: ${Buffer.byteLength(body)}\r\nConnection: close\r\n\r\n${body}`,
  );
}

async function forward(
  req: http.IncomingMessage,
  res: http.ServerResponse,
  access: LocalAccess,
  lookup: Lookup,
): Promise<void> {
  let url: URL;
  try {
    url = new URL(req.url ?? '');
    if (url.protocol !== 'http:') throw new Error('not http');
  } catch {
    res
      .writeHead(400, { 'Content-Type': 'text/plain' })
      .end('proxy requests need an absolute http URL\n');
    return;
  }
  let address: string;
  try {
    address = await resolveChecked(url.hostname, access, lookup);
  } catch (err) {
    res
      .writeHead(err instanceof Refused ? 403 : 502, { 'Content-Type': 'text/plain' })
      .end(`${(err as Error).message}\n`);
    return;
  }
  const headers: http.OutgoingHttpHeaders = {};
  for (const [name, value] of Object.entries(req.headers)) {
    if (value !== undefined && !HOP.has(name)) headers[name] = value;
  }
  // RFC 9112 §3.2.2: the absolute URI's authority wins over any Host header.
  headers.host = url.host;
  const upstream = http.request(
    {
      host: address,
      port: url.port ? Number(url.port) : 80,
      method: req.method,
      path: url.pathname + url.search,
      headers,
      setHost: false,
      // The address is already checked: never look the name up a second time.
      lookup: (_h, _o, cb) => cb(null, address, net.isIP(address)),
    },
    (up) => {
      const out: http.OutgoingHttpHeaders = {};
      for (const [name, value] of Object.entries(up.headers)) {
        if (value !== undefined && !HOP.has(name)) out[name] = value;
      }
      res.writeHead(up.statusCode ?? 502, up.statusMessage, out);
      up.pipe(res);
    },
  );
  upstream.on('error', () => {
    if (!res.headersSent)
      res.writeHead(502, { 'Content-Type': 'text/plain' }).end('upstream unreachable\n');
    else res.destroy();
  });
  req.pipe(upstream);
}
