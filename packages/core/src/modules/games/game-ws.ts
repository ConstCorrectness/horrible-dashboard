/**
 * Client for the `/ws` `games` channel: the node relays central-game-server events
 * (lobby tables, your_turn, public_state, game_over) here, and panels drive the
 * node's connection back through it.
 *
 * State lives in a module-level store (not per-component) because dockview unmounts
 * inactive tab panes — the lobby and the board must survive tab switches and keep
 * receiving live updates. Panels read it with `useGames()`.
 */
import { useSyncExternalStore } from 'react';

import { registry } from '../../registry';
import { toastsStore } from '../../toasts';
import { sendChannel, subscribeChannel } from '../../ws';

/** Reveal the Game Board companion inside the Games group shell (opening the group
 * if needed). Called when a match becomes live so the board pops automatically. */
export function revealBoard(): void {
  registry.revealCompanion('games.board');
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
  events: TownEvent[];
}

export interface GamesState {
  connected: boolean;
  accountId: string | null;
  selfPlay: boolean;
  tables: TableInfo[];
  gameId: string | null;
  board: PublicState | null;
  yourTurn: YourTurn | null;
  over: { winner: number | null; returns: Record<string, number> } | null;
  thinkingSeat: number | null;
  challengeRunning: boolean;
  challengeReport: ChallengeReport | null;
  town: TownState;
}

const initialTown: TownState = {
  joined: false,
  tick: 0,
  phase: 'morning',
  places: [],
  residents: [],
  events: [],
};

const initial: GamesState = {
  connected: false,
  accountId: null,
  selfPlay: false,
  tables: [],
  gameId: null,
  board: null,
  yourTurn: null,
  over: null,
  thinkingSeat: null,
  challengeRunning: false,
  challengeReport: null,
  town: initialTown,
};

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

// One subscription for the whole app, set up on first import of this module.
subscribeChannel('games', (msg) => {
  const d = (msg.data ?? {}) as Record<string, unknown>;
  switch (msg.event) {
    case 'error':
      toastsStore.add('error', 'Games', (d.message as string) ?? 'Games error');
      break;
    case 'authed':
      set({
        connected: Boolean(d.connected),
        accountId: (d.account_id as string) ?? null,
        selfPlay: Boolean(d.self_play),
        ...(d.connected ? {} : { board: null, yourTurn: null, over: null, town: initialTown }),
      });
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
      set({ thinkingSeat: null });
      break;
    case 'challenge_scenarios':
      set({ challengeRunning: true, challengeReport: null });
      break;
    case 'challenge_report':
      set({ challengeRunning: false, challengeReport: d as unknown as ChallengeReport });
      break;
    case 'game_over':
      set({
        over: {
          winner: (d.winner as number | null) ?? null,
          returns: (d.returns as Record<string, number>) ?? {},
        },
        yourTurn: null,
        thinkingSeat: null,
      });
      break;
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
