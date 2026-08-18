/**
 * The stage: the screen the room looks at.
 *
 * This is the only client that owns a `<video>` element, and that makes it the
 * one asymmetric piece of the module. The server holds *intent* (`playing`,
 * `semitones`, which entry is up); this pane reconciles the element toward that
 * intent and reports the real position back. Everything else — the queue pane, a
 * guest's phone — is a pure renderer, which is why a remote can pause a video it
 * isn't showing.
 *
 * The reconciliation runs in effects keyed on the intent, never on user events:
 * pressing pause here posts to the server and waits for the broadcast to come
 * back. Driving the element directly *and* posting would make the two disagree
 * whenever a remote acted at the same moment.
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import { useAgentContext } from '../../../agent-context';
import { useMediaStrip } from '../../audio/useMediaStrip';
import { useSetting } from '../../../settings';
import { toastsStore } from '../../../toasts';
import {
  applyPlayerState,
  connectKaraoke,
  ensureLoaded,
  getKaraokeStatus,
  getPlayerState,
  karaokeVersion,
  subscribeKaraoke,
} from '../store';
import {
  mediaUrl,
  nextSong,
  pause,
  play,
  reportEnded,
  reportProgress,
  restart,
  setTranspose,
  setVolume,
} from '../api';

/** mm:ss. Karaoke songs are minutes, never hours — no hour field on purpose. */
function clock(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '--:--';
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** How long the title/singer banner stays up at the start of a song. */
const BANNER_MS = 8000;
/** How often the stage tells the server where it is. */
const PROGRESS_MS = 1000;

export function KaraokeStagePanel() {
  useSyncExternalStore(subscribeKaraoke, karaokeVersion);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [bannerVisible, setBannerVisible] = useState(true);
  const state = getPlayerState();
  const status = getKaraokeStatus();
  const entry = state.now_playing;
  const settingVolume = useSetting<number>('karaoke.volume');

  // Route the stage's audio through the mixer instead of straight to the system
  // output. This is what lets a song go to the speakers *and* to a virtual cable
  // at the same time — the singer hears the backing track while a call, a stream
  // or a recording gets it too. The media is served by our own backend, so the
  // same-origin requirement in `useMediaStrip` holds.
  useMediaStrip(videoRef, { id: 'karaoke', label: 'Karaoke', icon: '🎤' });

  useEffect(() => {
    connectKaraoke();
    void ensureLoaded();
  }, []);

  // Seed the session volume from the setting once, on first mount with a value.
  // Guarded on `loaded` state rather than firing on every volume change, or the
  // setting would fight every adjustment made from a remote.
  const seeded = useRef(false);
  useEffect(() => {
    if (seeded.current || settingVolume == null) return;
    seeded.current = true;
    if (settingVolume !== state.volume) void setVolume(settingVolume).then(applyPlayerState);
  }, [settingVolume, state.volume]);

  // An entry can reach the stage before its file does — `POST /download` queues
  // while the download is still running, on purpose. Mounting a `<video>` at that
  // point is not merely early, it's unrecoverable: the element 404s, sets
  // `error.code = 4`, and a media element that failed to load never retries. So
  // the stage renders a waiting screen until the server says the file landed.
  const pending = Boolean(entry) && entry?.ready === false;
  const src = entry && !pending ? mediaUrl(entry.song_id, state.semitones) : '';

  // A new song, or a key change, is a *source* change. The transposed stream is a
  // live transcode with no seek (see backend transpose.py), so switching key has
  // to restart the element — hence keying the load on both. `pending` is in the
  // deps because the element is unmounted while waiting: when the download lands
  // a *fresh* element mounts, and an effect keyed on `src` alone would not re-run
  // for it.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !src) return;
    video.load();
    setBannerVisible(true);
    const timer = window.setTimeout(() => setBannerVisible(false), BANNER_MS);
    return () => window.clearTimeout(timer);
  }, [src, pending]);

  // Reconcile play/pause toward the server's intent.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !src) return;
    if (state.playing) {
      // Autoplay can be refused before the user has interacted with the page.
      // Report that back rather than leaving the room staring at a frozen frame
      // while every remote insists it's playing.
      void video.play().catch(() => {
        void pause().then(applyPlayerState);
      });
    } else {
      video.pause();
    }
  }, [state.playing, src, pending]);

  useEffect(() => {
    const video = videoRef.current;
    if (video) video.volume = state.volume;
  }, [state.volume]);

  // The server's `position` is advisory except when it *moves* — a remote seeking
  // is the one case the stage must follow. A tolerance is required: the stage's own
  // 1 Hz progress pings echo back, and reacting to those would re-seek every second.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !src) return;
    if (Math.abs(video.currentTime - state.position) > 2) {
      video.currentTime = state.position;
    }
  }, [state.position, src]);

  // Report the true position back so remotes can draw a scrubber.
  useEffect(() => {
    if (!entry) return;
    const timer = window.setInterval(() => {
      const video = videoRef.current;
      if (!video || video.paused) return;
      void reportProgress(
        video.currentTime,
        Number.isFinite(video.duration) ? video.duration : null,
      );
    }, PROGRESS_MS);
    return () => window.clearInterval(timer);
  }, [entry]);

  useAgentContext(() => ({
    nowPlaying: entry ? { title: entry.title, artist: entry.artist, singer: entry.singer } : null,
    playing: state.playing,
    queueLength: state.queue.length,
    upNext: state.queue.slice(0, 3).map((e) => ({ title: e.title, singer: e.singer })),
    semitones: state.semitones,
  }));

  const onEnded = useCallback(() => {
    void reportEnded().then(applyPlayerState);
  }, []);

  // A media element that fails to load goes quiet: no throw, no rejected promise,
  // just a black rectangle and `playing: true` on every remote. Surface it, and
  // pause so the transport stops lying about what the room is watching.
  const onMediaError = useCallback(() => {
    const code = videoRef.current?.error?.code;
    toastsStore.add(
      'error',
      'Playback failed',
      `${entry?.title ?? 'This song'} could not be played${code ? ` (media error ${code})` : ''}.`,
    );
    void pause().then(applyPlayerState);
  }, [entry?.title]);

  const transpose = useCallback(
    (delta: number) => {
      const next = Math.max(-6, Math.min(6, state.semitones + delta));
      if (next !== state.semitones) void setTranspose(next).then(applyPlayerState);
    },
    [state.semitones],
  );

  const upNext = state.queue[0];

  return (
    <div className="kk-stage">
      <div className="kk-stage__video-wrap">
        {entry && pending ? (
          <div className="kk-splash">
            <h1 className="kk-splash__title">DOWNLOADING</h1>
            <p className="kk-splash__next">
              <b>{entry.title}</b>
              {entry.singer ? ` — ${entry.singer}` : ''}
            </p>
            <p className="kk-splash__hint">
              It&rsquo;ll start on its own the moment the file lands.
            </p>
          </div>
        ) : entry ? (
          <>
            <video
              ref={videoRef}
              className="kk-stage__video"
              src={src}
              onEnded={onEnded}
              onError={onMediaError}
              playsInline
            />
            <div className={`kk-stage__nowbar${bannerVisible ? '' : ' kk-stage__nowbar--hidden'}`}>
              <span className="kk-stage__nowtitle">{entry.title}</span>
              {entry.artist ? <span>{entry.artist}</span> : null}
              {entry.singer ? <span className="kk-stage__singer">🎤 {entry.singer}</span> : null}
            </div>
          </>
        ) : (
          <div className="kk-splash">
            <h1 className="kk-splash__title">KARAOKE</h1>
            {upNext ? (
              <p className="kk-splash__next">
                Up next: <b>{upNext.title}</b>
                {upNext.singer ? ` — ${upNext.singer}` : ''}
              </p>
            ) : (
              <p className="kk-splash__hint">
                Search for a song and add it to the queue. Ask the agent too — try &ldquo;queue
                Africa by Toto for Sam&rdquo;.
              </p>
            )}
            {status && !status.ytdlp ? (
              <p className="kk-splash__hint">yt-dlp is not installed — run `uv sync`.</p>
            ) : null}
          </div>
        )}
      </div>

      <div className="kk-transport">
        <button
          type="button"
          onClick={() => void (state.playing ? pause() : play()).then(applyPlayerState)}
          disabled={!entry && state.queue.length === 0}
          title={state.playing ? 'Pause' : 'Play'}
        >
          {state.playing ? '⏸' : '▶'}
        </button>
        <button
          type="button"
          onClick={() => void restart().then(applyPlayerState)}
          disabled={!entry}
          title="Restart this song"
        >
          ⏮
        </button>
        <button
          type="button"
          onClick={() => void nextSong().then(applyPlayerState)}
          disabled={!entry && state.queue.length === 0}
          title="Next singer"
        >
          ⏭
        </button>
        <span className="kk-transport__time">
          {clock(state.position)} / {clock(state.duration)}
        </span>

        <span className="kk-transport__spacer" />

        <span className="kk-transport__group">
          Key
          <button type="button" onClick={() => transpose(-1)} title="Lower the key">
            ♭
          </button>
          <span
            className={`kk-key${state.semitones ? ' kk-key--shifted' : ''}`}
            title="Semitones from the original key"
          >
            {state.semitones > 0 ? `+${state.semitones}` : state.semitones}
          </span>
          <button type="button" onClick={() => transpose(1)} title="Raise the key">
            ♯
          </button>
        </span>

        <span className="kk-transport__group">
          🔊
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={state.volume}
            aria-label="Volume"
            onChange={(e) => void setVolume(Number(e.target.value)).then(applyPlayerState)}
          />
        </span>
      </div>
    </div>
  );
}
