/**
 * Client for the `/ws` `games` channel: the node relays central-game-server events
 * (lobby tables, your_turn, public_state, game_over) here, and panels drive the
 * node's connection back through it.
 *
 * State lives in a module-level store (not per-component) because the frame unmounts
 * inactive tab panes — the lobby and the board must survive tab switches and keep
 * receiving live updates. Panels read it with `useGames()`.
 */
import { useSyncExternalStore } from 'react';

import { revealRegionView } from '../../layout/controller';
import { registry } from '../../registry';
import { toastsStore } from '../../toasts';
import { sendChannel, subscribeChannel } from '../../ws';
import { resetArenaView } from './arena-view';
import { sfx } from './sfx';

/** Pop the Game Board when a match becomes live. If a **standalone** Game Board pane
 * is already open (e.g. the "Coding Harnesses" workspace seeds one), focus that pane
 * so we don't render a second board inside the lobby; otherwise reveal the board
 * as the lobby's bottom region (opening the lobby if needed). */
export function revealBoard(): void {
  const lc = registry.layoutController;
  const standalone = lc?.listOpenPanes().find((p) => p.id === 'games.board');
  if (standalone) {
    lc!.focusPane(standalone.instanceId);
    return;
  }
  revealRegionView('games.board');
}

export interface TableInfo {
  id: string;
  game_id: string;
  status: 'open' | 'playing' | 'done';
  seats: (string | null)[];
  capacity: number;
}

/** A game's spectator state (shape varies per game; tic-tac-toe fields shown). */
export interface PublicState {
  game: string;
  board?: (string | null)[];
  turn?: number | null;
  winner?: number | null;
  [k: string]: unknown;
}

export interface YourTurn {
  game_id: string;
  seat: number;
  observation: Record<string, unknown>;
  legal_actions: { id: string; label: string }[];
}

/** Who is sitting in a seat — from the server's `match_info` broadcast. */
export interface SeatProfile {
  account_id: string;
  display_name: string;
  handle: string | null;
  avatar: string;
  rating: number | null;
  tier: string | null;
  level: number;
  is_bot: boolean;
  model_label: string | null;
}

/** One reasoning step from *your own* agent, streamed live while it thinks. */
export interface TraceStep {
  kind: 'assistant' | 'tool_result' | 'chose' | 'fallback';
  content?: string;
  tool_calls?: { name: string; arguments: string }[];
  name?: string;
  result?: string;
  action_id?: string;
}

export interface TraceEntry {
  seat: number;
  idx: number;
  step: TraceStep;
}

/** The negotiated terms of a match (mirrors the server's Ruleset model). */
export interface Ruleset {
  game_id: string;
  best_of: number;
  difficulty: string;
  move_timeout_s: number | null;
  edit_phase_s: number;
  model_class: 'any' | 'local';
  rated: boolean;
}

/** An incoming challenge/rematch/counter offer awaiting your response. */
export interface ChallengeIncoming {
  offer_id: string;
  kind: 'challenge' | 'rematch' | 'counter';
  from_id: string;
  from_name: string;
  game_name: string;
  ruleset: Ruleset;
}

/** Your live ranked-queue slot (null when not queued). */
export interface QueueState {
  gameId: string;
  difficulty: string;
  waitingS: number;
  window: number;
}

/** Between-games score of a best-of-N series. */
export interface SeriesState {
  best_of: number;
  game_index: number;
  wins: number[];
  seats: string[];
  intermission_s: number;
}

/** Your post-game rating/XP movement (from the server's rating_update push). */
export interface RatingUpdate {
  game_id: string;
  rating?: number;
  delta?: number;
  tier?: string;
  placement_games?: number;
  xp: number;
  level: number;
}

export interface ChallengeReport {
  game_id: string;
  correct: number;
  total: number;
  score: number;
  covered: number;
  category_count: number;
  best: boolean;
  categories: Record<string, { passed: number; total: number }>;
}

export interface TownResident {
  account_id: string;
  name: string;
  avatar: string;
  place: string;
  asleep: boolean;
  x?: number;
  z?: number;
  energy?: number;
  strength?: number;
  wealth?: number;
  house_owned?: boolean;
  house_id?: string | null;
  job?: string;
  job_site?: string;
  inventory?: Record<string, number>;
}

/** A cottage lot on the residential lane; `owner` is set once it's bought. */
export interface TownHouse {
  id: string;
  x: number;
  z: number;
  owner: string | null;
  owner_id: string | null;
}

