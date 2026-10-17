/**
 * Typed client for the music backend (`/api/music/*`).
 *
 * Mirrors backend/modules/music/models.py. Every session mutation returns the full
 * `PlayerState` and also broadcasts it on the `music` channel, so a caller never
 * re-fetches after acting and every other client stays in step.
 */
import { apiDelete, apiGet, apiPatch, apiPost } from '../../api';
import { apiUrl } from '../../origin';

export type TrackStatus = 'queued' | 'downloading' | 'ready' | 'failed' | 'needs_review';
export type RepeatMode = 'off' | 'one' | 'all';

export interface Candidate {
  source: string;
  ref: string;
  title: string;
  artist: string;
  channel?: string;
  duration: number | null;
  url?: string;
  art_url?: string | null;
  /** Resolver scoring, on review candidates. */
  score?: number;
  why?: string[];
  /** Already in the library: offer "play" instead of "download". */
  track_id?: string | null;
  album?: string;
}

export interface Track {
  id: string;
  title: string;
  artist: string;
  album: string;
  album_artist: string;
  track_no: number | null;
  disc_no: number | null;
  year: string;
  genre: string;
  duration: number | null;
  bitrate: number | null;
  size_bytes: number | null;
  status: TrackStatus;
  error: string | null;
  source: string;
  source_ref: string;
  external_ids: Record<string, string>;
  art_hash: string;
  match_confidence: number | null;
  candidates: Candidate[];
  locked_fields: string[];
  play_count: number;
  skip_count: number;
  last_played: string | null;
  rating: number | null;
  added_at: string | null;
}

/** A seat in the play order. `entry_id`, not `track_id`, is what removal addresses. */
export interface QueueEntry {
  entry_id: string;
  track_id: string;
  title: string;
  artist: string;
  album: string;
  duration: number | null;
  art_hash: string;
  /** The file isn't on disk yet (queued while downloading). */
  ready: boolean;
  /** `radio`, `dj`, `playlist:<name>` — why it's here, when not a person's pick. */
  reason: string;
  played_at: string | null;
}

export interface PlayerState {
  now_playing: QueueEntry | null;
  playing: boolean;
  position: number;
  duration: number | null;
  volume: number;
  shuffle: boolean;
  repeat: RepeatMode;
  radio: boolean;
  queue: QueueEntry[];
  history: QueueEntry[];
  /** The one client (window) that plays the audio. */
  output_client: string | null;
  revision: number;
}

export interface Playlist {
  id: string;
  name: string;
  origin: string;
  description: string;
  track_count: number;
  created_at: string | null;
  updated_at: string | null;
  tracks?: Track[] | null;
}

export interface ProviderRow {
  id: string;
  label: string;
  configured: boolean;
  kind?: 'account' | 'file';
}

export interface MusicStatus {
  ytdlp: boolean;
  track_count: number;
  files_dir: string;
  sources: ProviderRow[];
  importers: ProviderRow[];
  listeners: ProviderRow[];
  scrobbles_pending: number;
  needs_review: number;
}

export interface ImportJob {
  id: string;
  importer: string;
  ref: string;
  name: string;
  origin: string;
  playlist_id: string;
  status: 'running' | 'done' | 'failed' | 'cancelled';
  note: string;
  total: number;
  finished: number;
  counts: Record<string, number>;
  created_at: string | null;
  updated_at: string | null;
}

export interface AccountPlaylist {
  ref: string;
  name: string;
  track_count: number | null;
  owner: string;
  note: string;
}

export interface Lyrics {
  track_id: string;
  source: string;
  synced: [number, string][];
  plain: string;
}

export interface ArtistRow {
  name: string;
  tracks: number;
  albums: number;
  plays: number | null;
}

export interface AlbumRow {
  name: string;
  artist: string;
  year: string;
  tracks: number;
  art_hash: string;
}

export type QueueMode = 'play' | 'next' | 'queue' | 'replace';

const enc = encodeURIComponent;

// --- library ---

export function listTracks(
  params: {
    search?: string;
    artist?: string;
    album?: string;
    status?: TrackStatus;
    sort?: string;
    limit?: number;
  } = {},
): Promise<{ tracks: Track[]; total: number }> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params))
    if (v !== undefined && v !== '') qs.set(k, String(v));
  const tail = qs.toString();
  return apiGet(`/music/tracks${tail ? `?${tail}` : ''}`);
}

