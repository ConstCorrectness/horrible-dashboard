/**
 * Now playing, outside the player pane: the shell indicator and the board widget.
 *
 * Music is one-per-app and keeps playing with its pane closed, on another desktop,
 * or in a hidden dock — so something in the shell chrome has to say it's on and
 * offer the controls, the same lesson the Clubhouse room indicator learned. It
 * renders nothing when nothing is loaded.
 */
import { useEffect } from 'react';

import { registry } from '../../registry';
import { IconClose } from '../../glyphs';
import { stop } from './api';
import { connectMusic, ensureLoaded } from './store';
import { act, Art, OutputNotice, Scrubber, Transport, useLocalPlayer, useMusicState } from './ui';

import './music.css';

export function MusicNowPlayingIndicator() {
  const state = useMusicState();
  const local = useLocalPlayer();
  const entry = state.now_playing;
  if (!entry) return null;
  const elsewhere = state.output_client !== null && !local.owner;

  return (
    <div className="mu-live" role="status" aria-live="polite">
      <button
        type="button"
        className="mu-live__open"
        title="Open the music player"
        onClick={() => registry.openPanel('music.player')}
      >
        <span
          className="mu-live__dot"
          data-on={state.playing ? 'true' : undefined}
          aria-hidden="true"
        />
        <span className="mu-live__title">{entry.title}</span>
        {entry.artist && <span className="mu-live__meta">{entry.artist}</span>}
        {elsewhere && <span className="mu-live__meta">other window</span>}
      </button>
      <Transport compact />
      <button
        type="button"
        className="mu-live__stop"
        aria-label="Stop the music"
        title="Stop"
        onClick={() => void act(stop(), 'Stop')}
      >
        <IconClose />
      </button>
    </div>
  );
}

/** The board/desktop widget: a compact player. */
export function MusicNowPlayingWidget() {
  const state = useMusicState();
  useEffect(() => {
    connectMusic();
    void ensureLoaded();
  }, []);
  const entry = state.now_playing;

  return (
    <div className="mu-widget">
      <Art
        hash={entry?.art_hash ?? ''}
        size="md"
        label={entry ? `${entry.title} cover` : undefined}
      />
      <div className="mu-widget__body">
        <span className="mu-kicker">
          {entry ? (state.playing ? 'Now playing' : 'Paused') : 'Music'}
        </span>
        <span className="mu-widget__title" title={entry?.title}>
          {entry?.title ?? 'Nothing playing'}
        </span>
        <span className="mu-widget__meta">
          {entry ? entry.artist || '—' : `${state.queue.length} queued`}
        </span>
        {entry && <Scrubber />}
        <Transport />
        <OutputNotice />
      </div>
    </div>
  );
}
