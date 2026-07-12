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

/** Your gamified profile (avatar + XP + derived level). */
export interface Profile {
  account_id: string;
  avatar: string;
  bio: string;
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
  social: initialSocial,
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
        ...(d.connected
          ? {}
          : { board: null, yourTurn: null, over: null, town: initialTown, social: initialSocial }),
      });
      break;
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

export function profileSet(avatar?: string, bio?: string): void {
  sendChannel('games', 'profile_set', { avatar, bio });
}

/** Clear the incoming-invite banner (after joining or dismissing it). */
export function dismissInvite(): void {
  set({ social: { ...state.social, invite: null } });
}
