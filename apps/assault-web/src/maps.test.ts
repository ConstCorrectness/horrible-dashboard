import { describe, expect, it } from 'vitest';
import { MAPS, MODE_OPTIONS, TRAIN_MODE, defaultMode, menuModes, modesFor } from './maps';

describe('the modes Create match offers per map', () => {
  it('lists the aim range, with aim and AWP arena but no bomb modes', () => {
    expect(MAPS.map((m) => m.id)).toContain('hd_aim');
    expect(modesFor('hd_aim')).toEqual(['dm', 'aim', 'awp']);
  });

  it('offers defuse and retake only where there are bomb sites', () => {
    expect(modesFor('hd_dust2')).toEqual(['dm', 'tdm', 'defuse', 'retake']);
    expect(modesFor('hd_facility')).toEqual(['dm', 'tdm']);
  });

  it("takes the server's list when it has one, in the menu's order", () => {
    const served = { hd_dust2: ['retake', 'dm'], hd_aim: ['awp', 'dm', 'ctf'] };
    expect(modesFor('hd_dust2', served)).toEqual(['dm', 'retake']);
    // A mode the menu does not open (ctf) is not offered even if served.
    expect(modesFor('hd_aim', served)).toEqual(['dm', 'awp']);
    // A map the server did not list falls back to the page's own table.
    expect(modesFor('hd_nuke', served)).toEqual(['dm', 'tdm', 'defuse', 'retake']);
  });

  it('never offers duel, which is a queue and not a room', () => {
    expect(MODE_OPTIONS).not.toContain('duel');
  });

  it('opens the aim range as offline training, not an empty room', () => {
    // A room on hd_aim is a deathmatch with nobody in it: lanes, no targets.
    expect(defaultMode('hd_aim')).toBe(TRAIN_MODE);
    expect(menuModes('hd_aim')[0]).toBe(TRAIN_MODE);
    expect(defaultMode('hd_dust2')).toBe('dm');
    expect(menuModes('hd_dust2')).not.toContain(TRAIN_MODE);
    // Never asked of the server, which has no such room mode.
    expect(modesFor('hd_aim')).not.toContain(TRAIN_MODE);
    expect(MODE_OPTIONS).not.toContain(TRAIN_MODE);
  });
});
