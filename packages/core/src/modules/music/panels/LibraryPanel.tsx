/**
 * The library: everything this app can play, and every way to add to it.
 *
 * Tabs rather than a tree because the jobs differ: *Songs* is reading and picking,
 * *Playlists* is the user's own groupings, *Add* is search-and-download plus files
 * from this machine, and *Import* brings playlists in from Spotify, Last.fm or an
 * exported file, with the review list for matches the resolver wasn't sure of.
 */
import { useEffect, useRef, useState } from 'react';

import { useAgentContext } from '../../../agent-context';
import { DataList, DataRow } from '../../../DataList';
import { IconImport, IconPlay, IconPlus, IconSearch, IconTrash } from '../../../glyphs';
import { Button, Chip, EmptyState, PaneHeader } from '../../../Primitives';
import { toastsStore } from '../../../toasts';
import {
  deletePlaylist,
  deleteTrack,
  download,
  queuePlaylist,
  queueTracks,
  searchSources,
  syncPlaylist,
  uploadAudio,
  type Candidate,
  type Track,
} from '../api';
import {
  connectMusic,
  ensureLibraryLoaded,
  ensureLoaded,
  getPlaylists,
  getTrackTotal,
  getTracks,
  refreshPlaylists,
  refreshTracks,
} from '../store';
import { act, formatTime, useMusicState } from '../ui';
import { ImportSection } from './ImportSection';

type Tab = 'songs' | 'playlists' | 'add' | 'import';

const SORTS: { id: string; label: string }[] = [
  { id: 'added', label: 'Recent' },
  { id: 'artist', label: 'Artist' },
  { id: 'title', label: 'Title' },
  { id: 'most_played', label: 'Most played' },
];

function statusKind(track: Track): 'idle' | 'info' | 'warn' | 'fail' {
  if (track.status === 'failed') return 'fail';
  if (track.status === 'needs_review') return 'warn';
  if (track.status !== 'ready') return 'info';
  return 'idle';
}

function SongsTab() {
  useMusicState();
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState('added');
  const first = useRef(true);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const timer = setTimeout(() => void refreshTracks(search, sort).catch(() => {}), 220);
    return () => clearTimeout(timer);
  }, [search, sort]);

  const tracks = getTracks().filter((t) => t.status !== 'needs_review');
  const playable = tracks.filter((t) => t.status !== 'failed');

  return (
    <>
      <div className="mu-toolbar">
        <label className="mu-search">
          <IconSearch />
          <input
            type="search"
            placeholder="Search title, artist, album"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort">
          {SORTS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
        <Button
          size="sm"
          intent="ghost"
          icon={<IconPlay />}
          disabled={playable.length === 0}
          onClick={() =>
            void act(
              queueTracks(
                playable.slice(0, 500).map((t) => t.id),
                'replace',
              ),
              'Play all',
            )
          }
        >
          Play all
        </Button>
      </div>
      {tracks.length === 0 ? (
        <EmptyState title={search ? 'No matches' : 'Your library is empty'}>
          {search
            ? 'Nothing in the library matches. Try the Add tab to find it and download it.'
            : 'Use the Add tab to download songs or bring in files from this machine, or Import to copy in your Spotify and Apple Music playlists.'}
        </EmptyState>
      ) : (
        <DataList label="Songs">
          {tracks.map((track, index) => (
            <DataRow
              key={track.id}
              index={index}
              kind={statusKind(track)}
              hideMark={track.status === 'ready'}
              title={track.title}
              // Durations and counts are the telemetry; who and what album is the
              // line under the title, so a narrow strip doesn't truncate the title
              // to two letters to fit them.
              meta={[formatTime(track.duration), ...(track.play_count ? [`${track.play_count}×`] : [])]}
              badge={track.status !== 'ready' ? track.status : undefined}
              onClick={
                track.status === 'failed'
                  ? undefined
                  : () => void act(queueTracks([track.id], 'play'), 'Play')
              }
              actions={
                <>
                  <Button
                    size="sm"
                    intent="ghost"
                    title="Play next"
                    aria-label="Play next"
                    disabled={track.status === 'failed'}
                    icon={<IconPlus />}
                    onClick={() => void act(queueTracks([track.id], 'next'), 'Play next')}
                  />
                  <Button
                    size="sm"
                    intent="ghost"
                    title="Delete from the library"
                    aria-label="Delete"
                    icon={<IconTrash />}
                    onClick={() => {
                      if (
                        window.confirm(`Delete “${track.title}” and its file from the library?`)
                      ) {
                        void act(deleteTrack(track.id), 'Delete');
                      }
                    }}
                  />
                </>
              }
            >
              {track.status === 'failed' && track.error
                ? track.error
                : [track.artist, track.album].filter(Boolean).join(' — ') || undefined}
            </DataRow>
          ))}
        </DataList>
      )}
    </>
  );
}