export interface TownEvent {
  tick: number;
  type: string;
  name: string;
  avatar: string;
  place: string;
  text: string;
}

/** The fish tank: AgentTown's spectator state, fed by `town_joined`/`town_state`. */
export interface TownState {
  joined: boolean;
  tick: number;
  phase: string;
  places: string[];
  residents: TownResident[];
  houses: TownHouse[];
  events: TownEvent[];
}

// ---- The Plaza (human social layer) ----------------------------------------

/** A real user standing in a room (the Habbo-style floor renders these). */
export interface SocialOccupant {
  account_id: string;
  name: string;
  avatar: string;
  x: number;
  y: number;
}

/** A speech (or emote) bubble that popped over someone's avatar. */
export interface SocialBubble {
  id: string;
  account_id: string;
  name: string;
  avatar: string;
  text: string;
  emote: boolean;
  x: number;
  y: number;
  ts: number;
}

/** A room in the lobby (declutters the main plaza). */
export interface RoomInfo {
  id: string;
  name: string;
  icon: string;
}

/** One entry in the global "who's online" roster. */
export interface RosterEntry {
  account_id: string;
  name: string;
  avatar: string;
  room: string;
  activity: string;
  level: number;
}

/** A friend / friend-request row. `online` is set on accepted friends. */
export interface FriendEntry {
  account_id: string;
  display_name: string;
  avatar: string;
  level: number;
  online?: boolean;
}

/** Your gamified profile (avatar + XP + derived level + unique handle). */
export interface Profile {
  account_id: string;
  avatar: string;
  bio: string;
  handle?: string | null;
  xp: number;
  level: number;
  level_floor: number;
  next_level_xp: number | null;
}

/** An incoming game challenge from another user (host already opened the table). */
export interface SocialInvite {
  table_id: string;
  game_id: string;
  game_name: string;
  from_id: string;
  from_name: string;
}

export interface SocialState {
  joined: boolean;
  room: string;
  rooms: RoomInfo[];
  occupants: SocialOccupant[];
  bubbles: SocialBubble[];
  roster: RosterEntry[];
  friends: FriendEntry[];
  pending: FriendEntry[];
  profile: Profile | null;
  invite: SocialInvite | null;
}

export interface GamesState {
  connected: boolean;
  /** True while `ensureConnected` is waiting on the server's `authed` ack — the
   * board and the hub's status chip render a "connecting…" state from this. */
  connecting: boolean;
  accountId: string | null;
  selfPlay: boolean;
  /** Server capabilities from AUTHED — feature-detect before showing newer UI. */
  caps: string[];
  tables: TableInfo[];
  gameId: string | null;
  board: PublicState | null;
  yourTurn: YourTurn | null;
  over: { winner: number | null; returns: Record<string, number> } | null;
  thinkingSeat: number | null;
  /** Seat identities for the live match (null before `match_info` arrives). */
  matchSeats: SeatProfile[] | null;
  /** The live match's table (rematch offers target it). */
  tableId: string | null;
  /** Where the live match's replay will be saved once it finishes. */
  replayId: string | null;
  /** Wall-clock ms when `match_info` arrived. View chrome that must fire once
   * per match (the VS splash) derives from this instead of a mount effect, so a
   * tab-switch remount doesn't replay it. */
  matchStartedAt: number | null;
  /** Your own agent's live reasoning feed for the current match. */
  trace: TraceEntry[];
  /** Your ranked-queue slot, an incoming offer, the series score, last rating move. */
  queue: QueueState | null;
  offer: ChallengeIncoming | null;
  series: SeriesState | null;
  lastRating: RatingUpdate | null;
  challengeRunning: boolean;
  challengeReport: ChallengeReport | null;
  town: TownState;
  social: SocialState;
}

const initialTown: TownState = {
  joined: false,
  tick: 0,
  phase: 'morning',
  places: [],
  residents: [],
  houses: [],
  events: [],
};

const initialSocial: SocialState = {
  joined: false,
  room: 'plaza',
  rooms: [],
  occupants: [],
  bubbles: [],
  roster: [],
  friends: [],
  pending: [],
  profile: null,
  invite: null,
};

const initial: GamesState = {
  connected: false,
  connecting: false,
  accountId: null,
  selfPlay: false,
  caps: [],
  tables: [],
  gameId: null,
  board: null,
  yourTurn: null,
  over: null,
  thinkingSeat: null,
  matchSeats: null,
  tableId: null,
  replayId: null,
  matchStartedAt: null,
  trace: [],
  queue: null,
  offer: null,
  series: null,
  lastRating: null,
  challengeRunning: false,
  challengeReport: null,
  town: initialTown,
  social: initialSocial,
};

