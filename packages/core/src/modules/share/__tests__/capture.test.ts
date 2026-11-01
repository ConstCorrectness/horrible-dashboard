// @vitest-environment happy-dom
/**
 * The capture probe's four states.
 *
 * The one worth testing hardest is `unsupported-shell`: it exists because a webview
 * can expose `getDisplayMedia` and then reject it with the same `NotAllowedError` a
 * dismissed picker produces (WKWebView did, under the old Tauri shell), so the API
 * is present and lying and no amount of feature detection can see it. If the
 * capability check ever moves *after* the feature detection, such a shell silently
 * goes back to reporting "available" and the share fails with no message at all.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import {
  BROWSER_CAPABILITIES,
  DESKTOP_CAPABILITIES,
  desktopCapabilities,
  initCapabilities,
} from '../../../capabilities';

/** A shell that knows it cannot capture, though its webview exposes the API. */
const WITHHOLDING_SHELL = DESKTOP_CAPABILITIES.filter((c) => c !== 'media.displayCapture');
import { probeCapture, startCapture } from '../capture';

function withDisplayMedia(present: boolean): void {
  const media = { getDisplayMedia: present ? async () => new MediaStream() : undefined };
  Object.defineProperty(navigator, 'mediaDevices', { value: media, configurable: true });
}

describe('probeCapture', () => {
  beforeEach(() => {
    initCapabilities(BROWSER_CAPABILITIES);
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
    withDisplayMedia(true);
  });

  it('is available in a browser with the API on a secure origin', () => {
    expect(probeCapture().support).toBe('available');
  });

  it('reports the shell, not the API, when the capability is withheld', () => {
    // The function is right there and still cannot be used.
    initCapabilities(WITHHOLDING_SHELL);
    withDisplayMedia(true);
    const probe = probeCapture();
    expect(probe.support).toBe('unsupported-shell');
    expect(probe.reason).toMatch(/desktop shell cannot capture/);
  });

  it('grants capture on the desktop (Chromium on every platform)', () => {
    initCapabilities(desktopCapabilities());
    expect(probeCapture().support).toBe('available');
  });

  it('blames the origin, not the browser, on an insecure context', () => {
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true });
    expect(probeCapture().support).toBe('insecure-context');
  });

  it('reports unavailable when the API is genuinely absent', () => {
    withDisplayMedia(false);
    expect(probeCapture().support).toBe('unavailable');
  });

  it('always explains itself', () => {
    // "An unexplained no is the failure" — every state carries a sentence.
    for (const setup of [
      () => initCapabilities(WITHHOLDING_SHELL),
      () => Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true }),
      () => withDisplayMedia(false),
    ]) {
      initCapabilities(BROWSER_CAPABILITIES);
      Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
      withDisplayMedia(true);
      setup();
      expect(probeCapture().reason.length).toBeGreaterThan(20);
    }
  });
});

/**
 * The audio leg is a bonus, so its failure must not cost the screen. Found live: the
 * desktop shell's WASAPI loopback could not open an output Voicemeeter held, and
 * Chromium rejected the whole capture with `NotReadableError: Device in use`.
 */
describe('startCapture', () => {
  const stream = { getVideoTracks: () => [], getTracks: () => [] } as unknown as MediaStream;
  const domError = (name: string, message = name) => Object.assign(new Error(message), { name });

  function withGetDisplayMedia(impl: (c?: MediaStreamConstraints) => Promise<MediaStream>) {
    const calls: (MediaStreamConstraints | undefined)[] = [];
    const getDisplayMedia = (c?: MediaStreamConstraints) => {
      calls.push(c);
      return impl(c);
    };
    Object.defineProperty(navigator, 'mediaDevices', {
      value: { getDisplayMedia },
      configurable: true,
    });
    return calls;
  }

  beforeEach(() => {
    initCapabilities(BROWSER_CAPABILITIES);
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
  });

  it('retries video-only when the audio device cannot be opened', async () => {
    const calls = withGetDisplayMedia(async (c) => {
      if (c?.audio) throw domError('NotReadableError', 'Device in use');
      return stream;
    });
    await expect(startCapture()).resolves.toBe(stream);
    expect(calls.map((c) => c?.audio)).toEqual([true, false]);
  });

  it('does not retry a dismissed picker', async () => {
    const calls = withGetDisplayMedia(async () => {
      throw domError('NotAllowedError');
    });
    await expect(startCapture()).rejects.toMatchObject({ cancelled: true });
    expect(calls).toHaveLength(1);
  });

  it('reports the retry failing as a real failure', async () => {
    withGetDisplayMedia(async () => {
      throw domError('NotReadableError', 'Could not start video source');
    });
    await expect(startCapture()).rejects.toMatchObject({
      cancelled: false,
      message: 'Screen capture failed: Could not start video source',
    });
  });
});
