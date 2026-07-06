/**
 * HTTP helpers for the games module: sign-in status, GitHub device-flow sign-in
 * (proxied through the node so the browser talks to one origin), and the ladder.
 * Live match state comes over the `games` `/ws` channel (see game-ws.ts); these are
 * the request/response bits.
 */
import { apiGet, apiPost } from '../../api';

export interface GamesStatus {
  connected: boolean;
  account_id: string | null;
  signed_in: boolean;
  display_name: string | null;
  server_url: string;
  policy: string;
  games: { id: string; name: string }[];
}

export interface LeaderRow {
  account_id: string;
  display_name: string;
  rating: number;
  wins: number;
  losses: number;
  draws: number;
  games: number;
}

interface DeviceStart {
  user_code?: string;
  verification_uri?: string;
  device_code?: string;
  interval?: number;
  error?: string;
}

export interface GameCatalogEntry {
  id: string;
  name: string;
}

export function fetchStatus(): Promise<GamesStatus> {
  return apiGet<GamesStatus>('/games/status');
}

/**
 * The engine's game catalog (`{id, name}`), sourced from the node's registry via
 * `/games/status`. Panels use it to drive game pickers so a newly-registered game
 * appears everywhere without touching the UI. Falls back to Tic-Tac-Toe if the node
 * is unreachable, so the lobby still renders.
 */
export async function fetchGamesCatalog(): Promise<GameCatalogEntry[]> {
  try {
    const status = await fetchStatus();
    if (status.games?.length) return status.games;
  } catch {
    // node down / not yet started — fall through to the built-in default
  }
  return [{ id: 'tictactoe', name: 'Tic-Tac-Toe' }];
}

export function fetchLeaderboard(
  gameId: string,
): Promise<{ game_id: string; entries: LeaderRow[] }> {
  return apiGet(`/games/leaderboard?game_id=${encodeURIComponent(gameId)}`);
}

export interface ChallengeRow {
  account_id: string;
  display_name: string;
  correct: number;
  total: number;
  score: number;
}

export function fetchChallengeLeaderboard(
  gameId: string,
): Promise<{ game_id: string; entries: ChallengeRow[] }> {
  return apiGet(`/games/challenges/leaderboard?game_id=${encodeURIComponent(gameId)}`);
}

export function signOut(): Promise<{ ok: boolean }> {
  return apiPost('/games/signout', {});
}

/** OAuth providers the game server can sign a player in with (both device flow). */
export type SignInProvider = 'github' | 'google';

const PROVIDER_FALLBACK_URL: Record<SignInProvider, string> = {
  github: 'https://github.com/login/device',
  google: 'https://www.google.com/device',
};

/**
 * Run a provider's device flow: start it, surface the code via `onCode`, then poll
 * until the node captures the token (or it errors/times out). Resolves to the
 * signed-in display name. The node normalizes both providers to one wire shape.
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
  const deadline = Date.now() + 5 * 60 * 1000; // device codes last ~15m; cap our wait at 5m
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, intervalMs));
    const poll = await apiPost<{
      signed_in?: boolean;
      account?: { display_name?: string };
      error?: string;
    }>(`/games/auth/${provider}/poll`, { device_code: start.device_code });
    if (poll.signed_in) return poll.account?.display_name ?? 'signed in';
    if (poll.error && poll.error !== 'authorization_pending') throw new Error(poll.error);
  }
  throw new Error('sign-in timed out');
}

/** Back-compat alias: the original GitHub-only entry point. */
export function signInWithGitHub(onCode: (code: string, url: string) => void): Promise<string> {
  return signInWith('github', onCode);
}
