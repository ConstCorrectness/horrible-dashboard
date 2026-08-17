/**
 * The pips appear exactly when the top strip is not there to switch with.
 *
 * A floating desktop hides the workspace strip, so something has to switch; a tiling
 * desktop keeps the strip, and a second always-visible switcher beside it is the
 * redundancy that got the pips turned off by default in the first place.
 */
import { describe, expect, it } from 'vitest';

import { zonesForMode } from '../taskbar';

const DEFAULT_ZONES = ['start', 'windows', 'spacer', 'mx', 'tray', 'clock'];

describe('zonesForMode', () => {
  it('leaves a tiling desktop alone — the strip is the switcher there', () => {
    expect(zonesForMode(DEFAULT_ZONES, 'tiling')).toEqual(DEFAULT_ZONES);
  });

  it('adds the pips on a floating desktop, where the strip is hidden', () => {
    expect(zonesForMode(DEFAULT_ZONES, 'floating')).toContain('desktops');
  });

  it('puts them right after the start button and before the pane list', () => {
    const zones = zonesForMode(DEFAULT_ZONES, 'floating');
    expect(zones.indexOf('desktops')).toBe(zones.indexOf('start') + 1);
    expect(zones.indexOf('desktops')).toBeLessThan(zones.indexOf('windows'));
  });

  it('respects an explicit choice rather than duplicating or moving it', () => {
    const custom = ['start', 'windows', 'desktops', 'clock'];
    expect(zonesForMode(custom, 'floating')).toEqual(custom);
  });

  it('still adds them when the start button has been removed', () => {
    expect(zonesForMode(['windows', 'clock'], 'floating')).toEqual([
      'desktops',
      'windows',
      'clock',
    ]);
  });

  it('does not mutate the stored zones', () => {
    const stored = [...DEFAULT_ZONES];
    zonesForMode(stored, 'floating');
    expect(stored).toEqual(DEFAULT_ZONES);
  });

  it('returns the same array when nothing changes, so the caller can skip a copy', () => {
    const stored = [...DEFAULT_ZONES];
    expect(zonesForMode(stored, 'tiling')).toBe(stored);
  });
});
