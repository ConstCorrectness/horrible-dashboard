/**
 * Shared account state — one answer to "who am I signed in as", for every surface
 * that asks.
 *
 * Before this, each surface fetched its own copy: the hassault boot overlay, the
 * games lobby sidebar, and the games first-run hero all called the status endpoint
 * independently and held the result in their own `useState`. Signing in on one of
 * them left the others showing a signed-out screen until they happened to remount,
 * which is exactly the kind of disagreement a shared identity is supposed to make
 * impossible. Now there is one fetch, one cache, and every subscriber moves at once.
 *
 * The identity itself still lives where it always did — the node holds the JWT
 * server-side and hands back only the account (see `account.ts`). This is a cache
 * of that answer, not a second source of it.
 */
import { apiGet } from './api';
import type { Account } from './account';

/** The account fields `/games/status` carries. Reusing that route rather than
 * adding one: it already returns exactly this, and identity is issued by the game
 * server, so the games namespace is where it honestly lives. */
interface StatusResponse {
  signed_in?: boolean;
  account_id?: string | null;
  display_name?: string | null;
  username?: string | null;
  server_url?: string;
}

export interface AccountState {
  account: Account | null;
  signedIn: boolean;
  /** Which game server issued (or would issue) this identity. Worth surfacing:
   * a signed-out state is unexplainable without naming the server it's against. */
  server: string;
  /** `unavailable` means the backend couldn't be reached — distinct from a
   * confident "you are signed out", which is what `ready` + `!signedIn` says. */
  phase: 'loading' | 'ready' | 'unavailable';
}

const SIGNED_OUT: AccountState = {
  account: null,
  signedIn: false,
  server: '',
  phase: 'loading',
};

let state: AccountState = SIGNED_OUT;
const listeners = new Set<() => void>();
/** In-flight refresh, shared so N mounting components cause one request. */
let inFlight: Promise<AccountState> | null = null;
let everLoaded = false;

function emit(next: AccountState): void {
  state = next;
  for (const listener of listeners) listener();
}

/**
 * Re-read the account from the node and publish it to every subscriber. Safe to
 * call from anywhere after a sign-in, a sign-out, or a username change.
 */
export function refreshAccount(): Promise<AccountState> {
  if (inFlight) return inFlight;
  inFlight = apiGet<StatusResponse>('/games/status')
    .then((raw) => {
      const signedIn = raw.signed_in === true;
      const next: AccountState = {
        signedIn,
        account: signedIn
          ? {
              id: raw.account_id ?? '',
              display_name: raw.display_name ?? 'signed in',
              handle: raw.username ?? null,
            }
          : null,
        server: raw.server_url ?? '',
        phase: 'ready',
      };
      emit(next);
      return next;
    })
    .catch(() => {
      // Keep whatever account we already had: a backend blip must not present as
      // a sign-out and send a signed-in user back through a sign-in screen.
      const next: AccountState = { ...state, phase: 'unavailable' };
      emit(next);
      return next;
    })
    .finally(() => {
      inFlight = null;
      everLoaded = true;
    });
  return inFlight;
}

export const accountStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    // First subscriber kicks off the initial load; later ones ride the cache.
    if (!everLoaded && !inFlight) void refreshAccount();
    return () => {
      listeners.delete(listener);
    };
  },
  getState(): AccountState {
    return state;
  },
  /** Test seam: drop the cache so a suite starts from a known state. */
  reset(): void {
    everLoaded = false;
    inFlight = null;
    emit(SIGNED_OUT);
  },
};
