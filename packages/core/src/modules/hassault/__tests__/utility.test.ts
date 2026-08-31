/**
 * Throwing, on the client's side of the line.
 *
 * `GrenadeController` is pure and takes no three and no React, so the thing that
 * is actually easy to get wrong here can be checked headless: the throw is
 * **edge-triggered**. `throw` rides on a movement command, so a key read as
 * "held" sets the flag sixty times a second, the server's cooldown accepts one
 * and discards fifty-nine, and the player sees one grenade for a key they held
 * with nothing to explain where the rest went.
 */
import { describe, expect, it } from 'vitest';

import type { TacticalSpec } from '../api';
import type { SelfState } from '../net';
import { GrenadeController, THROW_COOLDOWN_MS } from '../utility';

function spec(id: string, carried: number, type: TacticalSpec['type'] = 'he'): TacticalSpec {
  return {
    id,
    name: id,
    type,
    fuseTime: 1.9,
    impact: false,
    radius: 9,
    duration: 0,
    maxDamage: 98,
    damagePerSecond: 0,
    bounceDamping: 0.45,
    carried,
  };
}

const SPECS = [spec('he', 1), spec('flash', 2, 'flash'), spec('smoke', 1, 'smoke')];

function controller(): GrenadeController {
  const c = new GrenadeController();
  c.setSpecs(SPECS);
  return c;
}

function you(overrides: Partial<SelfState> = {}): SelfState {
  return {
    hp: 100,
    alive: true,
    weapon: 0,
    ammo: 30,
    reserve: 90,
    reloading: false,
    reloadIn: 0,
    respawnIn: 0,
    protected: false,
    kills: 0,
    deaths: 0,
    mag: 30,
    hits: [],
    ...overrides,
  };
}

describe('throwing is edge-triggered', () => {
  it('one press is one throw, however many frames it spans', () => {
    const c = controller();
    c.press();
    expect(c.frame(1000, null).throw).toBe(true);
    // The key is still down as far as the panel is concerned, but nothing was
    // pressed *again*, so nothing else goes out.
    expect(c.frame(1016, null).throw).toBe(false);
    expect(c.frame(1032, null).throw).toBe(false);
  });

  it('refuses a second throw inside the cooldown', () => {
    const c = controller();
    c.select(1); // flash, two of them
    c.press();
    expect(c.frame(1000, null).throw).toBe(true);
    c.press();
    expect(c.frame(1000 + THROW_COOLDOWN_MS - 50, null).throw).toBe(false);
    c.press();
    expect(c.frame(1000 + THROW_COOLDOWN_MS + 10, null).throw).toBe(true);
  });

  it('throws the selected slot', () => {
    const c = controller();
    c.select(2);
    c.press();
    expect(c.frame(1000, null).nade).toBe(2);
  });

  it('carries the underhand flag through', () => {
    const c = controller();
    c.press(true);
    expect(c.frame(1000, null).lob).toBe(true);
  });
});

describe('carry counts', () => {
  it('drops the count the instant you throw, before the server answers', () => {
    // The same bargain the ammo counter makes: predicted, and corrected on the
    // next snapshot. A count that waited a round trip would let you press twice.
    const c = controller();
    c.select(1);
    expect(c.countOf(1)).toBe(2);
    c.press();
    c.frame(1000, null);
    expect(c.countOf(1)).toBe(1);
  });

  it('takes the server’s answer over its own', () => {
    const c = controller();
    c.press();
    c.frame(1000, null);
    expect(c.countOf(0)).toBe(0);
    // The server says the throw never happened — a cooldown we mispredicted, or
    // a command that never arrived. The count has to come back.
    c.frame(1100, you({ nades: { he: 1, flash: 2, smoke: 1 } }));
    expect(c.countOf(0)).toBe(1);
  });

  it('will not throw what it is not carrying', () => {
    // Selected back onto the spent slot deliberately — the auto-cycle above only
    // fires at the moment one runs out, so this is the state a player reaches by
    // pressing that grenade's own key afterwards.
    const c = controller();
    c.press();
    c.frame(1000, null);
    c.select(0);
    c.press();
    expect(c.frame(1000 + THROW_COOLDOWN_MS + 10, null).throw).toBe(false);
  });

  it('readies the next grenade you actually have once one runs out', () => {
    // Standing there holding an empty hand after your last smoke is a state with
    // nothing to do in it.
    const c = controller();
    c.select(0);
    c.press();
    c.frame(1000, null);
    expect(c.selected).not.toBe(0);
    expect(c.countOf(c.selected)).toBeGreaterThan(0);
  });
});

