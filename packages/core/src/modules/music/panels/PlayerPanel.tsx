/**
 * The music player: what's playing, the transport, and the words.
 *
 * Asymmetric on purpose — the cover and identity hold the left column, lyrics
 * take the rest — rather than a centered stack: the lyrics are what you read
 * while it plays, so they get the width. The library and the queue are this pane's
 * own strips (regions), not separate launcher entries.
 *
 * This pane does not own the audio. The `<audio>` element is app-wide
 * (player.ts), so closing or hiding the pane changes nothing about what you hear.
 */
import { useEffect, useMemo, useRef, useState } from 'react';

import { useAgentContext } from '../../../agent-context';
import { EmptyState } from '../../../Primitives';
import { RollingNumber } from '../../../DataList';
import { getLyrics, seek, type Lyrics } from '../api';
import { connectMusic, ensureLoaded } from '../store';
import {
  act,
  Art,
  OutputNotice,
  Scrubber,
  Transport,
  useMusicState,
  useProgress,
  VolumeControl,
  reasonLabel,
} from '../ui';

function useLyrics(trackId: string | null, ready: boolean): Lyrics | null {
  const [lyrics, setLyrics] = useState<Lyrics | null>(null);
  useEffect(() => {
    setLyrics(null);
    if (!trackId || !ready) return;
    let live = true;
    getLyrics(trackId)
      .then((found) => {
        if (live) setLyrics(found);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [trackId, ready]);
  return lyrics;
}

/** The current line of time-synced lyrics, kept in view as it advances. */
function SyncedLyrics({ lines }: { lines: [number, string][] }) {
  const { position } = useProgress();
  const current = useMemo(() => {
    let index = -1;
    for (let i = 0; i < lines.length; i++) {
      if (lines[i][0] <= position + 0.25) index = i;
      else break;
    }
    return index;
  }, [lines, position]);
  const listRef = useRef<HTMLOListElement>(null);

  useEffect(() => {
    // Scroll the list itself, never `scrollIntoView`: that scrolls every scrollable
    // ancestor too, and in a narrow (stacked) pane it carried the transport
    // controls out of view to follow the words.
    const list = listRef.current;
    const row = list?.querySelector<HTMLElement>(`[data-line="${current}"]`);
    if (!list || !row) return;
    // `.mu-lyrics` is positioned, so a line's offsetTop is relative to the list.
    const top = row.offsetTop - list.clientHeight / 2 + row.clientHeight / 2;
    list.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  }, [current]);

  return (
    <ol className="mu-lyrics" ref={listRef}>
      {lines.map(([time, words], i) => (
        <li key={`${i}-${time}`} data-line={i}>
          <button
            type="button"
            className="mu-lyric"
            data-state={i === current ? 'now' : i < current ? 'past' : undefined}
            title="Jump here"
            onClick={() => void act(seek(time), 'Seek')}
          >
            {words || '…'}
          </button>
        </li>
      ))}
    </ol>
  );
}

export function MusicPlayerPanel() {
  const state = useMusicState();
  const entry = state.now_playing;

  useEffect(() => {
    connectMusic();
    void ensureLoaded();
  }, []);

  const lyrics = useLyrics(entry?.track_id ?? null, Boolean(entry?.ready));

  useAgentContext(() => ({
    nowPlaying: entry
      ? {
          entry_id: entry.entry_id,
          track_id: entry.track_id,
          title: entry.title,
          artist: entry.artist,
          album: entry.album,
        }
      : null,
    playing: state.playing,
    position: Math.round(state.position),
    upNext: state.queue
      .slice(0, 5)
      .map((e) => ({ entry_id: e.entry_id, title: e.title, artist: e.artist })),
    modes: { shuffle: state.shuffle, repeat: state.repeat, radio: state.radio },
  }));

  if (!entry) {
    return (
      <div className="mu-player mu-player--empty">
        <EmptyState title="Nothing playing">
          Pick a song from the library on the left, search for one to download, or ask the agent —
          “play something by Fleetwood Mac”.
        </EmptyState>
        {state.queue.length > 0 && (
          <div className="mu-player__dock">
            <Transport full />
          </div>
        )}
      </div>
    );
  }

  const reason = reasonLabel(entry.reason);

  return (
    <div className="mu-player">
      <section className="mu-player__now" aria-label="Now playing">
        <Art hash={entry.art_hash} size="lg" label={`${entry.title} cover`} />
        <div className="mu-player__id">
          <span className="mu-kicker">{state.playing ? 'Now playing' : 'Paused'}</span>
          <h2 className="mu-player__title" title={entry.title}>
            {entry.title}
          </h2>
          <div className="mu-player__sub">
            {entry.artist && <span className="mu-player__artist">{entry.artist}</span>}
            {entry.album && <span className="mu-player__album">{entry.album}</span>}
          </div>
          <div className="mu-player__meta">
            {!entry.ready && <span>downloading…</span>}
            {reason && <span>{reason}</span>}
            <span>
              <RollingNumber value={state.queue.length} /> up next
            </span>
          </div>
        </div>
        <div className="mu-player__controls">
          <Scrubber />
          <div className="mu-player__row">
            <Transport full />
            <VolumeControl />
          </div>
          <OutputNotice />
        </div>
      </section>

      <section className="mu-player__words" aria-label="Lyrics">
        <div className="mu-section-head">
          <span className="mu-section-title">Lyrics</span>
          {lyrics && lyrics.source !== 'none' && (
            <span className="mu-section-meta">{lyrics.source}</span>
          )}
        </div>
        {!entry.ready ? (
          <p className="mu-muted">The song is still downloading.</p>
        ) : !lyrics ? (
          <p className="mu-muted">Looking for lyrics…</p>
        ) : lyrics.synced.length > 0 ? (
          <SyncedLyrics lines={lyrics.synced} />
        ) : lyrics.plain ? (
          <pre className="mu-lyrics-plain">{lyrics.plain}</pre>
        ) : (
          <p className="mu-muted">
            {lyrics.source === 'instrumental'
              ? 'Instrumental.'
              : 'No lyrics found. Drop a matching .lrc file next to the song when you import it to add your own.'}
          </p>
        )}
      </section>
    </div>
  );
}
