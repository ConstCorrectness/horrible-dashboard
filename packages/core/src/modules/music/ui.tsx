/**
 * Pieces every music surface shares: the transport, the cover, the scrubber, and
 * the one way an action reports failure.
 *
 * The transport is the same object in the player pane, the board widget and the
 * shell indicator, so it lives here once. Every control is a vector glyph
 * inheriting `currentColor` (glyphs.tsx), never an emoji.
 */
import { useCallback, useState, useSyncExternalStore, type CSSProperties } from 'react';

import { ApiError } from '../../api';
import {
  IconMusic,
  IconPause,
  IconPlay,
  IconRadio,
  IconRepeat,
  IconShuffle,
  IconSkipNext,
  IconSkipPrev,
  IconVolume,
} from '../../glyphs';
import { toastsStore } from '../../toasts';
import {
  artUrl,
  next,
  pause,
  previous,
  seek,
  setRadio,
  setRepeat,
  setShuffle,
  setVolume,
  type PlayerState,
  type RepeatMode,
} from './api';
import { getLocalPlayer, playHere, subscribeLocalPlayer } from './player';
import {
  applyPlayerState,
  getPlayerState,
  getProgress,
  musicVersion,
  progressVersionOf,
  subscribeMusic,
  subscribeProgress,
} from './store';

/** `3:07`, `1:02:45`. Durations are telemetry: always set in mono by the caller. */
export function formatTime(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '–:––';
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}

/** Run a call, adopt the state it returns, toast on failure. */
export async function act(promise: Promise<PlayerState | unknown>, label: string): Promise<void> {
  try {
    const result = await promise;
    if (result && typeof result === 'object' && 'revision' in result) {
      applyPlayerState(result as PlayerState);
    }
  } catch (e) {
    toastsStore.add(
      'error',
      label,
      e instanceof ApiError || e instanceof Error ? e.message : String(e),
    );
  }
}

export function useMusicState(): PlayerState {
  useSyncExternalStore(subscribeMusic, musicVersion);
  return getPlayerState();
}

export function useProgress(): { position: number; duration: number | null } {
  useSyncExternalStore(subscribeProgress, progressVersionOf);
  return getProgress();
}

export function useLocalPlayer() {
  return useSyncExternalStore(subscribeLocalPlayer, getLocalPlayer, getLocalPlayer);
}

/** Cover art in its clipped frame, or the drawn placeholder. */
export function Art({
  hash,
  size = 'md',
  label,
}: {
  hash: string;
  size?: 'sm' | 'md' | 'lg';
  label?: string;
}) {
  const [broken, setBroken] = useState(false);
  const src = artUrl(hash);
  return (
    <span className="mu-art" data-size={size} role="img" aria-label={label ?? 'Cover art'}>
      {src && !broken ? (
        <img src={src} alt="" loading="lazy" onError={() => setBroken(true)} />
      ) : (
        <IconMusic />
      )}
    </span>
  );
}

const NEXT_REPEAT: Record<RepeatMode, RepeatMode> = { off: 'all', all: 'one', one: 'off' };

/**
 * Play/pause and skip, plus the mode toggles when `full`.
 *
 * Play goes through {@link playHere}: a person pressing play in this window means
 * "play it here", which also unblocks a browser that refused autoplay.
 */
