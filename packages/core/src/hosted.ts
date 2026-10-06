/**
 * The hosted hub, seen from the browser.
 *
 * Hosted, the frontend is served by (or rewritten to) the hub — `backend/hub/` —
 * which signs people in with their games account and proxies `/api` and `/ws` to
 * that person's own backend instance. Everywhere else (`pnpm dev`, the desktop
 * shell) there is no hub, and nothing here changes anything.
 *
 * The entry asks {@link probeHub} once, before any `/api` call, because signed out
 * every `/api` call answers 401 and the boot would fill in defaults over the
 * user's real settings. Signed in, it waits for the instance to come up
 * ({@link waitForInstance}) before loading anything from it.
 */
import { apiUrl, getWsOrigin, wsUrl } from './origin';

/** The account the hub signed in — the game server's account, minus any token. */
export interface HubAccount {
  id: string;
  handle: string | null;
  display_name: string;
}

export type HubProbe =
  | { mode: 'local' }
  | { mode: 'signedOut' }
  | { mode: 'signedIn'; account: HubAccount };

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

let account: HubAccount | null = null;
let signedOut = false;

/** The hub account this page is signed in as; null when not behind a hub. */
export function hubAccount(): HubAccount | null {
  return account;
}

/** Whether this page is talking to a hosted instance through the hub. */
/**
 * Whether a hub answered that this browser is signed out. Nothing on the login
 * screen needs the socket, and the hub refuses it, so `ws.ts` stays closed rather
 * than retrying a refused upgrade for as long as the sign-in screen is open.
 */
export function hubSignedOut(): boolean {
  return signedOut;
}

export function isHosted(): boolean {
  return account !== null;
}

/**
 * Ask whether a hub is in front of the backend, and who is signed in.
 *
 * Only a JSON answer counts. A plain backend has no `/hub/me` and 404s, but the
 * Vite dev server answers unknown paths with `index.html` and a 200 — so "200"
 * alone would make every `pnpm dev` session think it was hosted. Any failure to
 * reach it at all is also `local`: the boot then fails, or succeeds, exactly as it
 * did before the hub existed.
 */
export async function probeHub(fetchImpl: FetchLike = fetch): Promise<HubProbe> {
  let res: Response;
  try {
    res = await fetchImpl(apiUrl('/hub/me'), { credentials: 'include' });
  } catch {
    return { mode: 'local' };
  }
  if (!(res.headers.get('content-type') ?? '').includes('application/json')) {
    return { mode: 'local' };
  }
  let body: { signed_in?: boolean; account?: HubAccount };
  try {
    body = (await res.json()) as typeof body;
  } catch {
    return { mode: 'local' };
  }
  if (res.status === 401 && body.signed_in === false) {
    signedOut = true;
    return { mode: 'signedOut' };
  }
  if (res.ok && body.signed_in === true && body.account) {
    account = body.account;
    return { mode: 'signedIn', account: body.account };
  }
  return { mode: 'local' };
}

/** POST to a hub route (outside `/api`), with the session cookie. */
export async function hubPost<T>(
  path: string,
  body: unknown = {},
  fetchImpl: FetchLike = fetch,
): Promise<T> {
  const res = await fetchImpl(apiUrl(path), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return (await res.json()) as T;
}

export async function hubGet<T>(path: string, fetchImpl: FetchLike = fetch): Promise<T> {
  const res = await fetchImpl(apiUrl(path), { credentials: 'include' });
  return (await res.json()) as T;
}

/** End the hub session and return to the sign-in screen. */
export async function signOutHub(): Promise<void> {
  try {
    await hubPost('/hub/logout');
  } finally {
    window.location.reload();
  }
}

/**
 * Wait for the user's instance to answer. The hub starts it at sign-in and stops
 * it when idle; while it boots, `/api/*` answers 503 `{starting}`. Resolves true
 * once `/api/health` is 200, false after `timeoutMs`.
 */
export async function waitForInstance(
  timeoutMs = 180_000,
  fetchImpl: FetchLike = fetch,
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const res = await fetchImpl(apiUrl('/api/health'), { credentials: 'include' });
      if (res.ok) return true;
      if (res.status === 401) return false; // the session ended under us
    } catch {
      // not reachable yet — keep polling
    }
    if (Date.now() >= deadline) return false;
    await sleep(1000);
  }
}

/**
 * Whether WebSockets go to another origin than this page, so the upgrade will not
 * carry the hub's (first-party, SameSite=Lax) cookie — the Vercel deployment, where
 * `/api` rides a same-origin rewrite but `/ws` cannot.
 */
export function crossOriginSocket(): boolean {
  const origin = getWsOrigin();
  if (!origin) return false;
  try {
    return new URL(origin).origin !== window.location.origin;
  } catch {
    return false;
  }
}

/**
 * The URL to open `/ws` at. Hosted on another origin, a single-use ticket from the
 * hub stands in for the cookie; everywhere else this is plain {@link wsUrl}.
 * The ticket is fetched through the same-origin route (`/hub/ws-ticket`, rewritten
 * to the hub like `/api`), which is where the cookie lives.
 */
export async function socketUrl(path: string, fetchImpl: FetchLike = fetch): Promise<string> {
  const url = wsUrl(path);
  if (!isHosted() || !crossOriginSocket()) return url;
  const res = await fetchImpl(apiUrl('/hub/ws-ticket'), {
    method: 'POST',
    credentials: 'include',
  });
  if (!res.ok) return url;
  const { ticket } = (await res.json()) as { ticket?: string };
  if (!ticket) return url;
  return `${url}${url.includes('?') ? '&' : '?'}ticket=${encodeURIComponent(ticket)}`;
}

/** Test seam: forget the probed account. */
export function _resetHostedForTests(): void {
  account = null;
  signedOut = false;
}
