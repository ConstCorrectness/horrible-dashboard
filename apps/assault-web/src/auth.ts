/**
 * Sign-in for the browser build — talking to the game server directly.
 *
 * The desktop app signs in through `@horrible/core`'s `SignInCard`, which never
 * sees a token: the node holds the JWT server-side and hands the page only the
 * account. There is no node here. The page's origin *is* the game server (or,
 * with `VITE_BACKEND_URL`, a page pointed at one), so the token has to live in
 * the browser and be attached by this code — to `/me`, to `/account/handle`, and
 * to the match socket as `?token=`, which is the only way a browser can identify
 * itself on a websocket.
 *
 * Signed out is a first-class state, not an error: the guest callsign still plays,
 * it just plays unrated (the server marks any room with a guest in it unrated).
 */
import { useSyncExternalStore } from 'react';
import { apiUrl, setWsPath } from '@horrible/core';

const TOKEN_KEY = 'hassault_token';

export type Provider = 'github' | 'google';

export interface Account {
  id: string;
  display_name: string;
  /** The claimed username. Null until chosen — OAuth never picks one for you. */
  handle?: string | null;
  suggested_handle?: string | null;
}

export interface AuthState {
  token: string | null;
  account: Account | null;
  /** True until the stored token (if any) has been checked against `/me`. */
  checking: boolean;
}

export interface ProviderFlows {
  github?: { device?: boolean; web?: boolean };
  google?: { device?: boolean; web?: boolean };
  local?: { password?: boolean };
}

// ---- storage ----------------------------------------------------------------
//
// Every access is guarded: private windows and blocked site data throw on
// `localStorage`, and a sign-in page that crashes there is worse than one that
// simply forgets you on reload.

function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function writeToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* the session still works; it just won't survive a reload */
  }
}

// ---- the store --------------------------------------------------------------

let state: AuthState = { token: readToken(), account: null, checking: readToken() !== null };
const listeners = new Set<() => void>();

function set(next: Partial<AuthState>): void {
  state = { ...state, ...next };
  for (const fn of listeners) fn();
}

export const authStore = {
  subscribe(fn: () => void): () => void {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
  getState(): AuthState {
    return state;
  },
};

export function useAuth(): AuthState {
  return useSyncExternalStore(authStore.subscribe, authStore.getState, authStore.getState);
}

/** The name a signed-in player goes by in a match: the handle once claimed. */
export function accountName(account: Account | null): string | null {
  if (!account) return null;
  return account.handle || null;
}

/**
 * Point the match socket at the right identity. A token only counts once it has a
 * handle: an account mid-way through choosing one would otherwise join named by
 * its raw id, so until then it plays as the guest callsign.
 */
export function applySocketIdentity(guestName: string): void {
  const { token, account } = state;
  if (token && account?.handle) {
    setWsPath(`/hassault-ws?token=${encodeURIComponent(token)}`);
  } else {
    setWsPath(`/hassault-ws?guest=1&name=${encodeURIComponent(guestName)}`);
  }
}

// ---- HTTP -------------------------------------------------------------------

/** The server answers `{error}` with a 200 for user-facing failures; surface it. */
async function post<T>(path: string, body: unknown, token?: string | null): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(apiUrl(path), {
    method: 'POST',
    headers,
    body: JSON.stringify(body ?? {}),
    credentials: 'omit',
  });
  if (!res.ok) throw new Error(`The server answered ${res.status}. Try again in a moment.`);
  const data = (await res.json()) as T & { error?: string };
  if (data && typeof data === 'object' && 'error' in data && data.error) {
    throw new Error(capitalise(String(data.error)));
  }
  return data;
}

function capitalise(text: string): string {
  return text ? text[0]!.toUpperCase() + text.slice(1) : text;
}

export async function fetchProviders(): Promise<ProviderFlows> {
  try {
    const res = await fetch(apiUrl('/auth/providers'), { credentials: 'omit' });
    return res.ok ? ((await res.json()) as ProviderFlows) : {};
  } catch {
    // Unknown means "leave the buttons on" — a click-time error explains itself.
    return {};
  }
}

interface SignedIn {
  token: string;
  account: Account;
}

function adopt(result: SignedIn): Account {
  writeToken(result.token);
  set({ token: result.token, account: result.account, checking: false });
  return result.account;
}

/** Re-read the account behind the stored token, dropping a token the server rejects. */
export async function refreshAccount(): Promise<void> {
  const token = state.token;
  if (!token) {
    set({ checking: false });
    return;
  }
  try {
    const res = await fetch(apiUrl('/me'), {
      headers: { Authorization: `Bearer ${token}` },
      credentials: 'omit',
    });
    const data = (await res.json()) as { account?: Account; error?: string };
    if (data.account) {
      set({ account: data.account, checking: false });
    } else {
      // Expired or from another server. Forget it rather than retrying forever.
      writeToken(null);
      set({ token: null, account: null, checking: false });
    }
  } catch {
    // Offline is not "signed out": keep the token, show the guest UI for now.
    set({ checking: false });
  }
}

export function signInWithPassword(email: string, password: string): Promise<Account> {
  return post<SignedIn>('/auth/local/login', { email, password }).then(adopt);
}

export function signUpWithPassword(
  email: string,
  password: string,
  username: string,
): Promise<Account> {
  return post<SignedIn>('/auth/local/signup', { email, password, username }).then(adopt);
}

export async function claimHandle(handle: string): Promise<Account> {
  const result = await post<{ account: Account }>('/account/handle', { handle }, state.token);
  set({ account: result.account });
  return result.account;
}

export function signOut(): void {
  writeToken(null);
  set({ token: null, account: null, checking: false });
}

/**
 * The redirect flow, in a popup.
 *
 * The window is opened **before** the first `await`, from the click itself: a
 * popup opened after a network round trip is no longer a user gesture and every
 * browser blocks it. It starts blank and is sent to the provider once the server
 * has issued the login URL.
 *
 * Completion is learned by polling with the private `retrieval_code`, not from the
 * popup: the callback page is on the game server's origin and closes itself, and
 * the code never appears in a URL, so nothing on the provider side can lift it.
 */
export async function signInWithProvider(
  provider: Provider,
  signal: AbortSignal,
): Promise<Account> {
  const popup = window.open('about:blank', 'hassault-signin', 'width=520,height=680');
  try {
    const start = await post<{ login_url: string; retrieval_code: string; expires_in?: number }>(
      `/auth/${provider}/web/start`,
      {},
    );
    if (popup && !popup.closed) popup.location.href = start.login_url;
    else window.open(start.login_url, '_blank');

    const deadline = Date.now() + Math.min((start.expires_in ?? 300) * 1000, 5 * 60 * 1000);
    while (Date.now() < deadline) {
      await sleep(1500, signal);
      const result = await post<{ pending?: boolean; token?: string; account?: Account }>(
        `/auth/${provider}/web/poll`,
        { retrieval_code: start.retrieval_code },
      );
      if (result.token && result.account) {
        return adopt({ token: result.token, account: result.account });
      }
    }
    throw new Error('Sign-in timed out. Start it again when you are ready.');
  } catch (err) {
    if (popup && !popup.closed) popup.close();
    throw err;
  }
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Cancelled', 'AbortError'));
      return;
    }
    const id = setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(id);
        reject(new DOMException('Cancelled', 'AbortError'));
      },
      { once: true },
    );
  });
}
