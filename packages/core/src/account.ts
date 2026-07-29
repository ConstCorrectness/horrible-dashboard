/**
 * The app's account — one identity, shared by every feature that needs one.
 *
 * There is exactly one account here, issued by the central game server: the games
 * ladder and HorribleAssault sign in together, because two separate sign-ins for
 * the same person on the same machine would be a bug, not a feature. It lives in
 * `packages/core` rather than inside either module for the same reason — a module
 * must never import another module's internals (see `.claude/skills/new-module`),
 * so shared behaviour becomes a core service instead.
 *
 * Three ways in, all ending in the same place:
 *
 * - **Redirect (web) OAuth** — the one-click default. Authorize on the provider's
 *   own consent page, no code typing.
 * - **Device OAuth** — the fallback when the game server has no web credentials
 *   for that provider (GitHub's device flow needs only a client id).
 * - **Email + password** — needs no OAuth configuration at all, so it works on a
 *   server with nothing set up.
 *
 * **The token never reaches this code.** The node holds the JWT server-side and
 * hands back only the account; everything here deals in display names and
 * callsigns. Password bodies are additionally kept out of the I/O ring buffer —
 * see `REDACT_BODY_PREFIXES` in api.ts.
 */
import { apiGet, apiPost } from './api';
import { isDesktopShell, openExternal } from './external';

export type SignInProvider = 'github' | 'google';

/** Which sign-in flows a provider supports on the connected game server. A missing
 * provider (or a `{}` response) means the server couldn't say — treat as available
 * and let the click-time error handle it. */
export interface AuthProviderFlows {
  device?: boolean;
  web?: boolean;
  /** Only on the pseudo-provider `local`: email+password, always available. */
  password?: boolean;
}

export type AuthProviders = Partial<Record<SignInProvider | 'local', AuthProviderFlows>>;

export interface Account {
  id: string;
  display_name: string;
  /** The globally unique callsign (the game server's `handle`). */
  handle?: string | null;
}

export function fetchAuthProviders(): Promise<AuthProviders> {
  return apiGet('/games/auth/providers');
}

interface DeviceStart {
  device_code?: string;
  user_code?: string;
  verification_uri?: string;
  interval?: number;
  error?: string;
}

interface PollResult {
  signed_in?: boolean;
  pending?: boolean;
  account?: { display_name?: string };
  error?: string;
}

const PROVIDER_FALLBACK_URL: Record<SignInProvider, string> = {
  github: 'https://github.com/login/device',
  google: 'https://www.google.com/device',
};

/** Codes last ~15 minutes at the provider; cap our own wait well under that. */
const SIGN_IN_DEADLINE_MS = 5 * 60 * 1000;

/**
 * Run a provider's device flow: start it, surface the code via `onCode`, then poll
 * until the node captures the token. Resolves to the signed-in display name. The
 * node normalizes both providers to one wire shape.
 */
export async function signInWith(
  provider: SignInProvider,
  onCode: (code: string, url: string) => void,
): Promise<string> {
  const start = await apiPost<DeviceStart>(`/games/auth/${provider}/start`, {});
  if (start.error || !start.device_code) {
    throw new Error(
      start.error || `sign-in unavailable — configure games.${provider}.clientId on the server`,
    );
  }
  onCode(start.user_code ?? '', start.verification_uri ?? PROVIDER_FALLBACK_URL[provider]);
  const intervalMs = Math.max((start.interval ?? 5) * 1000, 2000);
  const deadline = Date.now() + SIGN_IN_DEADLINE_MS;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, intervalMs));
    const poll = await apiPost<PollResult>(`/games/auth/${provider}/poll`, {
      device_code: start.device_code,
    });
    if (poll.signed_in) return poll.account?.display_name ?? 'signed in';
    if (poll.error && poll.error !== 'authorization_pending') throw new Error(poll.error);
  }
  throw new Error('sign-in timed out');
}

/** Back-compat alias: the original GitHub-only entry point. */
export function signInWithGitHub(onCode: (code: string, url: string) => void): Promise<string> {
  return signInWith('github', onCode);
}

/**
 * Run a provider's redirect (authorization-code) flow — no code typing. Hands the
 * consent URL to `openAuthorize` (the caller opens it, ideally in a pre-opened
 * popup so it isn't blocked), then polls the node until it captures the token. The
 * private retrieval code stays on the node; the browser never sees it.
 */
