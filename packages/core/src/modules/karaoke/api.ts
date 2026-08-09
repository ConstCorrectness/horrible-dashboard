/**
 * Typed client for the karaoke backend (`/api/karaoke/*`).
 *
 * Mirrors backend/modules/karaoke/models.py. Every session mutation returns the
 * full `PlayerState`, so callers never have to re-fetch after acting — and the
 * same write also broadcasts, which is how the other panes and any phone in the
 * room stay in step.
 */
import { apiDelete, apiGet, apiPost } from '../../api';

export type DownloadStatus = 'queued' | 'downloading' | 'ready' | 'failed';

export interface SearchResult {
  video_id: string;
  title: string;
  url: string;
  channel: string;
  duration: number | null;
  thumbnail: string | null;
  downloaded: boolean;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
  note: string;
}

export interface SongModel {
  id: string;
  title: string;
  artist: string;
  video_id: string;
  url: string;
  filename: string;
  duration: number | null;
  status: DownloadStatus;
  error: string | null;
  size_bytes: number | null;
  play_count: number;
  last_played: string | null;
  added_at: string | null;
}

/**
 * A seat in the running order. `entry_id` — not `song_id` — is what removals and
 * reorders address: the same song queued for three singers is three entries.
 */
export interface QueueEntry {
  entry_id: string;
  song_id: string;
  title: string;
  artist: string;
  singer: string;
  duration: number | null;
  /**
   * Whether the media file exists yet. An entry is queued *while its download is
   * still running*, so it can reach the stage before its file does. A `<video>`
   * that fails to load never retries, so the stage must wait on this rather than
   * pointing at a URL that 404s — otherwise a song that arrived a moment later
   * stays black forever.
   */
  ready: boolean;
  played_at: string | null;
}

export interface PlayerState {
  now_playing: QueueEntry | null;
  playing: boolean;
  position: number;
  duration: number | null;
  volume: number;
  semitones: number;
  queue: QueueEntry[];
  history: QueueEntry[];
  autoplay: boolean;
  revision: number;
}

export interface KaraokeStatus {
  ytdlp: boolean;
  ffmpeg: boolean;
  songs_dir: string;
  song_count: number;
}

export function searchYoutube(query: string, limit = 20): Promise<SearchResponse> {
  return apiGet<SearchResponse>(`/karaoke/search?q=${encodeURIComponent(query)}&limit=${limit}`);
}

export function listSongs(search = ''): Promise<{ songs: SongModel[] }> {
  const qs = search ? `?search=${encodeURIComponent(search)}` : '';
  return apiGet<{ songs: SongModel[] }>(`/karaoke/songs${qs}`);
}

export function downloadSong(body: {
  url?: string;
  video_id?: string;
  title?: string;
  artist?: string;
  queue_for?: string;
}): Promise<SongModel> {
  return apiPost<SongModel>('/karaoke/download', body);
}

export function deleteSong(songId: string): Promise<{ ok: boolean }> {
  return apiDelete<{ ok: boolean }>(`/karaoke/songs/${songId}`);
}

export function getPlayer(): Promise<PlayerState> {
  return apiGet<PlayerState>('/karaoke/player');
}

export function getStatus(): Promise<KaraokeStatus> {
  return apiGet<KaraokeStatus>('/karaoke/status');
}

export function addToQueue(songId: string, singer = '', next = false): Promise<PlayerState> {
  return apiPost<PlayerState>('/karaoke/queue', { song_id: songId, singer, next });
}

export function removeFromQueue(entryId: string): Promise<PlayerState> {
  return apiDelete<PlayerState>(`/karaoke/queue/${entryId}`);
}

export function moveInQueue(entryId: string, position: number): Promise<PlayerState> {
  return apiPost<PlayerState>('/karaoke/queue/move', { entry_id: entryId, position });
}

export function clearQueue(): Promise<PlayerState> {
  return apiPost<PlayerState>('/karaoke/queue/clear', {});
}

export function play(): Promise<PlayerState> {
  return apiPost<PlayerState>('/karaoke/player/play', {});
}

export function pause(): Promise<PlayerState> {
  return apiPost<PlayerState>('/karaoke/player/pause', {});
}

export function nextSong(): Promise<PlayerState> {
  return apiPost<PlayerState>('/karaoke/player/next', {});
}

export function restart(): Promise<PlayerState> {
  return apiPost<PlayerState>('/karaoke/player/restart', {});
}

export function stop(): Promise<PlayerState> {
  return apiPost<PlayerState>('/karaoke/player/stop', {});
}

export function seek(position: number): Promise<PlayerState> {
  return apiPost<PlayerState>('/karaoke/player/seek', { position });
}

export function setVolume(volume: number): Promise<PlayerState> {
  return apiPost<PlayerState>('/karaoke/player/volume', { volume });
}

export function setTranspose(semitones: number): Promise<PlayerState> {
  return apiPost<PlayerState>('/karaoke/player/transpose', { semitones });
}

export function setAutoplay(autoplay: boolean): Promise<PlayerState> {
  return apiPost<PlayerState>('/karaoke/player/autoplay', { autoplay });
}

export function reportProgress(position: number, duration: number | null): Promise<unknown> {
  return apiPost('/karaoke/player/progress', { position, duration });
}

export function reportEnded(): Promise<PlayerState> {
  return apiPost<PlayerState>('/karaoke/player/ended', {});
}

/**
 * The `<video>` src for a song. `semitones` is in the URL rather than a header
 * because a media element fetches by URL alone — and because it makes a key
 * change a *source change*, which is exactly the reload the transcoded stream
 * needs (see backend transpose.py).
 */
export function mediaUrl(songId: string, semitones = 0): string {
  const qs = semitones ? `?semitones=${semitones}` : '';
  return `/api/karaoke/media/${songId}${qs}`;
}
