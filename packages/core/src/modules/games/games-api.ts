/**
 * HTTP helpers for the games module: sign-in status, GitHub device-flow sign-in
 * (proxied through the node so the browser talks to one origin), and the ladder.
 * Live match state comes over the `games` `/ws` channel (see game-ws.ts); these are
 * the request/response bits.
 */
import { apiGet, apiPost, apiPut } from '../../api';

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
  /** Masked (null) while the player is still in placement matches. */
  rating: number | null;
  tier?: string;
  placement_games?: number;
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

// ---- the agent (loadout) -----------------------------------------------------
//
// A player's agent for a game: an optional `my_agent(obs, config)` entrypoint
// (`agent_code`) over the declarative harness (system-prompt `context` + custom
// Python `tools` + the `model` that drives them). Empty agent_code = the default
// agent (context + tools drive the model). See backend agent_sdk.py.

export interface LoadoutTool {
  name: string;
  description: string;
  code: string;
  parameters: Record<string, { type?: string; description?: string }>;
  required: string[];
}

export interface Loadout {
  game_id: string;
  context: string;
  tools: LoadoutTool[];
  model: Record<string, unknown> | null;
  agent_code: string;
}

export interface LoadoutValidation {
  ok: boolean;
  tools: { name: string; ok: boolean; error: string | null }[];
  agent_error: string | null;
}

export function getLoadout(gameId: string): Promise<Loadout> {
  return apiGet(`/games/loadout/${encodeURIComponent(gameId)}`);
}

export function saveLoadout(gameId: string, loadout: Loadout): Promise<Loadout> {
  return apiPut(`/games/loadout/${encodeURIComponent(gameId)}`, loadout);
}

export function validateLoadout(loadout: Loadout): Promise<LoadoutValidation> {
  return apiPost('/games/loadout/validate', loadout);
}

/** The starter `my_agent` source to seed the editor for a fresh agent on a game. */
export function getAgentStarter(gameId: string): Promise<{ game_id: string; agent_code: string }> {
  return apiGet(`/games/agent-starter/${encodeURIComponent(gameId)}`);
}

/** A shipped starter harness for a game: a titled, blurbed loadout whose `tools`
 * are the templated tool definitions the builder's Tools section offers as a
 * starting point (backend/modules/games/templates.py). */
export interface LoadoutTemplate {
  id: string;
  game_id: string;
  title: string;
  blurb: string;
  loadout: Loadout;
}

/** The shipped templates, optionally narrowed to one game. */
export async function fetchLoadoutTemplates(gameId?: string): Promise<LoadoutTemplate[]> {
  const q = gameId ? `?game_id=${encodeURIComponent(gameId)}` : '';
  const res = await apiGet<{ templates?: LoadoutTemplate[] }>(`/games/loadout-templates${q}`);
  return res.templates ?? [];
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

// ---- replays -----------------------------------------------------------------

/** A replay summary row (no event log — fetch the replay itself for that). */
export interface ReplaySummary {
  id: string;
  game_id: string;
  table_id: string;
  series_id: string | null;
  created_at: number;
  seats: string[];
  winner: number | null;
  returns: Record<string, number>;
  public: boolean;
}

/** One event in a replay's log. `kind` is public_state | action | trace | game_over. */
export interface ReplayEvent {
  kind: string;
  seat?: number;
  state?: Record<string, unknown>;
  action_id?: string;
  timeout?: boolean;
  steps?: { kind: string; [k: string]: unknown }[];
  returns?: Record<string, number>;
  winner?: number | null;
  [k: string]: unknown;
}

export interface Replay extends ReplaySummary {
  events: ReplayEvent[];
}

export function fetchReplays(
  scope: 'mine' | 'public',
  gameId?: string,
): Promise<{ replays?: ReplaySummary[]; error?: string }> {
  const game = gameId ? `&game_id=${encodeURIComponent(gameId)}` : '';
  return apiGet(`/games/replays?scope=${scope}${game}`);
}

export function fetchReplay(replayId: string): Promise<{ replay?: Replay; error?: string }> {
  return apiGet(`/games/replays/${encodeURIComponent(replayId)}`);
}

export function publishReplay(replayId: string): Promise<{ ok?: boolean; error?: string }> {
  return apiPost(`/games/replays/${encodeURIComponent(replayId)}/publish`, {});
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

/**
 * Run a provider's redirect (authorization-code) flow — no code typing. Starts the
 * flow, hands the provider consent URL to `openAuthorize` (the caller opens it, ideally
 * in a pre-opened popup so it isn't blocked), then polls the node until it captures the
 * token. The private retrieval code stays on the node; the browser never sees it.
 * Resolves to the signed-in display name.
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
  const deadline = Date.now() + 5 * 60 * 1000; // codes last ~15m; cap our wait at 5m
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2500));
    const poll = await apiPost<{
      signed_in?: boolean;
      pending?: boolean;
      account?: { display_name?: string };
      error?: string;
    }>(`/games/auth/${provider}/web/poll`, {});
    if (poll.signed_in) return poll.account?.display_name ?? 'signed in';
    if (poll.error) throw new Error(poll.error);
  }
  throw new Error('sign-in timed out');
}
