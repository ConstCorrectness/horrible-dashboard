/**
 * Does a URL *name* a local address — this machine or its network?
 *
 * The desktop browser is fenced off from those (apps/desktop/src/browserProxy.ts,
 * which decides on the resolved address). This is the cheap, name-only half the
 * agent tools use to ask before `browser.open` unlocks one. A public-looking name
 * that resolves locally is not caught here, and does not need to be: it is never
 * unlocked, so the proxy refuses it.
 */

/** Suffixes that only mean something on a local network. */
const LOCAL_SUFFIXES = [
  '.localhost',
  '.local',
  '.lan',
  '.internal',
  '.intranet',
  '.home.arpa',
  '.home',
  '.corp',
];

function v4Local(host: string): boolean | null {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (!m) return null;
  const [a, b] = [Number(m[1]), Number(m[2])];
  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    a >= 224
  );
}

/** Whether `url` points at a local host by its name alone. Not-a-URL is false. */
export function isLocalUrl(url: string): boolean {
  let host: string;
  try {
    host = new URL(url).hostname.toLowerCase().replace(/\.$/, '');
  } catch {
    return false;
  }
  if (host.startsWith('[')) {
    const v6 = host.slice(1, -1);
    const mapped = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/.exec(v6);
    if (mapped?.[1]) return v4Local(mapped[1]) ?? true;
    // Loopback, unspecified, unique-local, link-local, multicast, and the hex form
    // of a mapped address; anything else is a public IPv6 literal.
    return /^(::1?|f[cd][0-9a-f]{0,2}:.*|fe[89ab][0-9a-f]:.*|ff[0-9a-f]{2}:.*|::ffff:.*)$/.test(v6);
  }
  const v4 = v4Local(host);
  if (v4 !== null) return v4;
  if (host === 'localhost' || !host.includes('.')) return true;
  return LOCAL_SUFFIXES.some((s) => host.endsWith(s));
}
