/**
 * Client for the `/ws` `lobby` channel: keeps the lobby connection + room/directory
 * snapshot the Lobby widget renders, and exposes the room intents (connect, list,
 * create, join) that drive the backend `LobbyClient`. Joining a room hands off to the
 * peer fabric (direct P2P, relay fallback) — see modules/network lobby.py.
 */
import { onSocketOpen, sendChannel, subscribeChannel } from '../../ws';
import { toastsStore } from '../../toasts';

export interface LobbyRoom {
  id: string;
  name: string;
  host: string;
  host_name: string;
  members: number;
  visibility: string;
  locked: boolean;
}

export interface LobbyNode {
  node_id: string;
  node_name: string;
  status: string;
  capabilities: string[];
}

export interface LobbyState {
  connected: boolean;
  url: string | null;
  rooms: LobbyRoom[];
  directory: LobbyNode[];
}

let state: LobbyState = { connected: false, url: null, rooms: [], directory: [] };
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((l) => l());
}

export function getLobbyState(): LobbyState {
  return state;
}

export function subscribeLobby(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

let started = false;

/** Subscribe to the `lobby` channel and request a snapshot on (re)connect. */
export function initLobby(): void {
  if (started) return;
  started = true;
  subscribeChannel('lobby', (msg) => {
    const d = (msg.data ?? {}) as Record<string, unknown>;
    if (msg.event === 'state') {
      state = {
        connected: Boolean(d.connected),
        url: (d.url as string | null) ?? null,
        rooms: (d.rooms as LobbyRoom[]) ?? [],
        directory: (d.directory as LobbyNode[]) ?? [],
      };
      emit();
    } else if (msg.event === 'rooms') {
      state = { ...state, rooms: (d.rooms as LobbyRoom[]) ?? [] };
      emit();
    } else if (msg.event === 'directory') {
      state = { ...state, directory: (d.directory as LobbyNode[]) ?? [] };
      emit();
    } else if (msg.event === 'joined') {
      toastsStore.add('success', 'Joined room', `Connected to ${String(d.peer ?? 'host')}.`);
    } else if (msg.event === 'error') {
      toastsStore.add('error', 'Lobby', String(d.message ?? 'lobby error'));
    }
  });
  onSocketOpen(() => sendChannel('lobby', 'state', {}));
}

export function lobbyConnect(url?: string): void {
  sendChannel('lobby', 'connect', url ? { url } : {});
}

export function lobbyListRooms(): void {
  sendChannel('lobby', 'list_rooms', {});
}

export function lobbyCreateRoom(name: string): void {
  sendChannel('lobby', 'create_room', { name });
}

export function lobbyJoinRoom(roomId: string, token?: string): void {
  sendChannel('lobby', 'join_room', { roomId, token });
}

export function lobbyLeaveRoom(roomId: string): void {
  sendChannel('lobby', 'leave_room', { roomId });
}
