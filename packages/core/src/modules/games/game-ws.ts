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

import { sendChannel, subscribeChannel } from '../../ws';

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
}

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
};

let state: GamesState = initial;
const listeners = new Set<() => void>();

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
    case 'authed':
      set({
        connected: Boolean(d.connected),
        accountId: (d.account_id as string) ?? null,
        selfPlay: Boolean(d.self_play),
        ...(d.connected ? {} : { board: null, yourTurn: null, over: null }),
      });
      break;
    case 'tables':
      set({ tables: (d.tables as TableInfo[]) ?? [] });
      break;
    case 'table':
      set({ tables: upsertTable(d.table as TableInfo) });
      break;
    case 'your_turn':
      set({ yourTurn: d as unknown as YourTurn, thinkingSeat: Number(d.seat) });
      break;
    case 'public_state':
      set({
        gameId: (d.game_id as string) ?? state.gameId,
        board: (d.state as PublicState) ?? null,
        over: null,
        thinkingSeat: null,
      });
      break;
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