export const editTrack = (id: string, fields: Partial<Track>): Promise<Track> =>
  apiPatch(`/music/tracks/${enc(id)}`, fields);
export const deleteTrack = (id: string): Promise<{ ok: boolean }> =>
  apiDelete(`/music/tracks/${enc(id)}`);
export const enrichTrack = (id: string): Promise<Track> =>
  apiPost(`/music/tracks/${enc(id)}/enrich`, {});
export const listArtists = (): Promise<{ artists: ArtistRow[] }> => apiGet('/music/artists');
export const listAlbums = (artist = ''): Promise<{ albums: AlbumRow[] }> =>
  apiGet(`/music/albums${artist ? `?artist=${enc(artist)}` : ''}`);
export const getStatus = (): Promise<MusicStatus> => apiGet('/music/status');
export const getLyrics = (id: string): Promise<Lyrics> => apiGet(`/music/lyrics/${enc(id)}`);

export function searchSources(
  q: string,
  limit = 10,
): Promise<{ query: string; candidates: Candidate[]; notes: string[] }> {
  return apiGet(`/music/search?q=${enc(q)}&limit=${limit}`);
}

export function download(body: {
  source?: string;
  ref?: string;
  url?: string;
  title?: string;
  artist?: string;
  then?: 'play' | 'next' | 'queue' | 'none';
}): Promise<Track> {
  return apiPost('/music/download', body);
}

export const importFiles = (
  paths: string[],
  mode: 'copy' | 'move' = 'copy',
): Promise<{ imported: Track[]; skipped: string[]; failed: string[] }> =>
  apiPost('/music/import/files', { paths, mode });

// --- playlists ---

export const listPlaylists = (): Promise<{ playlists: Playlist[] }> => apiGet('/music/playlists');
export const getPlaylist = (id: string): Promise<Playlist> => apiGet(`/music/playlists/${enc(id)}`);
export const createPlaylist = (
  name: string,
  trackIds: string[] = [],
  fromQueue = false,
): Promise<Playlist> =>
  apiPost('/music/playlists', { name, track_ids: trackIds, from_queue: fromQueue });
export const updatePlaylist = (
  id: string,
  body: { name?: string; track_ids?: string[] },
): Promise<Playlist> => apiPatch(`/music/playlists/${enc(id)}`, body);
export const addToPlaylist = (id: string, trackIds: string[]): Promise<Playlist> =>
  apiPost(`/music/playlists/${enc(id)}/tracks`, { track_ids: trackIds });
export const removeFromPlaylist = (id: string, trackIds: string[]): Promise<Playlist> =>
  apiPost(`/music/playlists/${enc(id)}/tracks/remove`, { track_ids: trackIds });
export const deletePlaylist = (id: string): Promise<{ ok: boolean }> =>
  apiDelete(`/music/playlists/${enc(id)}`);
export const syncPlaylist = (id: string): Promise<ImportJob> =>
  apiPost(`/music/playlists/${enc(id)}/sync`, {});

// --- importers, jobs, review ---

export const listImporters = (): Promise<{ importers: ProviderRow[] }> =>
  apiGet('/music/importers');
export const importerPlaylists = (id: string): Promise<{ playlists: AccountPlaylist[] }> =>
  apiGet(`/music/importers/${enc(id)}/playlists`);
export const importPlaylist = (importer: string, ref: string): Promise<ImportJob> =>
  apiPost('/music/import/playlist', { importer, ref });
export const listJobs = (): Promise<{ jobs: ImportJob[] }> => apiGet('/music/jobs');
export const cancelJob = (id: string): Promise<{ ok: boolean }> =>
  apiPost(`/music/jobs/${enc(id)}/cancel`, {});
export const reviewList = (): Promise<{ tracks: Track[] }> => apiGet('/music/review');
export const reviewTrack = (
  id: string,
  accept: boolean,
  candidate?: Candidate,
): Promise<{ track: Track | null }> => apiPost(`/music/review/${enc(id)}`, { accept, candidate });

