// @vitest-environment happy-dom
/**
 * The capture probe's four states.
 *
 * The one worth testing hardest is `unsupported-shell`: it exists because
 * WKWebView exposes `getDisplayMedia` and then rejects it with the same
 * `NotAllowedError` a dismissed picker produces, so the API is present and lying
 * and no amount of feature detection can see it. If the capability check ever
 * moves *after* the feature detection, macOS silently goes back to reporting
 * "available" and the share fails with no message at all.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { BROWSER_CAPABILITIES, desktopCapabilities, initCapabilities } from '../../../capabilities';
import { probeCapture } from '../capture';

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
    // The macOS desktop: the function is right there and still cannot be used.
    initCapabilities(desktopCapabilities('mac'));
    withDisplayMedia(true);
    const probe = probeCapture();
    expect(probe.support).toBe('unsupported-shell');
    expect(probe.reason).toMatch(/macOS/);
  });

  it('grants capture on the Windows and Linux desktop', () => {
    for (const platform of ['win', 'linux'] as const) {
      initCapabilities(desktopCapabilities(platform));
      expect(probeCapture().support).toBe('available');
    }
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
      () => initCapabilities(desktopCapabilities('mac')),
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
