/**
 * Client mirror of the server-held music session and library.
 *
 * One per browser window, read by every music surface — the player pane, the
 * shell's now-playing indicator, the board widget — because the backend already
 * made the session singular (backend/modules/music/session.py). Living here rather
 * than in a component is what lets the music survive a pane unmount: panes unmount
 * on every tab and workspace switch.
 *
 * The same two rules as karaoke's store:
 *
 * * **Drop stale broadcasts.** Every mutation bumps `revision`; an older one is a
 *   reconnect replay or a race, and rendering it makes the queue jump backwards.
 * * **`progress` is not `state`.** Position arrives ~1 Hz as its own light event.
 *   It is kept in a separate slot so a 1 Hz tick re-renders the scrubber, not the
 *   whole library.
 */
import { subscribeChannel, type WsMessage } from '../../ws';
import {
  getPlayer,
  getStatus,
  listJobs,
  listPlaylists,
  listTracks,
  type ImportJob,
  type MusicStatus,
  type PlayerState,
  type Playlist,
  type Track,
} from './api';

// --- reactive core (useSyncExternalStore) ---

let version = 0;
let progressVersion = 0;
const listeners = new Set<() => void>();
const progressListeners = new Set<() => void>();

function emit(): void {
  version += 1;
  for (const l of listeners) l();
}

function emitProgress(): void {
  progressVersion += 1;
  for (const l of progressListeners) l();
}

export function subscribeMusic(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function musicVersion(): number {
  return version;
}

export function subscribeProgress(listener: () => void): () => void {
  progressListeners.add(listener);
  return () => {
    progressListeners.delete(listener);
  };
}

export function progressVersionOf(): number {
  return progressVersion;
}

// --- state ---

export const EMPTY_STATE: PlayerState = {
  now_playing: null,
  playing: false,
  position: 0,
  duration: null,
  volume: 0.8,
  shuffle: false,
  repeat: 'off',
  radio: false,
  queue: [],
  history: [],
  output_client: null,
  revision: 0,
};

let player: PlayerState = EMPTY_STATE;
let progress = { position: 0, duration: null as number | null };
let tracks: Track[] = [];
let trackTotal = 0;
let playlists: Playlist[] = [];
let jobs: ImportJob[] = [];
let status: MusicStatus | null = null;
let loaded = false;
const stateListeners = new Set<(state: PlayerState) => void>();

export const getPlayerState = (): PlayerState => player;
export const getProgress = (): { position: number; duration: number | null } => progress;
export const getTracks = (): Track[] => tracks;
export const getTrackTotal = (): number => trackTotal;
export const getPlaylists = (): Playlist[] => playlists;
export const getJobs = (): ImportJob[] => jobs;
export const getMusicStatus = (): MusicStatus | null => status;

/** Non-React listeners on each adopted state — the audio element's reconciler. */
export function onPlayerState(listener: (state: PlayerState) => void): () => void {
  stateListeners.add(listener);
  return () => {
    stateListeners.delete(listener);
  };
}

/**
 * Adopt a `PlayerState` from a response or a broadcast, unless it's older than what
 * we hold. Every mutating call funnels through here.
 */
export function applyPlayerState(next: PlayerState): void {
  if (next.revision < player.revision) return;
  player = next;
  progress = { position: next.position, duration: next.duration };
  emit();
  emitProgress();
  for (const l of stateListeners) l(next);
}

export function applyProgress(position: number, duration: number | null): void {
  progress = { position, duration: duration ?? progress.duration };
  emitProgress();
}

export function upsertTrack(track: Track): void {
  const index = tracks.findIndex((t) => t.id === track.id);
  tracks = index === -1 ? [track, ...tracks] : tracks.map((t) => (t.id === track.id ? track : t));
  if (index === -1) trackTotal += 1;
  emit();
}

function removeTracks(ids: string[]): void {
  const drop = new Set(ids);
  const before = tracks.length;
  tracks = tracks.filter((t) => !drop.has(t.id));
  trackTotal = Math.max(0, trackTotal - (before - tracks.length));
  emit();
}

function upsertJob(job: ImportJob): void {
  const index = jobs.findIndex((j) => j.id === job.id);
  jobs = index === -1 ? [job, ...jobs] : jobs.map((j) => (j.id === job.id ? job : j));
  emit();
}

export async function refreshTracks(search = '', sort = 'added'): Promise<void> {
  const res = await listTracks({ search, sort, limit: 2000 });
  tracks = res.tracks;
  trackTotal = res.total;
  emit();
}

export async function refreshPlaylists(): Promise<void> {
  playlists = (await listPlaylists()).playlists;
  emit();
}

export async function refreshJobs(): Promise<void> {
  jobs = (await listJobs()).jobs;
  emit();
}

export async function refreshStatus(): Promise<void> {
  status = await getStatus();
  emit();
}

/**
 * Pull the session once per window, and the library lazily. Idempotent: every
 * music surface calls it on mount.
 */
export async function ensureLoaded(): Promise<void> {
  if (loaded) return;
  loaded = true;
  try {
    applyPlayerState(await getPlayer());
  } catch {
    // Backend not up yet — let the next mount try again.
    loaded = false;
  }
}

let libraryLoaded = false;

/** The library, playlists, jobs and capabilities — only the panes need these. */
export async function ensureLibraryLoaded(): Promise<void> {
  if (libraryLoaded) return;
  libraryLoaded = true;
  try {
    await Promise.all([refreshTracks(), refreshPlaylists(), refreshJobs(), refreshStatus()]);
  } catch {
    libraryLoaded = false;
  }
}

let unsubscribe: (() => void) | null = null;
let playlistRefresh: ReturnType<typeof setTimeout> | null = null;

/** Attach the `music` channel listener. Idempotent; safe under StrictMode. */
export function connectMusic(): void {
  if (unsubscribe) return;
  unsubscribe = subscribeChannel('music', (msg: WsMessage) => {
    const data = msg.data as Record<string, unknown>;
    if (msg.event === 'state') {
      applyPlayerState(msg.data as PlayerState);
    } else if (msg.event === 'progress') {
      applyProgress(Number(data.position) || 0, (data.duration as number | null) ?? null);
    } else if (msg.event === 'track') {
      if (libraryLoaded) upsertTrack(msg.data as Track);
    } else if (msg.event === 'job') {
      upsertJob(msg.data as ImportJob);
    } else if (msg.event === 'library') {
      if (Array.isArray(data.removed)) removeTracks(data.removed as string[]);
      if (data.playlists && libraryLoaded && !playlistRefresh) {
        // Coalesced: an import job reports after every track.
        playlistRefresh = setTimeout(() => {
          playlistRefresh = null;
          void refreshPlaylists().catch(() => {});
        }, 400);
      }
    }
  });
}

/** Test-only: drop everything so cases don't leak into each other. */
export function resetMusicForTests(): void {
  player = EMPTY_STATE;
  progress = { position: 0, duration: null };
  tracks = [];
  trackTotal = 0;
  playlists = [];
  jobs = [];
  status = null;
  loaded = false;
  libraryLoaded = false;
  stateListeners.clear();
  unsubscribe?.();
  unsubscribe = null;
}
