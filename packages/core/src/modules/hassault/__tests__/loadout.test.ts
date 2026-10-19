/**
 * The roster in the browser: the CS number keys, the loadout fallback rule the
 * range shares with `weapons.loadout_for`, and which model a weapon is drawn as.
 */
import { describe, expect, it } from 'vitest';

import type { WeaponSpec } from '../api';
import { ShotController } from '../combat';
import { loadoutSlots, parseLoadout } from '../loadout';
import { setWeaponTable, visualId, visualSlot } from '../weapon-visuals';

const spec = (id: string, slot: WeaponSpec['slot'], model?: string): WeaponSpec =>
  ({ id, name: id, slot, model: model ?? id, mag: 10, reserve: 10, interval: 0.1 }) as WeaponSpec;

const TABLE: WeaponSpec[] = [
  spec('knife', 'melee'),
  spec('pistol', 'secondary'),
  spec('assault', 'primary'),
  spec('shotgun', 'primary'),
  spec('sniper', 'primary'),
  spec('hand_cannon', 'secondary', 'pistol'),
  spec('lancer', 'primary', 'assault'),
];

describe('loadouts', () => {
  it('maps a choice to indexes, falling back slot by slot', () => {
    expect(loadoutSlots(TABLE, { primary: 'lancer', secondary: 'hand_cannon' })).toEqual({
      primary: 6,
      secondary: 5,
      melee: 0,
    });
    // A sniper is not a sidearm, and an id nobody knows is nobody's.
    expect(loadoutSlots(TABLE, { primary: 'nope', secondary: 'sniper' })).toEqual({
      primary: 2,
      secondary: 1,
      melee: 0,
    });
  });

  it('reads a stored choice and refuses junk', () => {
    expect(parseLoadout('{"primary":"lancer"}')).toEqual({ primary: 'lancer' });
    expect(parseLoadout('not json')).toBeNull();
    expect(parseLoadout('')).toBeNull();
  });
});

describe('the number keys', () => {
  function controller() {
    const c = new ShotController();
    c.weapons = TABLE;
    c.slot = 6;
    c.heldWeapons = [0, 5, 6];
    c.slots = { primary: 6, secondary: 5, melee: 0 };
    return c;
  }

  it('1 primary, 2 secondary, 3 knife', () => {
    const c = controller();
    c.selectKey(2);
    expect(c.slot).toBe(5);
    c.selectKey(3);
    expect(c.slot).toBe(0);
    c.selectKey(1);
    expect(c.slot).toBe(6);
  });

  it('refuses a weapon not in hand, and the wheel skips it', () => {
    const c = controller();
    c.select(2);
    expect(c.slot).toBe(6);
    c.cycle(1);
    expect(c.slot).toBe(0);
  });

  it('against an older server, key N is weapon N', () => {
    const c = controller();
    c.heldWeapons = null;
    c.slots = null;
    c.selectKey(3);
    expect(c.slot).toBe(2);
  });
});

describe('weapon visuals', () => {
  it('draws a weapon as the model it borrows', () => {
    setWeaponTable(TABLE);
    expect(visualId('lancer')).toBe('assault');
    expect(visualId(5)).toBe('pistol');
    expect(visualSlot(6)).toBe(2);
    // A newer server's weapon is drawn as a gun, not as nothing.
    expect(visualId('railgun')).toBe('assault');
  });
});
