import { describe, expect, it } from 'vitest';

import type { WeaponSpec } from '../api';
import { ShotController, recoilKick } from '../combat';
import type { SelfState } from '../net';

function spec(over: Partial<WeaponSpec> = {}): WeaponSpec {
  return {
    id: 'assault',
    name: 'Assault Rifle',
    damage: 21,
    headMultiplier: 2,
    rpm: 600,
    interval: 0.1,
    mag: 20,
    reserve: 120,
    reloadTime: 1.9,
    spread: 0.02,
    pellets: 1,
    range: 200,
    auto: true,
    ...over,
  };
}

const AUTO = spec();
const SEMI = spec({
  id: 'sniper',
  name: 'Sniper',
  auto: false,
  interval: 1,
  mag: 5,
  spread: 0.002,
});

function you(over: Partial<SelfState> = {}): SelfState {
  return {
    hp: 100,
    alive: true,
    weapon: 0,
    ammo: 20,
    reserve: 120,
    reloading: false,
    reloadIn: 0,
    respawnIn: 0,
    protected: false,
    kills: 0,
    deaths: 0,
    mag: 20,
    hits: [],
    ...over,
  };
}

function controller(specs: WeaponSpec[] = [AUTO, SEMI], slot = 0): ShotController {
  const shots = new ShotController();
  shots.setWeapons(specs, slot);
  return shots;
}

describe('ShotController trigger', () => {
  it('does not fire until the button is held', () => {
    const shots = controller();
    expect(shots.frame(0, 100, you()).fire).toBe(false);
  });

  it('fires on the frame the button goes down', () => {
    const shots = controller();
    shots.press();
    expect(shots.frame(0, 100, you()).fire).toBe(true);
  });

  it('carries the render time with the shot, which is what the server rewinds to', () => {
    const shots = controller();
    shots.press();
    const intent = shots.frame(0, 4321, you());
    expect(intent.fire).toBe(true);
    expect(intent.viewT).toBe(4321);
  });

  it('holds an automatic weapon at its fire interval, not at the frame rate', () => {
    const shots = controller();
    shots.press();
    let fired = 0;
    // One second of 60 fps frames against a 0.1 s interval.
    for (let frame = 0; frame <= 60; frame++) {
      if (shots.frame((frame * 1000) / 60, 0, you()).fire) fired += 1;
    }
    expect(fired).toBe(11);
  });

  it('needs the button released between shots on a semi-automatic', () => {
    const shots = controller([AUTO, SEMI], 1);
    // The server's word on which weapon we hold, or `frame` adopts slot 0 and
    // this becomes a test about the rifle.
    const holding = () => you({ weapon: 1, ammo: 5 });
    shots.press();
    expect(shots.frame(0, 0, holding()).fire).toBe(true);
    // Far past its interval, but the trigger has not been let go.
    expect(shots.frame(5000, 0, holding()).fire).toBe(false);
    shots.release();
    shots.press();
    expect(shots.frame(6000, 0, holding()).fire).toBe(true);
  });

  it('stops when the button is released', () => {
    const shots = controller();
    shots.press();
    shots.frame(0, 0, you());
    shots.release();
    expect(shots.frame(1000, 0, you()).fire).toBe(false);
  });
});

describe('ShotController state gates', () => {
  it('does not fire while dead', () => {
    const shots = controller();
    shots.press();
    expect(shots.frame(0, 0, you({ alive: false })).fire).toBe(false);
  });

  it('does not fire while reloading', () => {
    const shots = controller();
    shots.press();
    expect(shots.frame(0, 0, you({ reloading: true })).fire).toBe(false);
  });

  it('does not fire offline, when there is no authoritative state at all', () => {
    const shots = controller();
    shots.press();
    expect(shots.frame(0, 0, null).fire).toBe(false);
  });

  it('asks for a reload rather than firing an empty magazine', () => {
    const shots = controller();
    shots.press();
    const intent = shots.frame(0, 0, you({ ammo: 0 }));
    expect(intent.fire).toBe(false);
    expect(intent.reload).toBe(true);
  });

  it('predicts the magazine down on the frame it fires', () => {
    const shots = controller();
    shots.press();
    shots.frame(0, 0, you({ ammo: 7 }));
    expect(shots.ammo).toBe(6);
  });

  it('takes the server’s ammo back on the next snapshot', () => {
    const shots = controller();
    shots.press();
    shots.frame(0, 0, you({ ammo: 7 }));
    expect(shots.ammo).toBe(6);
    // The server disagrees — it always wins, exactly as it does for position.
    shots.frame(1, 0, you({ ammo: 3 }));
    expect(shots.ammo).toBe(3);
  });
});

