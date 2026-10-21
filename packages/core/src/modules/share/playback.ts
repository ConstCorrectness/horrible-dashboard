/**
 * Starting a live `<video>` and knowing when it is actually showing something.
 *
 * The same two rules the public viewer page (`backend/share_relay/viewer.py`)
 * settled on, split out of the mirror pane so they can be tested without a DOM:
 *
 * - **Autoplay with sound is refused until a gesture** in most engines, and a
 *   swallowed refusal leaves a paused element — a black box that looks exactly
 *   like a broken connection. So a refusal plays muted, and the caller shows a
 *   labelled Unmute; the audio is one click away and visibly so.
 * - **`ontrack` is not a picture.** A track arrives with the answer, long before
 *   (and sometimes without) a decoded frame. "Showing video" is the first frame
 *   that decodes, and until then the caller says it is waiting.
 */

/** The slice of `HTMLVideoElement` playback needs. */
export interface Playable {
  muted: boolean;
  play(): Promise<void>;
}

/**
 * How playback started: with `sound`, `muted` because the engine refused sound
 * without a gesture, or `blocked` when it refused even muted (a data-saver mode,
 * a strict policy) and needs a click to start at all.
 */
export type PlaybackOutcome = 'sound' | 'muted' | 'blocked';

/**
 * Start `el`, with sound if the engine allows it. Resolves `null` when
 * `isCurrent` says this attempt was superseded.
 *
 * `isCurrent` is not optional politeness. Swapping `srcObject` aborts a pending
 * `play()`, so an attempt that a newer one replaced (a new stream, or React
 * running the effect twice) *rejects* — and without the check it would read
 * that as an autoplay refusal and mute the element underneath the newer attempt,
 * which had already started with sound and would never show an Unmute.
 */
export async function startPlayback(
  el: Playable,
  isCurrent: () => boolean = () => true,
): Promise<PlaybackOutcome | null> {
  el.muted = false;
  try {
    await el.play();
    return isCurrent() ? 'sound' : null;
  } catch {
    /* refused with sound, or aborted by a newer attempt */
  }
  if (!isCurrent()) return null;
  el.muted = true;
  try {
    await el.play();
    return isCurrent() ? 'muted' : null;
  } catch {
    return isCurrent() ? 'blocked' : null;
  }
}

/** The slice of `HTMLVideoElement` first-frame detection needs. */
export interface FrameSource {
  readyState: number;
  addEventListener(type: 'loadeddata', listener: () => void): void;
  removeEventListener(type: 'loadeddata', listener: () => void): void;
  requestVideoFrameCallback?: (callback: () => void) => number;
  cancelVideoFrameCallback?: (handle: number) => void;
}

/** `HAVE_CURRENT_DATA`: the element holds a decoded frame for the current position. */
const HAVE_CURRENT_DATA = 2;

/**
 * Call `onFrame` once, on the first decoded frame. Returns a canceller.
 *
 * `loadeddata` is the portable signal; `requestVideoFrameCallback` gives the same
 * answer a frame earlier where it exists. Both are registered because neither
 * fires for a track that carries no media — which is the whole point: a stream
 * that never produces a picture keeps saying it is waiting rather than quietly
 * claiming to be live. And `rVFC` alone would never fire in a backgrounded tab,
 * which does not paint.
 */
export function watchFirstFrame(el: FrameSource, onFrame: () => void): () => void {
  let done = false;
  let handle: number | null = null;
  const cancel = () => {
    el.removeEventListener('loadeddata', arrive);
    if (handle !== null) el.cancelVideoFrameCallback?.(handle);
    handle = null;
  };
  function arrive() {
    if (done) return;
    done = true;
    cancel();
    onFrame();
  }
  // An element that already holds a frame (the same stream, re-attached) fires
  // neither event again.
  if (el.readyState >= HAVE_CURRENT_DATA) {
    arrive();
    return () => {};
  }
  el.addEventListener('loadeddata', arrive);
  if (typeof el.requestVideoFrameCallback === 'function') {
    handle = el.requestVideoFrameCallback(arrive);
  }
  return () => {
    done = true;
    cancel();
  };
}