/** Upload an exported playlist file. The importer is detected from the file. */
export async function importPlaylistFile(file: File, importer = ''): Promise<ImportJob> {
  const form = new FormData();
  form.append('file', file, file.name);
  form.append('importer', importer);
  const res = await fetch(apiUrl('/api/music/import/file'), { method: 'POST', body: form });
  const text = await res.text();
  if (!res.ok) {
    let detail = `import failed: ${res.status}`;
    try {
      const body = JSON.parse(text) as { detail?: unknown };
      if (typeof body.detail === 'string') detail = body.detail;
    } catch {
      // non-JSON error body — keep the status message
    }
    throw new Error(detail);
  }
  return JSON.parse(text) as ImportJob;
}

// --- player ---

export const getPlayer = (): Promise<PlayerState> => apiGet('/music/player');
export const queueTracks = (
  trackIds: string[],
  mode: QueueMode = 'queue',
  reason = '',
): Promise<PlayerState> => apiPost('/music/queue', { track_ids: trackIds, mode, reason });
export const queuePlaylist = (
  playlistId: string,
  mode: QueueMode = 'replace',
  shuffle = false,
): Promise<PlayerState> =>
  apiPost('/music/queue/playlist', { playlist_id: playlistId, mode, shuffle });
export const removeFromQueue = (entryId: string): Promise<PlayerState> =>
  apiDelete(`/music/queue/${enc(entryId)}`);
export const moveInQueue = (entryId: string, position: number): Promise<PlayerState> =>
  apiPost('/music/queue/move', { entry_id: entryId, position });
export const clearQueue = (): Promise<PlayerState> => apiPost('/music/queue/clear', {});
export const play = (): Promise<PlayerState> => apiPost('/music/player/play', {});
export const pause = (): Promise<PlayerState> => apiPost('/music/player/pause', {});
export const toggle = (): Promise<PlayerState> => apiPost('/music/player/toggle', {});
export const next = (): Promise<PlayerState> => apiPost('/music/player/next', {});
export const previous = (): Promise<PlayerState> => apiPost('/music/player/previous', {});
export const stop = (): Promise<PlayerState> => apiPost('/music/player/stop', {});
export const seek = (position: number): Promise<PlayerState> =>
  apiPost('/music/player/seek', { position });
export const setVolume = (volume: number): Promise<PlayerState> =>
  apiPost('/music/player/volume', { volume });
export const setShuffle = (value: boolean): Promise<PlayerState> =>
  apiPost('/music/player/shuffle', { value });
export const setRepeat = (mode: RepeatMode): Promise<PlayerState> =>
  apiPost('/music/player/repeat', { mode });
export const setRadio = (value: boolean): Promise<PlayerState> =>
  apiPost('/music/player/radio', { value });
export const claimOutput = (clientId: string, force: boolean): Promise<PlayerState> =>
  apiPost('/music/player/claim', { client_id: clientId, force });
export const reportProgress = (
  position: number,
  duration: number | null,
  clientId: string,
): Promise<unknown> =>
  apiPost('/music/player/progress', { position, duration, client_id: clientId });
export const reportEnded = (clientId: string): Promise<PlayerState> =>
  apiPost('/music/player/ended', { client_id: clientId });
export const djMore = (count = 10, seeds: string[] = []): Promise<PlayerState> =>
  apiPost('/music/dj', { count, seeds });

/**
 * Where an `<audio>` element loads a track. Same-origin and relative on purpose,
 * like karaoke's media URL: the element is routed through the mixer, and a
 * cross-origin source would play silence there (see audio/useMediaStrip.ts).
 */
export function mediaUrl(trackId: string): string {
  return `/api/music/media/${enc(trackId)}`;
}

/** Cover art by content hash; '' when there is none. */
export function artUrl(artHash: string): string {
  return artHash ? `/api/music/art/${enc(artHash)}` : '';
}

/** Upload audio files from the browser into the library (copied, deduped). */
export async function uploadAudio(
  files: File[],
): Promise<{ imported: Track[]; skipped: string[]; failed: string[] }> {
  const form = new FormData();
  for (const file of files) form.append('files', file, file.name);
  const res = await fetch(apiUrl('/api/music/upload'), { method: 'POST', body: form });
  const text = await res.text();
  if (!res.ok) throw new Error(`upload failed: ${res.status}`);
  return JSON.parse(text) as { imported: Track[]; skipped: string[]; failed: string[] };
}
