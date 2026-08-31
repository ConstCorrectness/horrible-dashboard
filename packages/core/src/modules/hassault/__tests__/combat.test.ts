import { describe, expect, it } from 'vitest';

import type { WeaponSpec } from '../api';
import { CROUCH_KICK_SCALE, ShotController, kickVector, recoilKick } from '../combat';
import type { SelfState } from '../net';

function spec(over: Partial<WeaponSpec> = {}): WeaponSpec {
  const weapon: WeaponSpec = {
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
    kickback: 0,
    zoomLevels: [],
    hipfireSpread: 0.02,
    ...over,
  };
  // Derived, not defaulted: the server reports `hipfireSpread == spread` for
  // every weapon without a scope, so a fixture that pinned it would let a case
  // that overrides only `spread` silently describe a weapon the backend cannot
  // produce — which is exactly how this factory got the crosshair test wrong.
  return over.hipfireSpread === undefined ? { ...weapon, hipfireSpread: weapon.spread } : weapon;
}

const AUTO = spec();
const SEMI = spec({
  id: 'sniper',
  name: 'Sniper',
  auto: false,
  kickback: 0,
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

/**
 * The shooter's own recoil push — AssaultCube's `attackphysics`, and the whole of
 * shoot-jumping.
 *
 * Mirrored by `test_hassault_noise.py`'s kickback tests on the server side. These
 * two have to agree exactly or every shot mispredicts: the client applies this
 * impulse on the frame it fires and the server applies its own after the same
 * command, so a disagreement shows up as the shooter being yanked back into place a
 * round trip later.
 */
describe('kickVector', () => {
  const shotgun = spec({ id: 'shotgun', kickback: 9.5 });

  it('pushes opposite the aim', () => {
    // Aiming straight down must push straight up: that *is* the shoot-jump.
    const down = kickVector(shotgun, 0, -Math.PI / 2);
    expect(down.z).toBeCloseTo(shotgun.kickback, 6);
    expect(Math.hypot(down.x, down.y)).toBeCloseTo(0, 6);

    // Aiming east pushes west.
    const east = kickVector(shotgun, 0, 0);
    expect(east.x).toBeCloseTo(-shotgun.kickback, 6);
    expect(east.z).toBeCloseTo(0, 6);
  });

  it('is braced by crouching', () => {
    const standing = kickVector(shotgun, 0, 0, false);
    const crouched = kickVector(shotgun, 0, 0, true);
    expect(Math.abs(crouched.x)).toBeCloseTo(Math.abs(standing.x) * CROUCH_KICK_SCALE, 6);
  });

  it('is nothing for a weapon with no kickback, and for no weapon at all', () => {
    expect(kickVector(spec({ kickback: 0 }), 0, -1.5)).toEqual({ x: 0, y: 0, z: 0 });
    expect(kickVector(undefined, 0, -1.5)).toEqual({ x: 0, y: 0, z: 0 });
  });

  it('has magnitude equal to the served number, whatever the angle', () => {
    // The one invariant worth pinning: the client must not be able to derive a
    // larger push than the server will apply, at any aim.
    for (const [yaw, pitch] of [
      [0, 0],
      [1.2, -0.7],
      [-2.4, 1.1],
      [3, 0.3],
    ] as const) {
      const kick = kickVector(shotgun, yaw, pitch);
      expect(Math.hypot(kick.x, kick.y, kick.z)).toBeCloseTo(shotgun.kickback, 6);
    }
  });
});

/**
 * The scope.
 *
 * Its accuracy half is the server's (`weapons.clamp_zoom`/`effective_spread`);
 * what lives here is the state machine — which weapon can scope, what clears it,
 * and that the shot actually carries the step it was taken at. A zoom that the
 * command forgets to mention is a scope that silently does nothing.
 */
const SCOPED = spec({
  id: 'sniper',
  name: 'Sniper',
  auto: false,
  interval: 1,
  mag: 5,
  spread: 0.002,
  hipfireSpread: 0.055,
  zoomLevels: [2, 4],
});

describe('ShotController scope', () => {
  it('steps through the magnifications and back to none', () => {
    const shots = controller([SCOPED]);
    expect(shots.magnification()).toBe(1);
    shots.cycleScope();
    expect([shots.scoped, shots.magnification()]).toEqual([1, 2]);
    shots.cycleScope();
    expect([shots.scoped, shots.magnification()]).toEqual([2, 4]);
    shots.cycleScope();
    expect([shots.scoped, shots.magnification()]).toEqual([0, 1]);
  });

  it('ignores the scope on a weapon that has none', () => {
    const shots = controller([AUTO]);
    shots.cycleScope();
    expect(shots.scoped).toBe(0);
    expect(shots.magnification()).toBe(1);
  });

  it('carries the zoom step on the shot that was taken at it', () => {
    const shots = controller([SCOPED]);
    // Twice, to the second step: a command that carried a boolean would pass
    // this at 1x and quietly cost the server the difference between 2x and 4x.
    shots.cycleScope();
    shots.cycleScope();
    shots.press();
    const intent = shots.frame(0, 100, you({ weapon: 0, ammo: 5, mag: 5 }));
    expect(intent.fire).toBe(true);
    expect(intent.scoped).toBe(2);
  });

  it('drops the scope when the weapon changes', () => {
    const shots = controller([SCOPED, AUTO]);
    shots.cycleScope();
    shots.select(1);
    expect(shots.scoped).toBe(0);
  });

  it('drops a scope the server switched us out of', () => {
    // The slot can change without us asking — a correction, a pickup — and only
    // the frame sees it. Left alone, the FOV would stay at 4x on a rifle.
    const shots = controller([SCOPED, AUTO]);
    shots.cycleScope();
    shots.cycleScope();
    expect(shots.scoped).toBe(2);
    shots.frame(0, 100, you({ weapon: 1, ammo: 20 }));
    expect(shots.scoped).toBe(0);
  });

  it('drops the scope on death, so you do not respawn zoomed in', () => {
    const shots = controller([SCOPED]);
    shots.cycleScope();
    shots.frame(0, 100, you({ weapon: 0, alive: false }));
    expect(shots.scoped).toBe(0);
  });

  it('shows the hip-fire cone in the crosshair until you scope', () => {
    // The crosshair is the only warning that an unscoped sniper is a gamble, so
    // it has to read the cone the next shot will actually use.
    const shots = controller([SCOPED]);
    const hip = shots.crosshairSpread();
    shots.cycleScope();
    expect(shots.crosshairSpread()).toBeLessThan(hip);
  });

  it('is cleared by reset, like every other piece of trigger state', () => {
    const shots = controller([SCOPED]);
    shots.cycleScope();
    shots.reset();
    expect(shots.scoped).toBe(0);
  });
});


/**
 * The recoil pattern.
 *
 * What is being pinned is the *application*, not the table — the offsets are
 * served, so there is one copy of them. The thing that can go wrong here is
 * silent and reads as a tuning problem: the table is absolute and the camera
 * accumulates, so applying the absolute walks the crosshair away by the running
 * sum of everything so far.
 */
describe('the spray pattern', () => {
  // A small, obviously-shaped table: a straight climb of 0.01 rad a shot with a
  // lateral drift that reverses, so a delta bug and a sign bug look different.
  const PATTERN: [number, number][] = [
    [0, 0],
    [0.002, 0.01],
    [0.004, 0.02],
    [-0.002, 0.03],
  ];
  const PATTERNED = spec({ spray: PATTERN, sprayReset: 0.35, residualSpread: 0.004 });

  /** Fire `n` shots and total the view-angle deltas the controller asked for. */
  function burst(shots: ShotController, n: number): { yaw: number; pitch: number } {
    let yaw = 0;
    let pitch = 0;
    for (let i = 0; i < n; i++) {
      shots.press();
      const intent = shots.frame(i * 200, 100, you());
      expect(intent.fire).toBe(true);
      // `dt` of zero, so recovery cannot contribute — this is measuring the
      // kick, and a recovery term mixed into it would hide a delta bug behind a
      // number that happens to look plausible.
      const delta = shots.recoil(0);
      yaw += delta.yaw;
      pitch += delta.pitch;
      shots.release();
    }
    return { yaw, pitch };
  }

  it('lands the camera exactly on the pattern, not on its running sum', () => {
    // **The absolute-versus-delta test.** After N shots the accumulated camera
    // offset must equal `spray[N-1]` — the position, not the sum of positions.
    // Getting this wrong is unusable within half a magazine and looks exactly
    // like a badly-chosen constant.
    const shots = controller([PATTERNED]);
    for (let n = 1; n <= PATTERN.length; n++) {
      const fresh = controller([PATTERNED]);
      const total = burst(fresh, n);
      expect(total.yaw).toBeCloseTo(PATTERN[n - 1][0], 9);
      expect(total.pitch).toBeCloseTo(PATTERN[n - 1][1], 9);
    }
    expect(shots.sprayIndex).toBe(0);
  });

  it('holds at the last entry past the end of the table', () => {
    // A pattern that restarted mid-magazine would be unlearnable, and the extra
    // shots must contribute nothing rather than wrapping back to the start.
    const shots = controller([PATTERNED]);
    const total = burst(shots, PATTERN.length + 5);
    expect(total.yaw).toBeCloseTo(PATTERN[PATTERN.length - 1][0], 9);
    expect(total.pitch).toBeCloseTo(PATTERN[PATTERN.length - 1][1], 9);
  });

  it('adopts the server’s index rather than keeping its own count', () => {
    // Without this, a shot the server refused — rate, empty, dead — leaves the
    // client one step ahead of the pattern for the rest of the magazine, kicking
    // the camera for a bullet that never left.
    const shots = controller([PATTERNED]);
    shots.press();
    shots.frame(0, 100, you());
    shots.recoil(0);
    expect(shots.sprayIndex).toBe(1);

    shots.release();
    shots.press();
    shots.frame(200, 100, you({ sprayIndex: 0 }));
    const delta = shots.recoil(0);
    // Back at the top: the step from nothing to `spray[0]`, which is zero.
    expect(delta.yaw).toBeCloseTo(PATTERN[0][0], 9);
    expect(delta.pitch).toBeCloseTo(PATTERN[0][1], 9);
  });

  it('falls back to noise for a weapon with no pattern', () => {
    // Four of the five weapons, and any server too old to send one. A rifle with
    // *no* recoil would be worse than the noise this replaces.
    const shots = controller([spec({ spray: [] })]);
    shots.press();
    shots.frame(0, 100, you());
    expect(shots.recoil(0).pitch).toBeGreaterThan(0);
  });

  it('draws the crosshair at the cone the next shot actually uses', () => {
    // A patterned weapon is left with `residualSpread`; drawing `spread` would
    // advertise a cone five times the real one, telling a player not to take a
    // shot the weapon would have made.
    const patterned = controller([PATTERNED]);
    const plain = controller([spec({ spray: [] })]);
    expect(patterned.crosshairSpread()).toBeLessThan(plain.crosshairSpread());
  });

  it('gives back the climb but never the drift', () => {
    // Recovery pulls the view down. Giving back the lateral component too would
    // drag the crosshair sideways after a burst — the one motion a player cannot
    // anticipate, because they did not put it there.
    const shots = controller([PATTERNED]);
    burst(shots, 3);
    const settle = shots.recoil(1);
    expect(settle.yaw).toBe(0);
    expect(settle.pitch).toBeLessThan(0);
  });
});


/**
 * Blocking the trigger while a grenade is in hand.
 *
 * The flag is cosmetic on the wire, deliberately. The obvious alternative —
 * `select`-ing some other slot — puts `weapon: n` on the next command, and
 * `_handle_combat` **cancels an in-flight reload** on a switch, so equipping a
 * grenade would silently abort a reload and cost a real switch delay.
 */
describe('setBlocked', () => {
  it('refuses to fire while something else is in hand', () => {
    const shots = controller();
    shots.setBlocked(true);
    shots.press();
    expect(shots.frame(0, 100, you()).fire).toBe(false);
  });

  it('refuses to scope, so the right button is free to be the toss', () => {
    // Without this a sniper would be left zoomed at 4x with a grenade up.
    // `SEMI` carries no `zoomLevels`, so the scope needs a weapon that has one.
    const shots = controller([spec({ id: 'sniper', zoomLevels: [2, 4] })]);
    shots.cycleScope();
    expect(shots.scoped).toBe(1);
    shots.setBlocked(true);
    shots.cycleScope();
    expect(shots.scoped).toBe(1);
  });

  it('still adopts the server’s word about what we are holding', () => {
    // Everything above the trigger check in `frame` is reconciliation, and it
    // has to land whether or not we can currently shoot — otherwise the HUD
    // describes the weapon we put away two seconds ago.
    const shots = controller();
    shots.setBlocked(true);
    shots.frame(0, 100, you({ weapon: 1, ammo: 3 }));
    expect(shots.slot).toBe(1);
    expect(shots.ammo).toBe(3);
  });

  it('fires again the moment the grenade is gone', () => {
    const shots = controller();
    shots.setBlocked(true);
    shots.press();
    shots.frame(0, 100, you());
    shots.setBlocked(false);
    shots.press();
    expect(shots.frame(1000, 100, you()).fire).toBe(true);
  });

  it('releases the trigger on the way in', () => {
    // A button left held while the grenade came up would fire on the frame the
    // weapon came back — a shot nobody asked for, at whatever you happened to be
    // looking at.
    const shots = controller();
    shots.press();
    shots.setBlocked(true);
    shots.setBlocked(false);
    expect(shots.frame(1000, 100, you()).fire).toBe(false);
  });
});
