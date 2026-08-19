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
  /** The globally unique username (the account's handle); null when signed out or
   * signed in without one yet. */
  username: string | null;
  server_url: string;
  policy: string;
  games: GameCatalogEntry[];
}

/** How a game's seat decides — the load-bearing axis (see docs/modules/games.mdx):
 * a `policy` game is a pure `obs → action` mapping (an LLM is optional-to-harmful);
 * a `reasoner` game needs the LLM harness (prompt + tools + model). */
export type DecisionClass = 'policy' | 'reasoner';

/** The four move-policy names, matching the backend `make_policy` vocabulary
 * (`manual` = no automatic policy). */
export type MovePolicy = 'random' | 'agent' | 'manual' | 'bot';

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

export interface GameCatalogEntry {
  id: string;
  name: string;
  /** Decision class + presentation metadata from the backend `GameSpec`. Optional
   * so an older node (or the offline fallback) still type-checks; consumers default
   * via `decisionClassOf` / the identity helpers. */
  decision_class?: DecisionClass;
  default_policy?: MovePolicy;
  allowed_policies?: MovePolicy[];
  obs_kind?: 'json' | 'frames';
  pacing?: 'turn' | 'realtime';
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

/**
 * Which harness a game's seat runs. `coded` is a Python policy and nothing else;
 * `llm` is context + tools + model + agent_code. They are separate objects with
 * separate version histories, so this is a discriminated union rather than one
 * shape with optional halves — and the backend's wire union **rejects** a body
 * carrying the other kind's fields rather than dropping them.
 */
export type HarnessKind = 'coded' | 'llm';

export interface LlmHarness {
  kind: 'llm';
  game_id: string;
  context: string;
  tools: LoadoutTool[];
  model: Record<string, unknown> | null;
  agent_code: string;
}

export interface CodedHarness {
  kind: 'coded';
  game_id: string;
  bot_code: string;
}

export type Harness = LlmHarness | CodedHarness;

/** The harness kind a game's seat runs, given its effective move policy. Mirrors
 * `harness_kind_for_policy` on the backend — only the `bot` policy is coded. */
export function harnessKindForPolicy(policy: string | undefined): HarnessKind {
  return policy === 'bot' ? 'coded' : 'llm';
}

/** The old name: pre-split, "loadout" always meant the LLM harness. */
export type Loadout = LlmHarness;

export interface LoadoutValidation {
  ok: boolean;
  tools: { name: string; ok: boolean; error: string | null }[];
  agent_error: string | null;
}

/** A game's harness. `kind` picks which one; omitted, the backend answers with
 * whichever this node's seat would actually run. */
export function getLoadout(gameId: string, kind?: HarnessKind): Promise<Harness> {
  const q = kind ? `?kind=${kind}` : '';
  return apiGet(`/games/loadout/${encodeURIComponent(gameId)}${q}`);
}

export function saveLoadout(gameId: string, harness: Harness): Promise<Harness> {
  return apiPut(`/games/loadout/${encodeURIComponent(gameId)}`, harness);
}

export function validateLoadout(harness: Harness): Promise<LoadoutValidation> {
  return apiPost('/games/loadout/validate', harness);
}

/** The starter `my_agent` source to seed the editor for a fresh agent on a game. */
export function getAgentStarter(gameId: string): Promise<{ game_id: string; agent_code: string }> {
  return apiGet(`/games/agent-starter/${encodeURIComponent(gameId)}`);
}

/** A realistic opening position for a game — the observation + legal actions the
 * Build panel's inspector shows so a player can see what they program against.
 * Cheap (no loadout / model / agent run); resample with a different `seed`. */
export interface SampleObservation {
  ok: boolean;
  error: string | null;
  game_id: string;
  observation: Record<string, unknown>;
  legal_actions: { id: string; label?: string }[];
}

export function fetchSampleObservation(gameId: string, seed = 0): Promise<SampleObservation> {
  return apiGet(`/games/sample-observation?game_id=${encodeURIComponent(gameId)}&seed=${seed}`);
}

/** Compile and run one tool/bot body against a supplied observation — the editor's
 * "test" path, reused by the tutorial to validate a step's bot on a sample position. */
export interface TestToolResult {
  ok: boolean;
  error: string | null;
  result: unknown;
}

export function testTool(
  code: string,
  obs: Record<string, unknown>,
  args: Record<string, unknown> = {},
): Promise<TestToolResult> {
  return apiPost('/games/test-tool', { code, obs, args });
}

/** A shipped starter harness for a game: a titled, blurbed loadout whose `tools`
 * are the templated tool definitions the builder's Tools section offers as a
 * starting point (backend/modules/games/templates.py). */
export interface LoadoutTemplate {
  id: string;
  game_id: string;
  kind: HarnessKind;
  title: string;
  blurb: string;
  loadout: Loadout;
}

/** The shipped templates, optionally narrowed to one game and one harness kind. */
export async function fetchLoadoutTemplates(
  gameId?: string,
  kind?: HarnessKind,
): Promise<LoadoutTemplate[]> {
  const params = new URLSearchParams();
  if (gameId) params.set('game_id', gameId);
  if (kind) params.set('kind', kind);
  const q = params.toString() ? `?${params}` : '';
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

/**
 * Sign-in moved to the core account service (`packages/core/src/account.ts`): one
 * identity is shared by the games ladder and HorribleAssault, and a module must
 * not import another module's internals. Re-exported here so existing call sites
 * keep working — new code should import from `../../account` directly.
 */
export {
  fetchAuthProviders,
  oauthSignIn,
  signInWith,
  signInWithGitHub,
  signInWithRedirect,
  signOut,
  type AuthProviderFlows,
  type SignInPrompt,
  type SignInProvider,
} from '../../account';

/**
 * A game's **RL environment** — the Gymnasium seam.
 *
 * `has_env` is false for every `reasoner` game (bug hunt, RAG race, code golf,
 * test duel): their action is a payload — a patch, an answer — not a point in an
 * action space, so there is nothing to declare and the Train section says so
 * rather than rendering a runner that cannot work.
 */
export interface TrainingCapability {
  self_play: boolean;
  default_episodes: number;
  max_episodes: number;
  /** Whether a learner that converges *in the pane* is a sensible ambition here. */
  in_app_optimizer: boolean;
  hint: string;
}

export interface EnvInfo {
  game_id: string;
  has_env: boolean;
  reason: string | null;
  observation_space: string | null;
  n_actions: number | null;
  training: TrainingCapability | null;
}

export function fetchEnvInfo(gameId: string): Promise<EnvInfo> {
  return apiGet(`/games/env/${encodeURIComponent(gameId)}`);
}

/** One headless training run: N episodes of a script bot against a chosen opponent. */
export interface TrainRunResult {
  ok: boolean;
  error: string | null;
  /** Which bot shape was detected: `agent` | `act` | `run` (legacy). */
  shape: string;
  episodes: number;
  wins: number;
  draws: number;
  losses: number;
  /** Moves outside the action mask. Counted, never silently repaired. */
  illegal: number;
  truncated: number;
  mean_reward: number;
  curve: number[];
  elapsed_ms: number;
  stopped_early: boolean;
  sample: {
    episode: number;
    seat: number;
    reward: number;
    illegal: boolean;
    moves: { seat: number; action?: string; illegal?: boolean; returned?: string }[];
    final: Record<string, unknown> | null;
  } | null;
}

export function runTraining(body: {
  game_id: string;
  code: string;
  opponent: string;
  episodes: number;
  seed?: number;
}): Promise<TrainRunResult> {
  return apiPost('/games/train/run', { seed: 0, ...body });
}
