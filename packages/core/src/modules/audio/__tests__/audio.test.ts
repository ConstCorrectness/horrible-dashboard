/**
 * Tests for the parts of the mixer that are pure logic.
 *
 * The graph itself is not tested here — vitest has no Web Audio implementation,
 * and a mock of `AudioContext` would only assert that the mock was called. What
 * *is* tested is the logic whose failures are silent in a browser: device
 * re-resolution across sessions, the virtual-device heuristic, and the fader
 * curve.
 */

import { describe, expect, it } from 'vitest';

import { hasDeviceLabels, isVirtualDevice, resolveDeviceId } from '../devices';
import { dbToGain } from '../engine';
import { busHasSource, SHARE_BUS_ID } from '../store';
import type { MixerState, StripState } from '../types';

function device(deviceId: string, label: string): MediaDeviceInfo {
  return {
    deviceId,
    label,
    kind: 'audiooutput',
    groupId: 'g',
    toJSON: () => ({}),
  } as MediaDeviceInfo;
}

describe('resolveDeviceId', () => {
  const devices = [device('abc123', 'Headphones'), device('def456', 'VoiceMeeter Input (VAIO)')];

  it('prefers an exact id match', () => {
    expect(resolveDeviceId('abc123', 'Headphones', devices)).toBe('abc123');
  });

  it('falls back to the label when the id has rotated', () => {
    // The scenario this exists for: the browser regenerated its device ids
    // (site data cleared, permission re-granted), so every saved id names
    // nothing. Without the label fallback every bus silently reverts to the
    // default output at once.
    expect(resolveDeviceId('stale-id', 'Headphones', devices)).toBe('abc123');
  });

  it('returns empty for a device that is genuinely gone', () => {
    // Empty means "system default", which is the right place for audio to go
    // when the chosen headphones are unplugged — better than silence.
    expect(resolveDeviceId('gone', 'Studio Monitors', devices)).toBe('');
  });

  it('returns empty when nothing was ever saved', () => {
    expect(resolveDeviceId('', '', devices)).toBe('');
  });

  it('prefers the id when two devices share a label', () => {
    // Two identical USB headsets: the label is ambiguous, the id is not.
    const twins = [device('first', 'USB Headset'), device('second', 'USB Headset')];
    expect(resolveDeviceId('second', 'USB Headset', twins)).toBe('second');
  });
});

describe('isVirtualDevice', () => {
  it('recognises the cables on each platform', () => {
    expect(isVirtualDevice('VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)')).toBe(true);
    expect(isVirtualDevice('CABLE Input (VB-Audio Virtual Cable)')).toBe(true);
    expect(isVirtualDevice('BlackHole 2ch')).toBe(true);
    expect(isVirtualDevice('horrible_dashboard null sink')).toBe(true);
  });

  it('does not claim real hardware is virtual', () => {
    expect(isVirtualDevice('Speakers (Realtek High Definition Audio)')).toBe(false);
    expect(isVirtualDevice('Headphones')).toBe(false);
  });
});

describe('hasDeviceLabels', () => {
  it('is false before a permission grant', () => {
    // The browser returns the right *number* of devices with empty labels until
    // a media permission is granted. That is a prompt to show, not an empty
    // device list, and a picker rendered in this state is a list of blanks.
    expect(hasDeviceLabels([device('a', ''), device('b', '')])).toBe(false);
  });

  it('is true once any label is populated', () => {
    expect(hasDeviceLabels([device('a', ''), device('b', 'Headphones')])).toBe(true);
  });

  it('is false for an empty list', () => {
    expect(hasDeviceLabels([])).toBe(false);
  });
});

describe('dbToGain', () => {
  it('maps 0 dB to unity', () => {
    expect(dbToGain(0)).toBeCloseTo(1);
  });

  it('maps -6 dB to about half amplitude', () => {
    expect(dbToGain(-6)).toBeCloseTo(0.501, 2);
  });

  it('treats the bottom of the fader as true silence', () => {
    // -60 dB is the fader floor. Left as a ratio it is 0.001, which is quiet
    // but audible on a loud system — a "muted" channel that still leaks.
    expect(dbToGain(-60)).toBe(0);
    expect(dbToGain(-90)).toBe(0);
  });

  it('allows boost above unity', () => {
    expect(dbToGain(6)).toBeGreaterThan(1);
  });
});

function strip(id: string, sends: Record<string, boolean>): StripState {
  return { id, label: id, gain: 0, muted: false, sends };
}

function mixerState(strips: StripState[]): MixerState {
  return { version: 1, buses: [], strips, inputDeviceId: '', inputDeviceLabel: '' };
}

describe('busHasSource', () => {
  // The reason this exists rather than counting tracks: a
  // MediaStreamAudioDestinationNode always carries exactly one audio track,
  // silent or not. Once `ensureShareBus` started actually creating the viewers'
  // bus, track-counting reported every share as "screen + audio" — a stream
  // claiming to send sound it was not sending.
  it('is false when the bus exists but nothing is routed to it', () => {
    const state = mixerState([strip('mic', { [SHARE_BUS_ID]: false })]);
    expect(busHasSource(state, SHARE_BUS_ID)).toBe(false);
  });

  it('is true as soon as one strip sends to it', () => {
    const state = mixerState([
      strip('mic', { [SHARE_BUS_ID]: false }),
      strip('karaoke', { [SHARE_BUS_ID]: true }),
    ]);
    expect(busHasSource(state, SHARE_BUS_ID)).toBe(true);
  });

  it('counts a muted strip as routed', () => {
    // Muted is one click from audible and the mixer already shows it as muted.
    // Treating it as "nothing routed" would tell the host to make a routing they
    // already made.
    const state = mixerState([{ ...strip('mic', { [SHARE_BUS_ID]: true }), muted: true }]);
    expect(busHasSource(state, SHARE_BUS_ID)).toBe(true);
  });

  it('ignores sends to other buses', () => {
    const state = mixerState([strip('mic', { A1: true })]);
    expect(busHasSource(state, SHARE_BUS_ID)).toBe(false);
  });

  it('is false before the mixer document has loaded', () => {
    // The unloaded case is the one that used to be silent: no document meant no
    // bus, and the pane blamed the host for not routing a strip.
    expect(busHasSource(null, SHARE_BUS_ID)).toBe(false);
  });
});