export function Transport({
  full = false,
  compact = false,
}: {
  full?: boolean;
  compact?: boolean;
}) {
  const state = useMusicState();
  const local = useLocalPlayer();
  const nothing = !state.now_playing && state.queue.length === 0;
  const playingHere = state.playing && (local.owner || state.output_client === null);

  return (
    <div className="mu-transport" data-compact={compact ? 'true' : undefined}>
      {full && (
        <button
          type="button"
          className="mu-tbtn"
          data-on={state.shuffle ? 'true' : undefined}
          aria-pressed={state.shuffle}
          title={state.shuffle ? 'Shuffle on' : 'Shuffle the queue'}
          onClick={() => void act(setShuffle(!state.shuffle), 'Shuffle')}
        >
          <IconShuffle />
        </button>
      )}
      <button
        type="button"
        className="mu-tbtn"
        title="Previous"
        disabled={!state.now_playing && state.history.length === 0}
        onClick={() => void act(previous(), 'Previous')}
      >
        <IconSkipPrev />
      </button>
      <button
        type="button"
        className="mu-tbtn mu-tbtn--main"
        title={playingHere ? 'Pause' : 'Play'}
        aria-label={playingHere ? 'Pause' : 'Play'}
        disabled={nothing}
        onClick={() => void act(playingHere ? pause() : playHere(), playingHere ? 'Pause' : 'Play')}
      >
        {playingHere ? <IconPause /> : <IconPlay />}
      </button>
      <button
        type="button"
        className="mu-tbtn"
        title="Next"
        disabled={!state.now_playing && state.queue.length === 0}
        onClick={() => void act(next(), 'Next')}
      >
        <IconSkipNext />
      </button>
      {full && (
        <>
          <button
            type="button"
            className="mu-tbtn"
            data-on={state.repeat !== 'off' ? 'true' : undefined}
            aria-pressed={state.repeat !== 'off'}
            title={`Repeat: ${state.repeat}`}
            onClick={() => void act(setRepeat(NEXT_REPEAT[state.repeat]), 'Repeat')}
          >
            <IconRepeat one={state.repeat === 'one'} />
          </button>
          <button
            type="button"
            className="mu-tbtn"
            data-on={state.radio ? 'true' : undefined}
            aria-pressed={state.radio}
            title={
              state.radio
                ? 'Radio on: similar songs keep coming'
                : 'Radio: keep playing similar songs'
            }
            onClick={() => void act(setRadio(!state.radio), 'Radio')}
          >
            <IconRadio />
          </button>
        </>
      )}
    </div>
  );
}

/** Position bar. Re-renders at the progress rate without touching the rest. */
export function Scrubber() {
  const { position, duration } = useProgress();
  const [dragging, setDragging] = useState<number | null>(null);
  const shown = dragging ?? position;
  const max = duration && duration > 0 ? duration : 0;
  const style = {
    '--mu-fill': max ? `${Math.min(100, (shown / max) * 100)}%` : '0%',
  } as CSSProperties;
  const commit = useCallback(() => {
    if (dragging !== null) void act(seek(dragging), 'Seek');
    setDragging(null);
  }, [dragging]);

  return (
    <div className="mu-scrub" style={style}>
      <span className="mu-time">{formatTime(shown)}</span>
      <input
        type="range"
        className="mu-range"
        aria-label="Seek"
        min={0}
        max={max || 1}
        step={0.5}
        value={Math.min(shown, max || 1)}
        disabled={!max}
        onChange={(e) => setDragging(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
      />
      <span className="mu-time">{formatTime(duration)}</span>
    </div>
  );
}

/** Volume. Committed on release so dragging doesn't fire twenty requests. */
export function VolumeControl() {
  const state = useMusicState();
  const [draft, setDraft] = useState<number | null>(null);
  const value = draft ?? state.volume;
  const commit = () => {
    if (draft !== null) void act(setVolume(draft), 'Volume');
    setDraft(null);
  };
  return (
    <div className="mu-volume" style={{ '--mu-fill': `${value * 100}%` } as CSSProperties}>
      <button
        type="button"
        className="mu-tbtn"
        title={value === 0 ? 'Unmute' : 'Mute'}
        onClick={() => void act(setVolume(value === 0 ? 0.8 : 0), 'Volume')}
      >
        <IconVolume muted={value === 0} />
      </button>
      <input
        type="range"
        className="mu-range"
        aria-label="Volume"
        min={0}
        max={1}
        step={0.02}
        value={value}
        onChange={(e) => setDraft(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
      />
    </div>
  );
}

/** "Playing in another window" / "click to start audio", when either is true. */
export function OutputNotice() {
  const state = useMusicState();
  const local = useLocalPlayer();
  if (!state.now_playing) return null;
  if (local.blocked) {
    return (
      <button type="button" className="mu-notice" onClick={() => void act(playHere(), 'Play')}>
        The browser paused audio until you interact — click to start it here.
      </button>
    );
  }
  if (state.output_client && !local.owner) {
    return (
      <button type="button" className="mu-notice" onClick={() => void act(playHere(), 'Play here')}>
        Playing in another window — play it here instead.
      </button>
    );
  }
  return null;
}

/** Why an entry is in the queue, as a short chip label. */
export function reasonLabel(reason: string): string {
  if (!reason) return '';
  if (reason.startsWith('playlist:')) return reason.slice('playlist:'.length);
  return reason;
}