/** Keep the live reasoning feed bounded (a long match streams a lot of steps). */
const TRACE_LIMIT = 500;

let state: GamesState = initial;
const listeners = new Set<() => void>();
// The tick whose events are already in the ticker (a join snapshot and that
// tick's broadcast can both carry them — append each tick's batch only once).
let lastEventTick = -1;
const EVENT_TICKER_LIMIT = 60;

function townUpdate(d: Record<string, unknown>, joined: boolean): TownState {
  const tick = Number(d.tick ?? state.town.tick);
  const incoming = (d.events as TownEvent[] | undefined) ?? [];
  let events = state.town.events;
  if (tick > lastEventTick && incoming.length > 0) {
    events = [...events, ...incoming].slice(-EVENT_TICKER_LIMIT);
  }
  if (tick > lastEventTick) lastEventTick = tick;
  return {
    joined,
    tick,
    phase: String(d.phase ?? state.town.phase),
    places: (d.places as string[] | undefined) ?? state.town.places,
    residents: (d.residents as TownResident[] | undefined) ?? state.town.residents,
    houses: (d.houses as TownHouse[] | undefined) ?? state.town.houses,
    events,
  };
}

function set(patch: Partial<GamesState>): void {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

function upsertTable(table: TableInfo): TableInfo[] {
  const rest = state.tables.filter((t) => t.id !== table.id);
  return [...rest, table];
}

// Server error codes that are agent-protocol churn, not something the watching
// human can act on: a move landing after the referee's clock already resolved the
// turn (tick games, self-play relaying both seats) is *expected* and the server
// handles it by ignoring the move. Toasting these nags the spectator.
const SILENT_ERROR_CODES = new Set(['not_your_turn']);
// Collapse identical error toasts arriving in a burst (e.g. one per tick).
let lastErrorToast = { text: '', at: 0 };

// One subscription for the whole app, set up on first import of this module.
subscribeChannel('games', (msg) => {
  const d = (msg.data ?? {}) as Record<string, unknown>;
  switch (msg.event) {
    case 'error': {
      const text = (d.message as string) ?? 'Games error';
      if (SILENT_ERROR_CODES.has(String(d.code ?? ''))) {
        console.debug('[games] suppressed server error:', d.code, text);
        break;
      }
      const now = Date.now();
      if (text === lastErrorToast.text && now - lastErrorToast.at < 10_000) break;
      lastErrorToast = { text, at: now };
      toastsStore.add('error', 'Games', text);
      break;
    }
    case 'authed': {
      const nowConnected = Boolean(d.connected);
      const nowSelfPlay = Boolean(d.self_play);
      set({
        connected: nowConnected,
        accountId: (d.account_id as string) ?? null,
        selfPlay: nowSelfPlay,
        caps: (d.caps as string[] | undefined) ?? state.caps,
        ...(nowConnected
          ? { connecting: false }
          : {
              board: null,
              yourTurn: null,
              over: null,
              matchSeats: null,
              replayId: null,
              matchStartedAt: null,
              trace: [],
              town: initialTown,
              social: initialSocial,
            }),
      });
      if (nowConnected) {
        settleWaiters((w) =>
          w.selfPlay === nowSelfPlay
            ? w.resolve()
            : w.reject(new Error('reconnected in a different mode')),
        );
      } else if (pendingReconnect !== null) {
        // Disconnect ack of a mode switch — fire the second half now.
        const mode = pendingReconnect;
        pendingReconnect = null;
        gamesConnect(mode);
      } else if (connectWaiters.length === 0) {
        set({ connecting: false });
      }
      // NOTE: a `connected:false` while waiters are pending is NOT treated as
      // failure — the node emits transient falses while tearing down a stale
      // connection mid-connect, and auth against a cold server can take a
      // couple of seconds. Real failures reach the user through the channel's
      // `error` event (toast); the waiters fall to their own timeout.
      break;
    }
    case 'match_info':
      sfx.matchStart();
      // A match just started: remember who's in each seat and reset the feed.
      resetArenaView();
      set({
        matchSeats: (d.seats as SeatProfile[]) ?? null,
        tableId: (d.table_id as string) ?? null,
        replayId: (d.replay_id as string) ?? null,
        gameId: (d.game_id as string) ?? state.gameId,
        matchStartedAt: Date.now(),
        trace: [],
        over: null,
        queue: null, // a started match consumes any queue slot
      });
      break;
    case 'rating_update': {
      const u = d as unknown as RatingUpdate;
      set({ lastRating: u });
      if (u.delta !== undefined && u.rating !== undefined) {
        const sign = u.delta >= 0 ? '+' : '';
        const tier = u.tier === 'placement' ? `placement ${u.placement_games}/5` : u.tier;
        toastsStore.add(
          u.delta >= 0 ? 'info' : 'warning',
          'Ranked',
          `${u.game_id}: ${sign}${u.delta} → ${Math.round(u.rating)} (${tier})`,
        );
      }
      break;
    }
    case 'queue_status':
      set({
        queue: {
          gameId: String(d.game_id ?? ''),
          difficulty: String(d.difficulty ?? 'standard'),
          waitingS: Number(d.waiting_s ?? 0),
          window: Number(d.window ?? 0),
        },
      });
      break;
    case 'match_found': {
      const opp = d.opponent as SeatProfile | null;
      toastsStore.add(
        'info',
        'Ranked',
        opp
          ? `Match found: ${opp.handle ?? opp.display_name} (${opp.rating ?? '?'})`
          : 'Match found: practice bot',
      );
      set({ queue: null });
      break;
    }
    case 'challenge_incoming':
      set({ offer: d as unknown as ChallengeIncoming });
      toastsStore.add(
        'info',
        'Games',
        `${(d.from_name as string) ?? 'Someone'} ${
          d.kind === 'rematch' ? 'wants a rematch' : 'challenged you'
        }: ${(d.game_name as string) ?? 'a game'}`,
      );
      break;
    case 'challenge_update': {
      const status = String(d.status ?? '');
      if (status === 'declined' || status === 'countered') {
        toastsStore.add('warning', 'Games', `Your challenge was ${status}`);
      } else if (status === 'accepted') {
        toastsStore.add('info', 'Games', 'Challenge accepted — starting…');
      }
      break;
    }
    case 'series_state':
      set({ series: d as unknown as SeriesState });
      break;
    case 'series_over': {
      const wins = (d.wins as number[]) ?? [];
      const seats = (d.seats as string[]) ?? [];
      const w = d.winner_seat as number | null;
      toastsStore.add(
        'info',
        'Series',
        w !== null && w !== undefined
          ? `🏆 ${seats[w] ?? `seat ${w}`} takes the series ${wins.join('–')}`
          : `Series drawn ${wins.join('–')}`,
      );
      set({ series: null });
      break;
    }
    case 'agent_trace': {
      const entry: TraceEntry = {
        seat: Number(d.seat ?? -1),
        idx: Number(d.idx ?? state.trace.length),
        step: (d.step as TraceStep) ?? { kind: 'assistant' },
      };
      set({ trace: [...state.trace, entry].slice(-TRACE_LIMIT) });
      break;
    }
    case 'social_joined':
      set({
        social: {
          ...state.social,
          joined: true,
          room: String(d.room ?? state.social.room),
          rooms: (d.rooms as RoomInfo[] | undefined) ?? state.social.rooms,
          occupants: (d.occupants as SocialOccupant[] | undefined) ?? [],
          bubbles: (d.bubbles as SocialBubble[] | undefined) ?? [],
        },
      });
      break;
    case 'social_state':
      set({
        social: {
          ...state.social,
          room: String(d.room ?? state.social.room),
          occupants: (d.occupants as SocialOccupant[] | undefined) ?? state.social.occupants,
          bubbles: (d.bubbles as SocialBubble[] | undefined) ?? state.social.bubbles,
        },
      });
      break;
    case 'social_roster':
      set({ social: { ...state.social, roster: (d.online as RosterEntry[]) ?? [] } });
      break;
    case 'social_invited':
      set({ social: { ...state.social, invite: d as unknown as SocialInvite } });
      toastsStore.add(
        'info',
        'Games',
        `${(d.from_name as string) ?? 'Someone'} challenged you to ${(d.game_name as string) ?? 'a game'}`,
      );
      break;
    case 'friends':
      set({
        social: {
          ...state.social,
          friends: (d.friends as FriendEntry[]) ?? [],
          pending: (d.pending as FriendEntry[]) ?? [],
        },
      });
      break;
    case 'profile':
      set({ social: { ...state.social, profile: d as unknown as Profile } });
      break;
    case 'town_joined':
      set({ town: townUpdate(d, true) });
      break;
    case 'town_state':
      set({ town: townUpdate(d, state.town.joined) });
      break;
    case 'tables':
      set({ tables: (d.tables as TableInfo[]) ?? [] });
      break;
    case 'table':
      set({ tables: upsertTable(d.table as TableInfo) });
      break;
    case 'your_turn': {
      const wasLive = state.board !== null || state.yourTurn !== null;
      set({ yourTurn: d as unknown as YourTurn, thinkingSeat: Number(d.seat) });
      if (!wasLive) revealBoard(); // a match just went live — pop the board
      break;
    }
    case 'public_state': {
      const wasLive = state.board !== null || state.yourTurn !== null;
      set({
        gameId: (d.game_id as string) ?? state.gameId,
        board: (d.state as PublicState) ?? null,
        over: null,
        thinkingSeat: null,
      });
      if (!wasLive) revealBoard(); // first state of a match — pop the board
      break;
    }
    case 'chose':
      sfx.move();
      set({ thinkingSeat: null });
      break;
    case 'challenge_scenarios':
      set({ challengeRunning: true, challengeReport: null });
      break;
    case 'challenge_report':
      set({ challengeRunning: false, challengeReport: d as unknown as ChallengeReport });
      break;
    case 'game_over': {
      const winner = (d.winner as number | null) ?? null;
      const mySeat = state.matchSeats?.findIndex((s) => s.account_id === state.accountId) ?? -1;
      if (winner !== null && mySeat >= 0) {
        if (winner === mySeat) sfx.win();
        else sfx.lose();
      }
      set({
        over: {
          winner,
          returns: (d.returns as Record<string, number>) ?? {},
        },
        yourTurn: null,
        thinkingSeat: null,
      });
      break;
    }
    default:
      break;
  }
});

export function useGames(): GamesState {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => state,
  );
}