export async function signInWithRedirect(
  provider: SignInProvider,
  openAuthorize: (url: string) => void,
): Promise<string> {
  const start = await apiPost<{ authorize_url?: string; error?: string }>(
    `/games/auth/${provider}/web/start`,
    {},
  );
  if (start.error || !start.authorize_url) {
    throw new Error(
      start.error || `sign-in unavailable — configure ${provider} web OAuth on the server`,
    );
  }
  openAuthorize(start.authorize_url);
  const deadline = Date.now() + SIGN_IN_DEADLINE_MS;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2500));
    const poll = await apiPost<PollResult>(`/games/auth/${provider}/web/poll`, {});
    if (poll.signed_in) return poll.account?.display_name ?? 'signed in';
    if (poll.error) throw new Error(poll.error);
  }
  throw new Error('sign-in timed out');
}

/** What an in-progress OAuth sign-in wants to show: the provider page to (re)open,
 * plus a device code when the flow fell back to typing one in. */
export interface SignInPrompt {
  code?: string;
  url: string;
}

/**
 * The whole OAuth click, popup handling included — what every sign-in button
 * should call.
 *
 * Three things here are load-bearing and easy to get wrong, which is why this is
 * one function rather than a pattern each caller reimplements:
 *
 * 1. The popup is opened **synchronously**, before any `await`. A popup opened
 *    after a network round-trip is not attributable to the click and gets blocked.
 * 2. The redirect flow is tried first, falling back to the device flow **only if
 *    it failed before navigating**. A failure *after* the consent page opened is
 *    real (timeout, user cancelled) and must not silently restart as a different
 *    flow.
 * 3. Under the desktop shell the webview can't open windows at all, so URLs go to
 *    the system browser — which is also what OAuth wants there (existing sessions,
 *    and Google rejects embedded webviews outright).
 */
export async function oauthSignIn(
  provider: SignInProvider,
  onPrompt: (prompt: SignInPrompt | null) => void,
): Promise<string> {
  const popup = isDesktopShell()
    ? null
    : window.open('', 'games-oauth', 'popup,width=600,height=760');
  let navigated = false;
  const point = (url: string) => {
    navigated = true;
    if (popup && !popup.closed) popup.location.href = url;
    else void openExternal(url);
  };
  try {
    try {
      return await signInWithRedirect(provider, (url) => {
        onPrompt({ url });
        point(url);
      });
    } catch (e) {
      if (navigated) throw e;
      return await signInWith(provider, (code, url) => {
        onPrompt({ code, url });
        point(url);
      });
    }
  } finally {
    if (popup && !popup.closed) popup.close();
    onPrompt(null);
  }
}

// ---- email + password --------------------------------------------------------
//
// These two are the only calls in the app that carry a password. Their path is in
// `REDACT_BODY_PREFIXES` (api.ts) and `_REDACT_BODY_PREFIXES` (instrument.py), so
// the body is dropped by the client recorder, the backend's inbound middleware and
// its outbound httpx hook alike — otherwise the observability panel would show the
// password in plaintext three times over.

interface LocalAuthResult {
  signed_in?: boolean;
  account?: Account;
  error?: string;
}

function unwrap(result: LocalAuthResult): Account {
  if (result.error) throw new Error(result.error);
  if (!result.signed_in || !result.account) throw new Error('sign-in failed');
  return result.account;
}

/** Create an account. `callsign` is optional — omitted, one is derived from the
 * email and can be renamed later. */
export async function signUpWithPassword(
  email: string,
  password: string,
  callsign = '',
): Promise<Account> {
  return unwrap(
    await apiPost<LocalAuthResult>('/games/auth/local/signup', { email, password, callsign }),
  );
}

export async function signInWithPassword(email: string, password: string): Promise<Account> {
  return unwrap(await apiPost<LocalAuthResult>('/games/auth/local/login', { email, password }));
}

/** Claim or rename the callsign. Throws with the server's reason ('that callsign is
 * taken', or the charset rule) so a form can show it verbatim. */
export async function setCallsign(callsign: string): Promise<Account> {
  const result = await apiPost<{ ok?: boolean; account?: Account; error?: string }>(
    '/games/auth/callsign',
    { callsign },
  );
  if (result.error) throw new Error(result.error);
  if (!result.account) throw new Error('could not set callsign');
  return result.account;
}

export function signOut(): Promise<{ ok: boolean }> {
  return apiPost('/games/signout', {});
}
