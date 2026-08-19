/**
 * A weapon's voice, which is derived rather than tabulated.
 *
 * What is worth pinning is not the exact numbers — they are a synth patch, and
 * tuning them is allowed — but the *relationships* a listener actually uses: a
 * sniper does not sound like a rifle, a shotgun does not sound like either, and
 * a knife does not sound like a gun at all. Those are the three discriminations
 * the mechanic is about, and each one silently disappears if the formula is
 * rewritten to stop reading the number it depends on.
 */
import { describe, expect, it } from 'vitest';

import type { WeaponSpec } from '../api';
import { weaponVoice } from '../audio';

function weapon(overrides: Partial<WeaponSpec>): WeaponSpec {
  return {
    id: 'assault',
    name: 'assault rifle',
    damage: 30,
    headMultiplier: 2,
    rpm: 700,
    interval: 60 / 700,
    mag: 30,
    reserve: 90,
    reloadTime: 2,
    spread: 0.02,
    pellets: 1,
    range: 200,
    auto: true,
    kickback: 4,
    zoomLevels: [],
    hipfireSpread: 0.02,
    ...overrides,
  };
}

const ASSAULT = weapon({});
const SNIPER = weapon({ id: 'sniper', damage: 90, rpm: 62, zoomLevels: [2, 4] });
const SHOTGUN = weapon({ id: 'shotgun', damage: 22, rpm: 70, pellets: 8 });
const KNIFE = weapon({ id: 'knife', damage: 55, rpm: 120, range: 5, kickback: 0 });

describe('weaponVoice', () => {
  it('pitches a slow heavy weapon below a fast light one', () => {
    // The cue that separates a rifle from a sniper across a map. Tied to `rpm`,
    // so a balance change to the fire rate moves the sound with it.
    expect(weaponVoice(SNIPER).frequency).toBeLessThan(weaponVoice(ASSAULT).frequency);
    expect(weaponVoice(SNIPER).decay).toBeGreaterThan(weaponVoice(ASSAULT).decay);
    // Heavier, so the thump under it is lower.
    expect(weaponVoice(SNIPER).body).toBeLessThan(weaponVoice(ASSAULT).body);
  });

  it('widens the band for a weapon that fires pellets', () => {
    // A narrow band rings and a wide one hisses: eight pellets is a blast, not
    // a bang, and `pellets` is the only thing that says so.
    expect(weaponVoice(SHOTGUN).q).toBeLessThan(weaponVoice(ASSAULT).q);
  });

  it('does not make the knife sound like a gun', () => {
    // Being able to hear that somebody is carrying a knife is the reason its
    // silence is worth anything — so its voice has to be unmistakably not a
    // gunshot, not merely a quieter one.
    const knife = weaponVoice(KNIFE);
    expect(knife.body).toBe(0);
    expect(knife.gain).toBeLessThan(weaponVoice(ASSAULT).gain);
    expect(knife.frequency).toBeGreaterThan(weaponVoice(ASSAULT).frequency);
  });

  it('gives an unknown weapon the generic shot rather than silence', () => {
    // A client older than the server must still hear a gunshot. The alternative
    // is a shot nobody can hear because a weapon was added.
    const unknown = weaponVoice(undefined);
    expect(unknown.gain).toBeGreaterThan(0);
    expect(unknown.decay).toBeGreaterThan(0);
  });
});
