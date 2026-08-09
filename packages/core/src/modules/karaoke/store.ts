/**
 * Client mirror of the server-held karaoke session.
 *
 * There is exactly one of these per browser and every karaoke pane reads from it,
 * because the backend already made the session singular (see
 * backend/modules/karaoke/session.py). State lives here rather than in a component
 * so it survives a pane remount — which matters more here than usual: switching
 * workspaces unmounts the stage, and a queue that lived in the stage's `useState`
 * would evaporate mid-party.
 *
 * Two rules the `karaoke` `/ws` handler enforces:
 *
 * * **Drop stale broadcasts.** Every server mutation bumps `revision`; a message
 *   that arrives out of order (a reconnect replay, two mutations racing) carries an
 *   older one and is ignored. Without this the queue visibly jumps backwards.
 * * **`progress` is not `state`.** The stage pings its position once a second and
 *   the server rebroadcasts only `{position, duration}` — merging that into the
 *   full state would mean re-rendering the queue list at 1 Hz.
 */
import { subscribeChannel, type WsMessage } from '../../ws';
import {
  getPlayer,
  getStatus,
  listSongs,
  type KaraokeStatus,
  type PlayerState,
  type SongModel,
} from './api';

// --- reactive core (useSyncExternalStore) ---
let version = 0;
const listeners = new Set<() => void>();

function emit(): void {
  version += 1;
  for (const l of listeners) l();
}

export function subscribeKaraoke(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function karaokeVersion(): number {
  return version;
}

// --- state ---

const EMPTY_STATE: PlayerState = {
  now_playing: null,
  playing: false,
  position: 0,
  duration: null,
  volume: 1,
  semitones: 0,
  queue: [],
  history: [],
  autoplay: true,
  revision: 0,
};

let player: PlayerState = EMPTY_STATE;
let songs: SongModel[] = [];
let status: KaraokeStatus | null = null;
let loaded = false;

export function getPlayerState(): PlayerState {
  return player;
}

export function getSongs(): SongModel[] {
  return songs;
}

export function getKaraokeStatus(): KaraokeStatus | null {
  return status;
}

/**
 * Adopt a `PlayerState` from a response or a broadcast, unless it's older than
 * what we already have. Every mutating API call funnels through here, so a caller
 * never has to think about ordering.
 */
export function applyPlayerState(next: PlayerState): void {
  if (next.revision < player.revision) return;
  player = next;
  emit();
}

function applyProgress(position: number, duration: number | null): void {
  player = { ...player, position, duration: duration ?? player.duration };
  emit();
}

function upsertSong(song: SongModel): void {
  const index = songs.findIndex((s) => s.id === song.id);
  songs = index === -1 ? [song, ...songs] : songs.map((s) => (s.id === song.id ? song : s));
  emit();
}

export async function refreshSongs(search = ''): Promise<void> {
  const response = await listSongs(search);
  songs = response.songs;
  emit();
}

/**
 * Pull the session and library once per browser. Idempotent: several panes mount
 * at once when the Karaoke workspace opens, and each one calls this.
 */
export async function ensureLoaded(): Promise<void> {
  if (loaded) return;
  loaded = true;
  try {
    const [state, songList, capabilities] = await Promise.all([
      getPlayer(),
      listSongs(),
      getStatus(),
    ]);
    applyPlayerState(state);
    songs = songList.songs;
    status = capabilities;
    emit();
  } catch {
    // A backend that isn't up yet shouldn't wedge the panes permanently — let the
    // next mount try again.
    loaded = false;
  }
}

let unsubscribe: (() => void) | null = null;

/** Attach the `karaoke` channel listener. Idempotent; safe under StrictMode. */
export function connectKaraoke(): void {
  if (unsubscribe) return;
  unsubscribe = subscribeChannel('karaoke', (msg: WsMessage) => {
    if (msg.event === 'state') {
      applyPlayerState(msg.data as PlayerState);
    } else if (msg.event === 'progress') {
      const data = msg.data as { position: number; duration: number | null };
      applyProgress(data.position, data.duration);
    } else if (msg.event === 'song') {
      upsertSong(msg.data as SongModel);
    }
  });
}

/** Test-only: drop everything so cases don't leak into each other. */
export function resetKaraokeForTests(): void {
  player = EMPTY_STATE;
  songs = [];
  status = null;
  loaded = false;
  unsubscribe?.();
  unsubscribe = null;
}
