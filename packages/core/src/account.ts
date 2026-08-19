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
 * usernames. Password bodies are additionally kept out of the I/O ring buffer —
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

export type AuthProviderMap = Partial<Record<SignInProvider | 'local', AuthProviderFlows>>;

/**
 * What the node can say about signing in: which flows work, and on which server.
 *
 * `server` matters because a greyed-out sign-in button is otherwise
 * unexplainable. The node resolves its game server from `GAMES_SERVER_URL`
 * ahead of the `games.serverUrl` setting, so under `pnpm dev` it targets the
 * bundled local game server — which ships with no OAuth credentials and reports
 * every provider unavailable. Reading the setting would tell the browser
 * something different and wrong.
 */
export interface AuthProviders {
  server: string;
  /** `{}` means the server couldn't say — treat every provider as available. */
  flows: AuthProviderMap;
}

export interface Account {
  id: string;
  display_name: string;
  /**
   * The globally unique username (the game server's `handle`).
   *
   * **Null means it has not been chosen yet**, which is a real state a signed-in
   * account can be in: OAuth sign-in deliberately no longer picks one on the
   * person's behalf. Every screen that gates on "is this account finished?" reads
   * this, not `id`.
   */
  handle?: string | null;
  /**
   * What to pre-fill the username field with — the provider login or email local
   * part, folded into the handle charset.
   *
   * A *suggestion*, never a claim: it is not reserved, and two people can be shown
   * the same one (two `sam@`s are both suggested `sam`). The first to claim it
   * gets it and the second is told so, which is the honest version of what
   * `ensure_handle` used to do by silently appending a `2`.
   */
  suggested_handle?: string | null;
}

export async function fetchAuthProviders(): Promise<AuthProviders> {
  const raw = await apiGet<Partial<AuthProviders>>('/games/auth/providers');
  return { server: raw.server ?? '', flows: raw.flows ?? {} };
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
  /**
   * The page could not be opened for the user — a blocked pop-up, or a desktop
   * shell that refused.
   *
   * The sign-in is still perfectly valid and still polling; the *only* thing
   * missing is that nobody is looking at the consent page. So this is not an
   * error, it is an instruction: the UI must stop saying "finish in the window
   * that opened" and start asking the user to open it themselves.
   */
  blocked?: boolean;
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
 * 4. **Every way of opening the page can fail silently**, so the result is
 *    checked. A blocked pop-up used to leave the button reading "Waiting…" for
 *    fifteen minutes with no browser ever appearing and no error — the flow was
 *    running correctly the whole time, waiting on a consent page nobody had been
 *    shown. When opening fails the prompt says so and offers the link, because
 *    the user's own click on it is a gesture no blocker will refuse.
 * 5. **A blocked prompt outlives the attempt that raised it.** The prompt is
 *    cleared on success only. It used to be cleared in a `finally`, which meant
 *    the address vanished off screen the moment the flow timed out or errored —
 *    taking the one thing the user still needed with it, at exactly the moment
 *    they had been told to go and use it.
 *
 * `prefer: 'device'` skips straight to the device flow. That flow opens nothing
 * and needs only a client id, so it is the honest answer when the machine has
 * shown it cannot put a page on screen; until now it was reachable only by
 * accident, when the redirect flow happened to fail first.
 */
export async function oauthSignIn(
  provider: SignInProvider,
  onPrompt: (prompt: SignInPrompt | null) => void,
  options: { prefer?: 'redirect' | 'device' } = {},
): Promise<string> {
  const deviceOnly = options.prefer === 'device';
  const popup =
    deviceOnly || isDesktopShell()
      ? null
      : window.open('', 'games-oauth', 'popup,width=600,height=760');
  let navigated = false;
  const point = (prompt: SignInPrompt) => {
    navigated = true;
    onPrompt(prompt);
    if (popup && !popup.closed) {
      popup.location.href = prompt.url;
      return;
    }
    // Nothing was pre-opened — either the blocker took it, or this is the
    // desktop shell, which never opens one. Ask the platform, and if that fails
    // too, hand the job back to the user rather than waiting on a page that is
    // not on their screen.
    void openExternal(prompt.url).then((opened) => {
      if (!opened) onPrompt({ ...prompt, blocked: true });
    });
  };
  try {
    let name: string;
    if (deviceOnly) {
      name = await signInWith(provider, (code, url) => point({ code, url }));
    } else {
      try {
        name = await signInWithRedirect(provider, (url) => point({ url }));
      } catch (e) {
        if (navigated) throw e;
        name = await signInWith(provider, (code, url) => point({ code, url }));
      }
    }
    onPrompt(null);
    return name;
  } finally {
    if (popup && !popup.closed) popup.close();
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

/**
 * Create an account. `username` is **required** — the server rejects a signup
 * without one.
 *
 * It used to be optional, with a handle derived from the email address when it
 * was left out. That is why nobody was ever asked what they wanted to be called:
 * the account arrived already holding one, and every downstream chooser saw a
 * finished account.
 */
export async function signUpWithPassword(
  email: string,
  password: string,
  username: string,
): Promise<Account> {
  return unwrap(
    await apiPost<LocalAuthResult>('/games/auth/local/signup', { email, password, username }),
  );
}

export async function signInWithPassword(email: string, password: string): Promise<Account> {
  return unwrap(await apiPost<LocalAuthResult>('/games/auth/local/login', { email, password }));
}

/** Claim or rename the username. Throws with the server's reason ('that username is
 * taken', or the charset rule) so a form can show it verbatim. */
export async function setUsername(username: string): Promise<Account> {
  const result = await apiPost<{ ok?: boolean; account?: Account; error?: string }>(
    '/games/auth/username',
    { username },
  );
  if (result.error) throw new Error(result.error);
  if (!result.account) throw new Error('could not set username');
  return result.account;
}

export function signOut(): Promise<{ ok: boolean }> {
  return apiPost('/games/signout', {});
}
