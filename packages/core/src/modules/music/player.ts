/**
 * The music player's audio: ONE `<audio>` element for the whole window, owned by no
 * pane.
 *
 * A pane is the wrong owner for music. Panes unmount on every tab and workspace
 * switch (see layout/pane-lifetime.ts), and music is one-per-app like a call: it
 * has to keep playing while you look at something else, and stop only when you say
 * so. So the element lives here, started once at boot (`startMusicPlayer`), and the
 * panes, the shell indicator and the agent are all remotes for it.
 *
 * It follows the server's intent (`PlayerState`) rather than holding its own:
 *
 * - **Exactly one window plays.** The session names an `output_client`; every other
 *   window stays paused. A window that sees "playing, nobody owns it" asks for the
 *   output (granted only if the owner is gone or silent); a person pressing play in
 *   a window forces it there ({@link playHere}).
 * - **It reports back.** Position ~1 Hz (the server's progress, the scrubbers, the
 *   listened time scrobbling is decided on) and end of track, which advances.
 * - **It waits for files.** A queued-while-downloading entry has `ready: false`; a
 *   media element that fails to load never retries, so it isn't pointed at one.
 *
 * Routed through the mixer as the `music` strip (never a private AudioContext),
 * and published to `navigator.mediaSession`, so keyboard media keys and the OS
 * media flyout drive *this* player.
 *
 * Nothing here touches the DOM at import time: core's unit tests run without one.
 */
import { mixer } from '../audio/engine';
import {
  artUrl,
  claimOutput,
  mediaUrl,
  next as apiNext,
  pause as apiPause,
  play as apiPlay,
  previous as apiPrevious,
  reportEnded,
  reportProgress,
  seek as apiSeek,
  type PlayerState,
} from './api';
import { applyPlayerState, getPlayerState, onPlayerState } from './store';

export const MUSIC_STRIP = 'music';
/** Seek the element when it drifts this far from the server's position. The
 * server's copy lags the element by up to one progress interval. */
const SEEK_TOLERANCE_S = 2.5;
const PROGRESS_EVERY_MS = 1000;

/** This window's identity for output ownership. Per page load, never persisted:
 * two windows restored from one profile must still be two clients. */