function PlaylistsTab() {
  useMusicState();
  const playlists = getPlaylists();
  if (playlists.length === 0) {
    return (
      <EmptyState title="No playlists yet">
        Save the queue as one from the queue strip, import yours from Spotify or Apple Music on the
        Import tab, or ask the agent to “make a playlist of my most played this month”.
      </EmptyState>
    );
  }
  return (
    <DataList label="Playlists">
      {playlists.map((pl, index) => {
        const synced = pl.origin.startsWith('spotify:') || pl.origin === 'lastfm:loved';
        return (
          <DataRow
            key={pl.id}
            index={index}
            hideMark
            title={pl.name}
            meta={[
              `${pl.track_count} songs`,
              pl.origin === 'local' ? 'local' : pl.origin.split(':')[0],
            ]}
            onClick={() => void act(queuePlaylist(pl.id, 'replace'), 'Play playlist')}
            actions={
              <>
                <Button
                  size="sm"
                  intent="ghost"
                  title="Shuffle play"
                  onClick={() => void act(queuePlaylist(pl.id, 'replace', true), 'Shuffle')}
                >
                  Shuffle
                </Button>
                {synced && (
                  <Button
                    size="sm"
                    intent="ghost"
                    title="Fetch what's new in the source playlist"
                    onClick={() => void act(syncPlaylist(pl.id), 'Sync')}
                  >
                    Sync
                  </Button>
                )}
                <Button
                  size="sm"
                  intent="ghost"
                  aria-label="Delete playlist"
                  title="Delete the playlist (songs stay in the library)"
                  icon={<IconTrash />}
                  onClick={() => {
                    if (
                      window.confirm(
                        `Delete the playlist “${pl.name}”? The songs stay in your library.`,
                      )
                    ) {
                      void act(deletePlaylist(pl.id), 'Delete playlist').then(() =>
                        refreshPlaylists(),
                      );
                    }
                  }}
                />
              </>
            }
          />
        );
      })}
    </DataList>
  );
}

