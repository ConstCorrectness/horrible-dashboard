/**
 * Display capture: getting the host's dashboard into a `MediaStream`.
 *
 * Support is decided by feature detection **and** one capability, because
 * neither alone is enough. Display capture does not split along "browser or
 * desktop": it works in a browser, works in Tauri on Windows and Linux (WebView2
 * shows its own native picker; WebKitGTK has one from 2.42), and is broken in
 * WKWebView on macOS. So the bulk of the answer is feature detection of the exact
 * API about to be called — never a `window.__TAURI__` check standing in for it.
 *
 * The capability covers the case feature detection provably cannot: see the
 * fourth state below. It is asserted per-platform by the shell
 * (`desktopCapabilities`), so it is not the "static per-host list that is wrong
 * on one platform" this comment used to warn about.
 *
 * The probe reports **four** states, the hardware module's rule: it is here, it
 * is definitely not here, we could not tell, or the shell told us. The third is
 * real — a page served over plain HTTP off localhost has no `mediaDevices` at
 * all, which is a *configuration* problem and must not be reported as "your
 * browser cannot do this".
 *
 * The fourth exists because feature detection has one blind spot it cannot see
 * past: **WKWebView exposes `getDisplayMedia` and then rejects it** with
 * `NotAllowedError` — the identical error a host produces by dismissing the
 * picker. So on the macOS desktop the API is present and lying, the probe would
 * say "available", the call would fail, and `startStream` would stay silent
 * because it correctly reads that error as a cancel. Nothing observable from
 * here distinguishes the two, so the shell asserts it instead: the
 * `media.displayCapture` capability is withheld on the macOS desktop and this
 * probe checks it before believing the API.
 */

import { hasCapability } from '../../capabilities';

export type CaptureSupport =
  | 'available'
  | 'unavailable'
  | 'insecure-context'
  | 'unsupported-shell';

export interface CaptureProbe {
  support: CaptureSupport;
  /** A sentence for the UI. Always set — an unexplained "no" is the failure. */
  reason: string;
}

interface DisplayMediaCapable {
  getDisplayMedia?: (constraints?: MediaStreamConstraints) => Promise<MediaStream>;
}

export function probeCapture(): CaptureProbe {
  // Before feature detection, not after: on the one platform this catches, the
  // feature detects as present.
  if (!hasCapability('media.displayCapture')) {
    return {
      support: 'unsupported-shell',
      reason:
        'This desktop shell cannot capture the screen — macOS webviews have no capture API. ' +
        'Open the dashboard in a browser to share your screen, or share panes semantically ' +
        'instead: guests still see the panes you mirror.',
    };
  }
  // `isSecureContext` first: without it `navigator.mediaDevices` is undefined,
  // and reporting that as "unavailable" would blame the browser for what is
  // actually an origin problem — the exact mistake the hardware module's third
  // state exists to prevent.
  if (typeof window !== 'undefined' && window.isSecureContext === false) {
    return {
      support: 'insecure-context',
      reason:
        'Screen capture needs a secure origin. Open the dashboard over HTTPS or on localhost.',
    };
  }
  const media = navigator?.mediaDevices as (MediaDevices & DisplayMediaCapable) | undefined;
  if (!media || typeof media.getDisplayMedia !== 'function') {
    return {
      support: 'unavailable',
      reason:
        'This app shell has no screen-capture API. Use the browser layout, or share panes ' +
        'semantically instead.',
    };
  }
  return { support: 'available', reason: 'Screen capture is available.' };
}

/** Frames per second requested from the capture. */
const FRAME_RATE = 15;

export class CaptureError extends Error {
  /** True when the host dismissed the picker — not a failure worth shouting about. */
  readonly cancelled: boolean;

  constructor(message: string, cancelled: boolean) {
    super(message);
    this.name = 'CaptureError';
    this.cancelled = cancelled;
  }
}

/**
 * Ask the host to pick something to share.
 *
 * Must be called from a user gesture — every engine requires one, and a capture
 * started from a timer or a websocket message is rejected with the same
 * `NotAllowedError` the picker's Cancel button produces. Those two are told apart
 * by `CaptureError.cancelled` so the UI can stay quiet about a deliberate cancel
 * and loud about a real refusal.
 *
 * Audio is requested but **not required**: `getDisplayMedia({audio:true})` yields
 * tab or system audio on Chromium and nothing at all elsewhere, so treating it as
 * mandatory would make the whole capture fail on Firefox and Safari. What viewers
 * hear is the mixer's `Viewers` bus, which works the same way everywhere; this is
 * a bonus track when the engine offers one.
 */
export async function startCapture(): Promise<MediaStream> {
  const probe = probeCapture();
  if (probe.support !== 'available') throw new CaptureError(probe.reason, false);

  const media = navigator.mediaDevices as MediaDevices & DisplayMediaCapable;
  try {
    const stream = await media.getDisplayMedia!({
      video: {
        frameRate: FRAME_RATE,
        // No width/height: constraining a display capture makes the browser
        // scale it, and a dashboard is text. Scaled text is the difference
        // between a readable share and a pointless one.
      },
      audio: true,
    });
    hintText(stream);
    return stream;
  } catch (err) {
    const name = (err as { name?: string })?.name ?? '';
    const cancelled = name === 'NotAllowedError' || name === 'AbortError';
    throw new CaptureError(
      cancelled
        ? 'No screen was picked.'
        : `Screen capture failed: ${(err as Error)?.message || name || 'unknown error'}`,
      cancelled,
    );
  }
}

/**
 * Tell the encoder this is text, not video.
 *
 * The default `contentHint` of `''` leaves the encoder guessing, and its guess is
 * tuned for camera footage: under pressure it protects *motion smoothness* by
 * dropping resolution, which is precisely backwards for a dashboard. `'detail'`
 * flips that trade — hold the pixels, drop the frame rate — so a stalled share
 * degrades into a slideshow of readable text rather than smooth mush.
 *
 * Free: no constraint negotiation, no extra CPU, no round trip. Guarded with
 * `in` because it is a hint on the track, and an engine that lacks it must not
 * take the capture down with it.
 */
function hintText(stream: MediaStream): void {
  for (const track of stream.getVideoTracks()) {
    if ('contentHint' in track) track.contentHint = 'detail';
  }
}

/** Stop every track. Safe on a stream that has already ended. */
export function stopCapture(stream: MediaStream | null): void {
  stream?.getTracks().forEach((t) => t.stop());
}

/**
 * Run `onEnded` when the host stops the capture from the browser's own bar.
 *
 * Every engine puts a "Stop sharing" control outside the page, and a share that
 * kept claiming to be live after the user pressed it would be the worst possible
 * bug in this module — the UI would say the stream is running while the viewers
 * see a frozen frame.
 */
export function onCaptureEnded(stream: MediaStream, onEnded: () => void): () => void {
  const track = stream.getVideoTracks()[0];
  if (!track) return () => {};
  const handler = () => onEnded();
  track.addEventListener('ended', handler);
  return () => track.removeEventListener('ended', handler);
}