describe('selection', () => {
  it('selecting an empty slot is allowed, and shows a zero', () => {
    // A better answer to "why did nothing happen" than silently readying a
    // different grenade than the one whose key was pressed.
    const c = controller();
    c.press();
    c.frame(1000, null); // spends the HE
    c.select(0);
    expect(c.selected).toBe(0);
    expect(c.countOf(0)).toBe(0);
  });

  it('cycle skips empty slots', () => {
    const c = controller();
    c.select(0);
    c.press();
    c.frame(1000, null); // HE gone
    c.select(0);
    c.cycle();
    expect(c.countOf(c.selected)).toBeGreaterThan(0);
  });

  it('ignores a slot that does not exist', () => {
    const c = controller();
    c.select(99);
    expect(c.selected).toBe(0);
  });
});

describe('being dead', () => {
  it('throws nothing', () => {
    const c = controller();
    c.press();
    expect(c.frame(1000, you({ alive: false })).throw).toBe(false);
  });

  it('does not queue the press for the respawn', () => {
    // A throw asked for a frame before dying must not come out three seconds
    // later at a spawn point, pointed wherever the camera happens to be.
    const c = controller();
    c.press();
    c.frame(1000, you({ alive: false }));
    expect(c.frame(2000, you()).throw).toBe(false);
  });
});

describe('reset', () => {
  it('refills, matching reset_loadout on the server', () => {
    const c = controller();
    c.press();
    c.frame(1000, null);
    c.reset();
    expect(c.countOf(0)).toBe(1);
    expect(c.countOf(1)).toBe(2);
  });
});


/**
 * Equipping a grenade.
 *
 * Selecting used to only *ready* one — the gun stayed up and throwing was its
 * own key. Equipping is what lets the two mouse buttons mean throw and toss
 * without taking the right button away from the sniper's scope, which is the
 * whole of that weapon's identity.
 */
describe('equipping', () => {
  it('starts with a weapon in hand, not a grenade', () => {
    const nades = controller();
    expect(nades.equipped).toBe(false);
  });

  it('takes a grenade in hand and puts it away again', () => {
    const nades = controller();
    nades.equip(0);
    expect(nades.equipped).toBe(true);
    nades.holster();
    expect(nades.equipped).toBe(false);
  });

  it('ignores a slot that does not exist', () => {
    // The number keys are real input, and `9` on a three-grenade loadout means
    // nothing rather than "the last one".
    const nades = controller();
    nades.equip(99);
    expect(nades.equipped).toBe(false);
  });

  it('puts the grenade away the instant it leaves the hand', () => {
    // A throw is one action, not a mode you have to leave: the weapon comes back
    // up on the same frame the grenade goes out.
    const nades = controller();
    nades.equip(0);
    nades.press(false);
    const intent = nades.frame(1000, you());
    expect(intent.throw).toBe(true);
    expect(nades.justThrew).toBe(true);
    expect(nades.equipped).toBe(false);
  });

  it('reports the throw for exactly one frame', () => {
    // The panel reads `justThrew` to bring the weapon back. Left latched, it
    // would re-equip on every subsequent frame.
    const nades = controller();
    nades.equip(0);
    nades.press(false);
    nades.frame(1000, you());
    expect(nades.justThrew).toBe(true);
    nades.frame(2000, you());
    expect(nades.justThrew).toBe(false);
  });

  it('throws overhand or underhand depending on which button', () => {
    // Left is the full throw, right is the short lob — the two the server has
    // always known about (`LOB_SCALE`), now on the buttons a hand is already on.
    const over = controller();
    over.equip(0);
    over.press(false);
    expect(over.frame(1000, you()).lob).toBe(false);

    const under = controller();
    under.equip(0);
    under.press(true);
    expect(under.frame(1000, you()).lob).toBe(true);
  });

  it('puts the pouch away when the last grenade is gone', () => {
    // Standing there with the weapon stowed and both mouse buttons doing nothing
    // is a state with nothing to do in it.
    const nades = controller();
    const specs = nades.catalogue;
    let now = 0;
    for (const spec of specs) {
      const slot = specs.indexOf(spec);
      for (let i = 0; i < spec.carried; i++) {
        now += THROW_COOLDOWN_MS + 1;
        nades.equip(slot);
        nades.press(false);
        nades.frame(now, you());
      }
    }
    expect(nades.equipped).toBe(false);
    now += THROW_COOLDOWN_MS + 1;
    nades.equip(0);
    nades.press(false);
    expect(nades.frame(now, you()).throw).toBe(false);
  });

  it('drops the grenade when you die', () => {
    // Coming back holding one you readied in a previous life, with the weapon
    // stowed, is a spawn you cannot shoot from.
    const nades = controller();
    nades.equip(0);
    nades.frame(1000, you({ alive: false }));
    expect(nades.equipped).toBe(false);
  });

  it('does not fire a throw queued on the frame it was holstered', () => {
    const nades = controller();
    nades.equip(0);
    nades.press(false);
    nades.holster();
    expect(nades.frame(1000, you()).throw).toBe(false);
  });
});
