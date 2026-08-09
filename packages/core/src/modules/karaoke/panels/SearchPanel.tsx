/**
 * Search & library: find karaoke videos on YouTube, download them, browse what's
 * already on the node.
 *
 * The two halves share one pane because they answer one question — "what can we
 * sing?" — and splitting them made the common case (search, tap, sing) a
 * pane-hopping exercise. The Library tab is the one that works with no network.
 *
 * The singer field is deliberately sticky across queues: at a real party one
 * person holds the machine and queues for whoever is standing next to them, so
 * re-typing a name per song is the wrong default.
 */
import { useEffect, useState, useSyncExternalStore } from 'react';

import { ApiError } from '../../../api';
import { useSetting } from '../../../settings';
import { toastsStore } from '../../../toasts';
import {
  addToQueue,
  deleteSong,
  downloadSong,
  searchYoutube,
  type SearchResult,
  type SongModel,
} from '../api';
import {
  applyPlayerState,
  connectKaraoke,
  ensureLoaded,
  getKaraokeStatus,
  getSongs,
  karaokeVersion,
  refreshSongs,
  subscribeKaraoke,
} from '../store';

function duration(seconds: number | null): string {
  if (seconds == null) return '';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function KaraokeSearchPanel() {
  useSyncExternalStore(subscribeKaraoke, karaokeVersion);
  const [tab, setTab] = useState<'search' | 'library'>('search');
  const [query, setQuery] = useState('');
  const [singer, setSinger] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [note, setNote] = useState('');
  const [searching, setSearching] = useState(false);
  const [filter, setFilter] = useState('');
  const limit = useSetting<number>('karaoke.searchResults') ?? 20;
  const songs = getSongs();
  const status = getKaraokeStatus();

  useEffect(() => {
    connectKaraoke();
    void ensureLoaded();
  }, []);

  async function runSearch(): Promise<void> {
    if (!query.trim()) return;
    setSearching(true);
    setNote('');
    try {
      const response = await searchYoutube(query.trim(), limit);
      setResults(response.results);
      setNote(response.note);
    } catch (e) {
      toastsStore.add('error', 'Search failed', e instanceof ApiError ? e.message : String(e));
    } finally {
      setSearching(false);
    }
  }

  async function queueResult(result: SearchResult): Promise<void> {
    try {
      // One call whether or not it's downloaded: the backend reuses an existing
      // copy and queues either way, so the button never has to know.
      await downloadSong({
        url: result.url,
        video_id: result.video_id,
        title: result.title,
        queue_for: singer,
      });
      await refreshSongs();
      toastsStore.add('info', 'Queued', result.title);
      setResults((prev) =>
        prev.map((r) => (r.video_id === result.video_id ? { ...r, downloaded: true } : r)),
      );
    } catch (e) {
      toastsStore.add('error', 'Queue failed', e instanceof ApiError ? e.message : String(e));
    }
  }

  async function queueSong(song: SongModel, next = false): Promise<void> {
    try {
      applyPlayerState(await addToQueue(song.id, singer, next));
      toastsStore.add('info', next ? 'Playing next' : 'Queued', song.title);
    } catch (e) {
      toastsStore.add('error', 'Queue failed', e instanceof ApiError ? e.message : String(e));
    }
  }

  async function removeSong(song: SongModel): Promise<void> {
    try {
      await deleteSong(song.id);
      await refreshSongs();
    } catch (e) {
      toastsStore.add('error', 'Delete failed', e instanceof ApiError ? e.message : String(e));
    }
  }

  const visibleSongs = filter.trim()
    ? songs.filter((s) =>
        `${s.title} ${s.artist}`.toLowerCase().includes(filter.trim().toLowerCase()),
      )
    : songs;

  return (
    <div className="kk-pane">
      <div className="kk-pane__bar">
        <button
          type="button"
          onClick={() => setTab('search')}
          style={tab === 'search' ? { borderColor: 'var(--accent)' } : undefined}
        >
          Search
        </button>
        <button
          type="button"
          onClick={() => setTab('library')}
          style={tab === 'library' ? { borderColor: 'var(--accent)' } : undefined}
        >
          Library ({songs.length})
        </button>
      </div>

      <div className="kk-pane__bar">
        <input
          type="text"
          value={singer}
          placeholder="Singer (optional)"
          aria-label="Singer"
          onChange={(e) => setSinger(e.target.value)}
        />
      </div>

      {status && !status.ytdlp && tab === 'search' ? (
        <div className="kk-warning">
          yt-dlp isn&apos;t installed, so search and downloads are unavailable. Run{' '}
          <code>uv sync</code>. The Library tab still works.
        </div>
      ) : null}

      {tab === 'search' ? (
        <>
          <div className="kk-pane__bar">
            <input
              type="search"
              value={query}
              placeholder="Song or artist…"
              aria-label="Search YouTube"
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void runSearch();
              }}
            />
            <button type="button" onClick={() => void runSearch()} disabled={searching}>
              {searching ? '…' : 'Search'}
            </button>
          </div>
          <div className="kk-pane__list">
            {note ? <div className="kk-pane__empty">{note}</div> : null}
            {!note && results.length === 0 ? (
              <div className="kk-pane__empty">
                Searches are biased toward karaoke versions automatically — just type the song.
              </div>
            ) : null}
            {results.map((result) => (
              <div className="kk-row" key={result.video_id}>
                {result.thumbnail ? (
                  <img className="kk-row__thumb" src={result.thumbnail} alt="" />
                ) : null}
                <div className="kk-row__main">
                  <div className="kk-row__title" title={result.title}>
                    {result.title}
                  </div>
                  <div className="kk-row__sub">
                    {result.channel}
                    {result.duration ? ` · ${duration(result.duration)}` : ''}
                    {result.downloaded ? ' · in library' : ''}
                  </div>
                </div>
                <div className="kk-row__actions">
                  <button type="button" onClick={() => void queueResult(result)}>
                    {result.downloaded ? 'Queue' : 'Get + queue'}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      ) : (
        <>
          <div className="kk-pane__bar">
            <input
              type="search"
              value={filter}
              placeholder="Filter the library…"
              aria-label="Filter library"
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>
          <div className="kk-pane__list">
            {visibleSongs.length === 0 ? (
              <div className="kk-pane__empty">
                Nothing downloaded yet. Find something on the Search tab.
              </div>
            ) : null}
            {visibleSongs.map((song) => (
              <div className="kk-row" key={song.id}>
                <div className="kk-row__main">
                  <div className="kk-row__title" title={song.title}>
                    {song.title}
                  </div>
                  <div className="kk-row__sub">
                    {song.artist ? `${song.artist} · ` : ''}
                    {duration(song.duration)}
                    {song.play_count ? ` · sung ${song.play_count}×` : ''}
                    {song.error ? ` · ${song.error}` : ''}
                  </div>
                </div>
                {song.status !== 'ready' ? (
                  <span className={`kk-status kk-status--${song.status}`}>{song.status}</span>
                ) : null}
                <div className="kk-row__actions">
                  <button
                    type="button"
                    onClick={() => void queueSong(song)}
                    disabled={song.status === 'failed'}
                  >
                    Queue
                  </button>
                  <button
                    type="button"
                    onClick={() => void queueSong(song, true)}
                    disabled={song.status === 'failed'}
                    title="Play this next"
                  >
                    ↑
                  </button>
                  <button type="button" onClick={() => void removeSong(song)} title="Delete">
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