// ---- actions ---------------------------------------------------------------

export function gamesConnect(selfPlay: boolean): void {
  sendChannel('games', 'connect', { selfPlay });
}

export function gamesDisconnect(): void {
  sendChannel('games', 'disconnect', {});
}

// ---- implicit connection ------------------------------------------------------
//
// The UI never shows Connect buttons: play/queue/join flows call
// `ensureConnected(mode)` and the node connects itself (the Plaza and AgentTown
// already work this way). Waiters resolve on the server's `authed` ack.

interface ConnectWaiter {
  selfPlay: boolean;
  resolve: () => void;
  reject: (e: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}
let connectWaiters: ConnectWaiter[] = [];
// Wrong-mode switch: reconnect with this mode once the disconnect is acked, so
// the disconnect → connect pair is serialized through the server instead of
// racing two sends.
let pendingReconnect: boolean | null = null;

function settleWaiters(fn: (w: ConnectWaiter) => void): void {
  const waiters = connectWaiters;
  connectWaiters = [];
  for (const w of waiters) {
    clearTimeout(w.timer);
    fn(w);
  }
}

/** Resolve once the node is connected in the requested mode, connecting (or
 * mode-switching) as needed. Rejects on timeout or a failed/dropped connect. */
export function ensureConnected(selfPlay: boolean, timeoutMs = 10_000): Promise<void> {
  if (state.connected && state.selfPlay === selfPlay && !state.connecting) {
    return Promise.resolve();
  }
  const promise = new Promise<void>((resolve, reject) => {
    const waiter: ConnectWaiter = {
      selfPlay,
      resolve,
      reject,
      timer: setTimeout(() => {
        connectWaiters = connectWaiters.filter((w) => w !== waiter);
        if (connectWaiters.length === 0) set({ connecting: false });
        reject(new Error('game server connection timed out'));
      }, timeoutMs),
    };
    connectWaiters.push(waiter);
  });
  if (!state.connecting) {
    set({ connecting: true });
    if (state.connected) {
      // Connected in the wrong mode: reconnect after the disconnect ack.
      pendingReconnect = selfPlay;
      gamesDisconnect();
    } else {
      gamesConnect(selfPlay);
    }
  }
  return promise;
}

export function gamesListTables(): void {
  sendChannel('games', 'list_tables', {});
}

export function gamesCreateTable(gameId: string): void {
  sendChannel('games', 'create_table', { gameId });
}

export function gamesJoinTable(tableId: string): void {
  sendChannel('games', 'join_table', { tableId });
}

export function gamesRunChallenges(gameId: string): void {
  sendChannel('games', 'run_challenges', { gameId });
}

// ---- ranked queue + negotiation ---------------------------------------------

export function gamesQueueJoin(gameId: string, difficulty = 'standard', placement = false): void {
  sendChannel('games', 'queue_join', { gameId, difficulty, placement });
  // Optimistic slot so the Find Match button flips immediately.
  set({ queue: { gameId, difficulty, waitingS: 0, window: 75 } });
}

export function gamesQueueLeave(): void {
  sendChannel('games', 'queue_leave', {});
  set({ queue: null });
}

/** Propose a match to a specific player with full negotiated terms. */
export function challengeOffer(accountId: string, ruleset: Partial<Ruleset>): void {
  sendChannel('games', 'challenge_offer', { account_id: accountId, ruleset });
}

/** Answer an incoming offer; `counter` sends back an edited ruleset. */
export function challengeRespond(
  offerId: string,
  response: 'accept' | 'decline' | 'counter',
  ruleset?: Partial<Ruleset>,
): void {
  sendChannel('games', 'challenge_respond', { offerId, response, ruleset });
  set({ offer: null });
}

/** Offer the opponent of the just-finished table the same terms again. */
export function rematchOffer(tableId: string): void {
  sendChannel('games', 'rematch_offer', { tableId });
}

/** Clear the incoming-offer card without answering (it expires server-side). */
export function dismissOffer(): void {
  set({ offer: null });
}

// ---- spectating + arcade ----------------------------------------------------

export function watchTable(tableId: string): void {
  sendChannel('games', 'watch_table', { tableId });
}

export function unwatchTable(tableId: string): void {
  sendChannel('games', 'unwatch_table', { tableId });
}

/** Push the current held-key set for the fighter arcade (fire on keydown/up). */
export function arcadeInput(keys: string[]): void {
  sendChannel('games', 'arcade_input', { keys });
}

/** Start a casual (unrated) fighter table — the Plaza arcade cabinet. */
export function startArcadeFighter(): void {
  sendChannel('games', 'create_table', {
    gameId: 'fighter',
    ruleset: { game_id: 'fighter', rated: false },
  });
}

// ---- AgentTown -------------------------------------------------------------

/** Spawn (or wake) your resident. The node auto-connects to the game server. */
export function townJoin(name: string, avatar: string): void {
  sendChannel('games', 'town_join', { name, avatar });
}

export function townLeave(): void {
  sendChannel('games', 'town_leave', {});
  set({ town: { ...state.town, joined: false } });
}

/** Tap the glass: a nudge injected into your resident's next agent tick. */
export function townWhisper(text: string): void {
  sendChannel('games', 'town_whisper', { text });
}

// ---- The Plaza (human social layer) ----------------------------------------

/** Enter the Plaza. The node auto-connects to the game server. */
export function socialJoin(name: string, avatar: string): void {
  sendChannel('games', 'social_join', { name, avatar });
}

export function socialLeave(): void {
  sendChannel('games', 'social_leave', {});
  set({ social: { ...state.social, joined: false } });
}

/** Walk your avatar to (x, y) in the current room (0..100 floor coordinates). */
export function socialMove(x: number, y: number): void {
  sendChannel('games', 'social_move', { x, y });
}

export function socialRoom(room: string): void {
  sendChannel('games', 'social_room', { room });
}

export function socialSay(text: string, emote = false): void {
  sendChannel('games', 'social_say', { text, emote });
}

/** Challenge a user to a game — hosts a table and pings their node to join. */
export function socialInvite(accountId: string, gameId: string): void {
  sendChannel('games', 'social_invite', { account_id: accountId, gameId });
}

export function friendRequest(accountId: string): void {
  sendChannel('games', 'friend_request', { account_id: accountId });
}

export function friendAccept(accountId: string): void {
  sendChannel('games', 'friend_accept', { account_id: accountId });
}

export function friendRemove(accountId: string): void {
  sendChannel('games', 'friend_remove', { account_id: accountId });
}

export function friendList(): void {
  sendChannel('games', 'friend_list', {});
}

export function profileGet(): void {
  sendChannel('games', 'profile_get', {});
}

// Handle is derived from the OAuth username server-side and locked, so it isn't
// settable here — only avatar and bio.
export function profileSet(avatar?: string, bio?: string): void {
  sendChannel('games', 'profile_set', { avatar, bio });
}

/** Clear the incoming-invite banner (after joining or dismissing it). */
export function dismissInvite(): void {
  set({ social: { ...state.social, invite: null } });
}