function AddTab() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Candidate[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const run = async () => {
    if (!query.trim()) return;
    setBusy(true);
    try {
      const res = await searchSources(query.trim(), 12);
      setResults(res.candidates);
      setNotes(res.notes);
    } catch (e) {
      setNotes([e instanceof Error ? e.message : String(e)]);
    } finally {
      setBusy(false);
    }
  };

  const fetchIt = async (cand: Candidate, then: 'play' | 'queue' | 'none') => {
    if (cand.track_id) {
      await act(queueTracks([cand.track_id], then === 'none' ? 'queue' : then), 'Queue');
      return;
    }
    try {
      const track = await download({ source: cand.source, ref: cand.ref, url: cand.url, then });
      setResults((rows) =>
        rows.map((r) => (r.ref === cand.ref ? { ...r, track_id: track.id } : r)),
      );
      toastsStore.add('info', 'Downloading', `${cand.title} is on its way into your library.`);
    } catch (e) {
      toastsStore.add('error', 'Download', e instanceof Error ? e.message : String(e));
    }
  };

  const upload = async (files: File[]) => {
    if (files.length === 0) return;
    setBusy(true);
    try {
      const res = await uploadAudio(files);
      const parts = [`${res.imported.length} added`];
      if (res.skipped.length) parts.push(`${res.skipped.length} already in the library`);
      if (res.failed.length) parts.push(`${res.failed.length} failed`);
      toastsStore.add(res.failed.length ? 'warning' : 'success', 'Import', parts.join(', '));
    } catch (e) {
      toastsStore.add('error', 'Import', e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <form
        className="mu-toolbar"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <label className="mu-search">
          <IconSearch />
          <input
            type="search"
            placeholder="Song and artist — “Africa Toto”"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <Button type="submit" intent="primary" size="sm" disabled={busy || !query.trim()}>
          Find
        </Button>
      </form>
      {notes.length > 0 && <p className="mu-muted mu-notes">{notes.join(' · ')}</p>}
      {results.length > 0 && (
        <DataList label="Search results">
          {results.map((cand, index) => (
            <DataRow
              key={`${cand.source}:${cand.ref}`}
              index={index}
              kind={cand.track_id ? 'ok' : 'idle'}
              hideMark={!cand.track_id}
              title={cand.title}
              meta={[cand.artist || cand.channel || '—', formatTime(cand.duration), cand.source]}
              badge={cand.track_id ? 'in library' : undefined}
              actions={
                <>
                  <Button size="sm" intent="ghost" onClick={() => void fetchIt(cand, 'play')}>
                    Play
                  </Button>
                  <Button size="sm" intent="ghost" onClick={() => void fetchIt(cand, 'queue')}>
                    Queue
                  </Button>
                  {!cand.track_id && (
                    <Button size="sm" intent="ghost" onClick={() => void fetchIt(cand, 'none')}>
                      Save
                    </Button>
                  )}
                </>
              }
            />
          ))}
        </DataList>
      )}
      <div
        className="mu-drop"
        data-over={dragOver ? 'true' : undefined}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          void upload(Array.from(e.dataTransfer.files));
        }}
      >
        <IconImport />
        <span className="mu-drop__title">Your own files</span>
        <span className="mu-muted">
          Drop audio files here (MP3, M4A, FLAC, OGG, WAV). They're copied into the library; a
          matching .lrc file brings its lyrics.
        </span>
        <Button size="sm" disabled={busy} onClick={() => fileInput.current?.click()}>
          Choose files
        </Button>
        <input
          ref={fileInput}
          type="file"
          accept="audio/*,.flac,.m4a,.opus"
          multiple
          hidden
          onChange={(e) => {
            void upload(Array.from(e.target.files ?? []));
            e.target.value = '';
          }}
        />
      </div>
    </>
  );
}

const TABS: { id: Tab; label: string }[] = [
  { id: 'songs', label: 'Songs' },
  { id: 'playlists', label: 'Playlists' },
  { id: 'add', label: 'Add' },
  { id: 'import', label: 'Import' },
];

export function MusicLibraryPanel() {
  useMusicState();
  const [tab, setTab] = useState<Tab>('songs');

  useEffect(() => {
    connectMusic();
    void ensureLoaded();
    void ensureLibraryLoaded();
  }, []);

  const total = getTrackTotal();
  const review = getTracks().filter((t) => t.status === 'needs_review').length;

  useAgentContext(() => ({
    tab,
    librarySize: total,
    visibleTracks: getTracks()
      .slice(0, 30)
      .map((t) => ({ track_id: t.id, title: t.title, artist: t.artist, status: t.status })),
    playlists: getPlaylists().map((p) => ({ id: p.id, name: p.name, tracks: p.track_count })),
  }));

  return (
    <div className="mu-pane mu-library">
      <PaneHeader title="Library" meta={[`${total} songs`, `${getPlaylists().length} playlists`]} />
      <div className="mu-tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            className="mu-tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
          >
            {t.label}
            {t.id === 'import' && review > 0 && <Chip kind="warn">{review}</Chip>}
          </button>
        ))}
      </div>
      <div className="mu-pane__scroll">
        {tab === 'songs' && <SongsTab />}
        {tab === 'playlists' && <PlaylistsTab />}
        {tab === 'add' && <AddTab />}
        {tab === 'import' && <ImportSection />}
      </div>
    </div>
  );
}

