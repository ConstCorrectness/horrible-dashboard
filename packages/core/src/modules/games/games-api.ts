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

export function fetchStatus(): Promise<GamesStatus> {
  return apiGet<GamesStatus>('/games/status');
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

/**
 * Run the GitHub device flow: start it, surface the code via `onCode`, then poll
 * until the node captures the token (or it errors/times out). Resolves to the signed-in
 * display name.
 */
export async function signInWithGitHub(
  onCode: (code: string, url: string) => void,
): Promise<string> {
  const start = await apiPost<DeviceStart>('/games/auth/github/start', {});
  if (start.error || !start.device_code) {
    throw new Error(
      start.error || 'sign-in unavailable — configure games.github.clientId on the server',
    );
  }
  onCode(start.user_code ?? '', start.verification_uri ?? 'https://github.com/login/device');
  const intervalMs = Math.max((start.interval ?? 5) * 1000, 2000);
  const deadline = Date.now() + 5 * 60 * 1000; // GitHub codes last ~15m; cap our wait at 5m
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, intervalMs));
    const poll = await apiPost<{
      signed_in?: boolean;
      account?: { display_name?: string };
      error?: string;
    }>('/games/auth/github/poll', { device_code: start.device_code });
    if (poll.signed_in) return poll.account?.display_name ?? 'signed in';
    if (poll.error && poll.error !== 'authorization_pending') throw new Error(poll.error);
  }
  throw new Error('sign-in timed out');
}