describe('ShotController weapon selection', () => {
  it('sends a switch once and then stops repeating it', () => {
    const shots = controller();
    shots.select(1);
    expect(shots.frame(0, 0, you()).weapon).toBe(1);
    expect(shots.frame(16, 0, you({ weapon: 1 })).weapon).toBe(-1);
  });

  it('ignores a slot that does not exist', () => {
    const shots = controller();
    shots.select(9);
    expect(shots.slot).toBe(0);
    expect(shots.frame(0, 0, you()).weapon).toBe(-1);
  });

  it('holds the pending slot until the server catches up', () => {
    const shots = controller();
    shots.select(1);
    shots.frame(0, 0, you({ weapon: 0 }));
    // The snapshot in flight still says slot 0; adopting it would flip the HUD
    // back for a whole round trip.
    expect(shots.slot).toBe(1);
  });

  it('adopts the server’s weapon when nothing is pending', () => {
    const shots = controller();
    shots.frame(0, 0, you({ weapon: 1 }));
    expect(shots.slot).toBe(1);
  });

  it('cycles through the slots and wraps', () => {
    const shots = controller();
    shots.cycle(-1);
    expect(shots.slot).toBe(1);
    shots.cycle(1);
    expect(shots.slot).toBe(0);
  });

  it('does not carry a held trigger through a switch', () => {
    const shots = controller([SEMI, SEMI]);
    shots.press();
    expect(shots.frame(0, 0, you()).fire).toBe(true);
    shots.select(1);
    expect(shots.frame(9000, 0, you({ weapon: 1 })).fire).toBe(false);
  });

  it('sends a reload request once', () => {
    const shots = controller();
    shots.requestReload();
    expect(shots.frame(0, 0, you()).reload).toBe(true);
    expect(shots.frame(16, 0, you()).reload).toBe(false);
  });
});

describe('ShotController recoil', () => {
  it('kicks the view up on the frame it fires', () => {
    const shots = controller();
    shots.press();
    shots.frame(0, 0, you());
    expect(shots.recoil(1 / 60).pitch).toBeCloseTo(recoilKick(AUTO), 5);
  });

  it('recovers afterwards, and never past where the burst began', () => {
    const shots = controller();
    shots.press();
    shots.frame(0, 0, you());
    let total = shots.recoil(1 / 60).pitch;
    shots.release();
    // Long enough to give every radian back several times over.
    for (let i = 0; i < 300; i++) total += shots.recoil(1 / 60).pitch;
    expect(total).toBeCloseTo(0, 4);
    expect(shots.recoil(1 / 60).pitch).toBe(0);
  });

  it('does not recover on a frame it also fired, or a burst would stand still', () => {
    const shots = controller();
    shots.press();
    shots.frame(0, 0, you());
    shots.recoil(1 / 60);
    shots.frame(200, 0, you());
    expect(shots.recoil(1 / 60).pitch).toBeGreaterThan(0);
  });

  it('opens the crosshair for a wider weapon', () => {
    const wide = controller([spec({ spread: 0.08 })]);
    const tight = controller([spec({ spread: 0.001 })]);
    expect(wide.crosshairSpread()).toBeGreaterThan(tight.crosshairSpread());
  });

  it('forgets everything on reset, so a new match starts cold', () => {
    const shots = controller();
    shots.press();
    shots.frame(0, 0, you());
    shots.reset();
    expect(shots.recoil(1 / 60).pitch).toBe(0);
    expect(shots.frame(1, 0, you()).fire).toBe(false);
  });
});