export const clientId: string =
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `c-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;

// --- the reconcile plan, pure so it can be tested without an element ---

export interface ElementView {
  /** The entry the element has loaded, or null. */
  loadedEntryId: string | null;
  currentTime: number;
  paused: boolean;
}

export interface ReconcilePlan {
  /** Load this entry (its id and URL); null = unload; undefined = leave as is. */
  load?: { entryId: string; url: string } | null;
  /** Seek here after any load. */
  seekTo?: number;
  play: boolean;
  /** Ask the server for the output (non-forced). */
  claim: boolean;
  /** This window is the one that plays. */
  owner: boolean;
}

export function planReconcile(state: PlayerState, view: ElementView, me: string): ReconcilePlan {
  const entry = state.now_playing;
  const owner = state.output_client === me;
  const plan: ReconcilePlan = { play: false, claim: false, owner };

  if (!entry) {
    if (view.loadedEntryId !== null) plan.load = null;
    return plan;
  }
  if (state.output_client === null && state.playing) {
    // Something should be playing and no window has it: offer to be the one.
    plan.claim = true;
    return plan;
  }
  if (!owner) {
    // Another window plays. Stay quiet, keep whatever is loaded.
    return plan;
  }
  if (!entry.ready) {
    // Its file is still downloading. Don't point the element at a 404.
    if (view.loadedEntryId !== null && view.loadedEntryId !== entry.entry_id) plan.load = null;
    return plan;
  }
  if (view.loadedEntryId !== entry.entry_id) {
    plan.load = { entryId: entry.entry_id, url: mediaUrl(entry.track_id) };
    // Resuming mid-song (a restart, a window taking over): pick up where it was.
    if (state.position > 1) plan.seekTo = state.position;
  } else if (Math.abs(view.currentTime - state.position) > SEEK_TOLERANCE_S) {
    plan.seekTo = state.position;
  }
  plan.play = state.playing;
  return plan;
}

// --- local, per-window state the UI shows (blocked autoplay, ownership) ---

export interface LocalPlayer {
  owner: boolean;
  /** The browser refused to start audio without a gesture: show "click to play". */
  blocked: boolean;
}

let local: LocalPlayer = { owner: false, blocked: false };
const localListeners = new Set<() => void>();

export function getLocalPlayer(): LocalPlayer {
  return local;
}

export function subscribeLocalPlayer(listener: () => void): () => void {
  localListeners.add(listener);
  return () => {
    localListeners.delete(listener);
  };
}

function setLocal(patch: Partial<LocalPlayer>): void {
  const nextLocal = { ...local, ...patch };
  if (nextLocal.owner === local.owner && nextLocal.blocked === local.blocked) return;
  local = nextLocal;
  for (const l of localListeners) l();
}

// --- the element ---

let el: HTMLAudioElement | null = null;
let loadedEntryId: string | null = null;
let routed = false;
let lastProgressAt = 0;
let claiming = false;
let started = false;
let watchdog: ReturnType<typeof setInterval> | null = null;
/** How often a silent window re-asks for the output while another one owns it.
 * The server only grants it once the owner has been quiet for 8 s, so this is
 * what hands the music over when the owning window is closed or asleep — that
 * produces no state broadcast to react to. */
const TAKEOVER_EVERY_MS = 5000;

function claimQuietly(): void {
  if (claiming) return;
  claiming = true;
  void claimOutput(clientId, false)
    .then(applyPlayerState)
    .catch(() => {})
    .finally(() => {
      claiming = false;
    });
}

/** Keep re-asking while something plays and another window owns it. */
function watchOwner(state: PlayerState): void {
  const waiting = state.playing && state.now_playing !== null && state.output_client !== null && state.output_client !== clientId;
  if (waiting && !watchdog) {
    watchdog = setInterval(claimQuietly, TAKEOVER_EVERY_MS);
  } else if (!waiting && watchdog) {
    clearInterval(watchdog);
    watchdog = null;
  }
}

function route(audio: HTMLAudioElement): void {
  if (routed) return;
  try {
    mixer.declareStrip({ id: MUSIC_STRIP, label: 'Music', icon: '🎵' });
    const handle = mixer.connectStrip(MUSIC_STRIP);
    const source = handle.context.createMediaElementSource(audio);
    source.connect(handle.input);
    routed = true;
  } catch {
    // Leave it on the default output: unroutable audio beats silent audio.
    routed = true;
  }
}

function element(): HTMLAudioElement {
  if (el) return el;
  const audio = new Audio();
  audio.preload = 'auto';
  audio.addEventListener('timeupdate', () => {
    const state = getPlayerState();
    if (state.output_client !== clientId || audio.paused) return;
    const now = Date.now();
    if (now - lastProgressAt < PROGRESS_EVERY_MS) return;
    lastProgressAt = now;
    const duration = Number.isFinite(audio.duration) ? audio.duration : null;
    void reportProgress(audio.currentTime, duration, clientId).catch(() => {});
  });
  audio.addEventListener('ended', () => {
    if (getPlayerState().output_client !== clientId) return;
    loadedEntryId = null;
    void reportEnded(clientId)
      .then(applyPlayerState)
      .catch(() => {});
  });
  audio.addEventListener('error', () => {
    // A file that won't decode (or vanished): move on rather than sit in silence.
    if (getPlayerState().output_client !== clientId || !loadedEntryId) return;
    loadedEntryId = null;
    void apiNext()
      .then(applyPlayerState)
      .catch(() => {});
  });
  el = audio;
  return audio;
}

async function tryPlay(audio: HTMLAudioElement): Promise<void> {
  try {
    if (routed && mixer.isStarted()) await mixer.getContext().resume();
    await audio.play();
    setLocal({ blocked: false });
  } catch (err) {
    // NotAllowedError: autoplay policy, no gesture yet. AbortError: a newer load
    // superseded this play — not a problem.
    if (err instanceof DOMException && err.name === 'NotAllowedError') setLocal({ blocked: true });
  }
}

function reconcile(state: PlayerState): void {
  const audio = element();
  const plan = planReconcile(
    state,
    { loadedEntryId, currentTime: audio.currentTime, paused: audio.paused },
    clientId,
  );
  setLocal({ owner: plan.owner });
  watchOwner(state);

  if (plan.claim) {
    claimQuietly();
    return;
  }

  if (plan.load === null) {
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
    loadedEntryId = null;
  } else if (plan.load) {
    route(audio);
    audio.src = plan.load.url;
    loadedEntryId = plan.load.entryId;
  }
  if (plan.seekTo !== undefined) {
    const target = plan.seekTo;
    if (audio.readyState >= 1) audio.currentTime = target;
    else
      audio.addEventListener('loadedmetadata', () => (audio.currentTime = target), { once: true });
  }
  audio.volume = Math.max(0, Math.min(1, state.volume));
  if (plan.play) {
    if (audio.paused) void tryPlay(audio);
  } else if (!audio.paused) {
    audio.pause();
  }
  publishMediaSession(state, plan.owner);
}

// --- OS media session ---

let sessionEntryId: string | null = null;

function publishMediaSession(state: PlayerState, owner: boolean): void {
  if (typeof navigator === 'undefined' || !('mediaSession' in navigator)) return;
  const ms = navigator.mediaSession;
  const entry = owner ? state.now_playing : null;
  if (!entry) {
    if (sessionEntryId !== null) {
      ms.metadata = null;
      ms.playbackState = 'none';
      sessionEntryId = null;
    }
    return;
  }
  if (sessionEntryId !== entry.entry_id && typeof MediaMetadata !== 'undefined') {
    const art = artUrl(entry.art_hash);
    ms.metadata = new MediaMetadata({
      title: entry.title,
      artist: entry.artist,
      album: entry.album,
      artwork: art ? [{ src: new URL(art, window.location.href).href }] : [],
    });
    sessionEntryId = entry.entry_id;
  }
  ms.playbackState = state.playing ? 'playing' : 'paused';
}

function bindMediaKeys(): void {
  if (typeof navigator === 'undefined' || !('mediaSession' in navigator)) return;
  const ms = navigator.mediaSession;
  const act = (fn: () => Promise<PlayerState>) => () =>
    void fn()
      .then(applyPlayerState)
      .catch(() => {});
  const handlers: [MediaSessionAction, MediaSessionActionHandler][] = [
    ['play', act(() => playHere())],
    ['pause', act(apiPause)],
    ['nexttrack', act(apiNext)],
    ['previoustrack', act(apiPrevious)],
    [
      'seekto',
      (details) =>
        void apiSeek(details.seekTime ?? 0)
          .then(applyPlayerState)
          .catch(() => {}),
    ],
  ];
  for (const [action, handler] of handlers) {
    try {
      ms.setActionHandler(action, handler);
    } catch {
      // An action this browser doesn't support.
    }
  }
}

// --- public ---

/**
 * A person pressed play in *this* window: take the output here, then play. Also
 * what unblocks a refused autoplay, since it runs inside the click.
 */
export async function playHere(): Promise<PlayerState> {
  const audio = element();
  // Start the element inside the gesture, before any await, or the browser no
  // longer counts the click as permission. Load the current entry first if this
  // window wasn't the one playing it.
  const entry = getPlayerState().now_playing;
  if (entry?.ready && loadedEntryId !== entry.entry_id) {
    route(audio);
    audio.src = mediaUrl(entry.track_id);
    loadedEntryId = entry.entry_id;
    const resumeAt = getPlayerState().position;
    if (resumeAt > 1) {
      audio.addEventListener('loadedmetadata', () => (audio.currentTime = resumeAt), {
        once: true,
      });
    }
  }
  if (loadedEntryId && audio.paused) void tryPlay(audio);
  if (mixer.isStarted()) void mixer.getContext().resume();
  const claimed = await claimOutput(clientId, true);
  applyPlayerState(claimed);
  if (claimed.playing) return claimed;
  const state = await apiPlay();
  applyPlayerState(state);
  return state;
}

/** Start following the session. Once per window, at boot. */
export function startMusicPlayer(): void {
  if (started || typeof window === 'undefined') return;
  started = true;
  bindMediaKeys();
  onPlayerState(reconcile);
  const state = getPlayerState();
  if (state.revision > 0) reconcile(state);
}

/** Test-only. */
export function resetPlayerForTests(): void {
  if (watchdog) clearInterval(watchdog);
  watchdog = null;
  el = null;
  loadedEntryId = null;
  routed = false;
  started = false;
  claiming = false;
  local = { owner: false, blocked: false };
  sessionEntryId = null;
}
