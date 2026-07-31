/**
 * The training range.
 *
 * Nothing here is authoritative — in a match the server owns every one of these
 * decisions — so what these pin is that training behaves like the match does.
 * A trigger that means something different in training is a trigger training
 * cannot teach you, which was the whole reason the range exists.
 */
import { describe, expect, it } from 'vitest';

import type { MapEntity, MapInfo, WeaponSpec } from '../api';
import { TARGET_HP, TARGET_RESPAWN, TrainingRange } from '../training';
import { PLAYER_EYE_HEIGHT, SPACE, World } from '../world';

const PLANES = ['type', 'floor', 'ceil', 'wtex', 'ftex', 'ctex', 'vdelta', 'utex', 'tag'];

/** An open box with spawn entities where the caller asks for them. */
function world(ssize = 64, spawns: [number, number][] = []): World {
  const n = ssize * ssize;
  const buf = new ArrayBuffer(n * PLANES.length);
  const plane = (name: string) => {
    const off = PLANES.indexOf(name) * n;
    return name === 'floor' || name === 'ceil'
      ? new Int8Array(buf, off, n)
      : new Uint8Array(buf, off, n);
  };
  // Open throughout rather than walled: these tests are about bodies and ammo,
  // and a wall would only add a second reason for a shot to stop.
  plane('type').fill(SPACE);
  plane('ceil').fill(32);

  const entities: MapEntity[] = spawns.map(([x, y]) => ({
    type: 3,
    name: 'playerstart',
    x,
    y,
    z: 4,
    yaw: 0,
    attrs: [0, 0, 0, 0],
  }));
  const info = {
    name: 'range',
    title: 'range',
    magic: 'ACMP',
    version: 10,
    sfactor: Math.log2(ssize),
    ssize,
    cubic_size: n,
    waterlevel: -100,
    watercolor: [0, 0, 0, 0],
    maprevision: 1,
    ambient: 0,
    flags: 0,
    timestamp: 0,
    entity_count: entities.length,
    entities,
    spawns: {},
    truncated: false,
    legacy_unscaled_attrs: false,
    plane_order: PLANES,
  } as unknown as MapInfo;
  return new World(info, buf);
}

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
    spread: 0,
    pellets: 1,
    range: 200,
    auto: true,
    kickback: 0,
    zoomLevels: [],
    hipfireSpread: 0,
    ...over,
  };
  return over.hipfireSpread === undefined ? { ...weapon, hipfireSpread: weapon.spread } : weapon;
}

/** A range with one dummy dead ahead of (8, 8) at (24, 8). */
function range(weapons: WeaponSpec[] = [spec()]): TrainingRange {
  const r = new TrainingRange();
  r.setWeapons(weapons, 0);
  r.place(world(64, [[8, 8]].concat([[24, 8]]) as [number, number][]), 8.5, 8.5);
  return r;
}

/** Fire straight down +x from (8.5, 8.5), where `place` puts the dummy. */
function fireAhead(r: TrainingRange, w: World, scoped = 0) {
  return r.fire(w, 8.5, 8.5, 0, PLAYER_EYE_HEIGHT, 0, 0, scoped, () => 0.5);
}

describe('TrainingRange placement', () => {
  it('puts dummies on the map’s own spawn points', () => {
    const r = range();
    const rows = r.rows();
    expect(rows.length).toBe(1);
    // Spawn entities are placed at cell centres, like every other spawn.
    expect(rows[0].x).toBeCloseTo(24.5, 5);
    expect(rows[0].y).toBeCloseTo(8.5, 5);
  });

  it('does not put a dummy on the point the player is standing on', () => {
    // Both spawns are the player's own cell and one two cubes away — near enough
    // to be the spot we spawned on rather than a target across the room.
    const r = new TrainingRange();
    r.setWeapons([spec()], 0);
    r.place(world(64, [[8, 8]]), 8.5, 8.5);
    expect(r.rows()).toHaveLength(0);
    expect(r.populated).toBe(false);
  });

  it('renders dummies as ordinary bodies so the avatar pool draws them', () => {
    const row = range().rows()[0];
    expect(row.alive).toBe(true);
    expect(row.hp).toBe(TARGET_HP);
    expect(row.bot).toBe(true);
  });
});

describe('TrainingRange shooting', () => {
  it('hits a dummy dead ahead and takes its health down', () => {
    const w = world(64, [
      [8, 8],
      [24, 8],
    ]);
    const r = range();
    const shot = fireAhead(r, w);
    expect(shot).not.toBeNull();
    expect(shot!.hits).toHaveLength(1);
    expect(shot!.hits[0].killed).toBe(false);
    expect(r.rows()[0].hp).toBeLessThan(TARGET_HP);
  });

  it('spends a round, and stops firing when the magazine is empty', () => {
    const w = world(64, [
      [8, 8],
      [24, 8],
    ]);
    const r = range([spec({ mag: 2 })]);
    expect(r.selfState().ammo).toBe(2);
    fireAhead(r, w);
    expect(r.selfState().ammo).toBe(1);
    fireAhead(r, w);
    expect(r.selfState().ammo).toBe(0);
    // Out of rounds is a shot that does not happen, not a shot that misses.
    expect(fireAhead(r, w)).toBeNull();
  });

  it('drops a dummy and stands it back up after the respawn delay', () => {
    const w = world(64, [
      [8, 8],
      [24, 8],
    ]);
    // One shot, more damage than a dummy has.
    const r = range([spec({ damage: TARGET_HP + 10 })]);
    const shot = fireAhead(r, w);
    expect(shot!.hits[0].killed).toBe(true);
    expect(r.rows()[0].alive).toBe(false);
    // A downed dummy is not a target; the shot passes through where it was.
    expect(fireAhead(r, w)!.hits).toHaveLength(0);
    r.update(TARGET_RESPAWN + 0.1);
    expect(r.rows()[0].alive).toBe(true);
    expect(r.rows()[0].hp).toBe(TARGET_HP);
  });

  it('drains hitmarkers on read, so each is shown once', () => {
    const w = world(64, [
      [8, 8],
      [24, 8],
    ]);
    const r = range();
    fireAhead(r, w);
    expect(r.selfState().hits).toHaveLength(1);
    expect(r.selfState().hits).toHaveLength(0);
  });

  it('uses the tight cone scoped and the wide one from the hip', () => {
    // The same mechanic the server applies, so a scoped shot in training and a
    // scoped shot in a match are the same shot.
    const w = world(64, [
      [8, 8],
      [24, 8],
    ]);
    const sniper = spec({ id: 'sniper', spread: 0, hipfireSpread: 0.4, zoomLevels: [2, 4] });
    // `rand` at its extreme puts the pellet at the very edge of whatever cone is
    // in force, so the two are separable without depending on a seed.
    const edge = () => 0.999;
    const hip = new TrainingRange();
    hip.setWeapons([sniper], 0);
    hip.place(w, 8.5, 8.5);
    const scoped = new TrainingRange();
    scoped.setWeapons([sniper], 0);
    scoped.place(w, 8.5, 8.5);

    const hipShot = hip.fire(w, 8.5, 8.5, 0, PLAYER_EYE_HEIGHT, 0, 0, 0, edge)!;
    const scopedShot = scoped.fire(w, 8.5, 8.5, 0, PLAYER_EYE_HEIGHT, 0, 0, 1, edge)!;
    // A zero cone goes exactly where it was aimed; the wide one does not.
    expect(scopedShot.ends[0][1]).toBeCloseTo(8.5, 6);
    expect(Math.abs(hipShot.ends[0][1] - 8.5)).toBeGreaterThan(1);
  });
});

describe('TrainingRange reloads', () => {
  it('refills the magazine from the reserve after the reload time', () => {
    const w = world(64, [
      [8, 8],
      [24, 8],
    ]);
    const r = range([spec({ mag: 3, reserve: 9, reloadTime: 2 })]);
    fireAhead(r, w);
    fireAhead(r, w);
    expect(r.selfState().ammo).toBe(1);
    r.requestReload();
    expect(r.selfState().reloading).toBe(true);
    r.update(2.1);
    const self = r.selfState();
    expect(self.reloading).toBe(false);
    expect(self.ammo).toBe(3);
    expect(self.reserve).toBe(7);
  });

  it('keeps an unlimited reserve unlimited', () => {
    // `-1` is bottomless; decrementing it would turn the sidearm into a weapon
    // with four billion rounds, which is a different thing from unlimited.
    const w = world(64, [
      [8, 8],
      [24, 8],
    ]);
    const r = range([spec({ mag: 2, reserve: -1, reloadTime: 1 })]);
    fireAhead(r, w);
    r.requestReload();
    r.update(1.1);
    const self = r.selfState();
    expect(self.ammo).toBe(2);
    expect(self.reserve).toBe(-1);
  });

  it('does not start a reload on a full magazine', () => {
    const r = range([spec({ mag: 5, reserve: 20, reloadTime: 1 })]);
    r.requestReload();
    expect(r.selfState().reloading).toBe(false);
  });

  it('cancels a reload when the weapon changes', () => {
    // Otherwise the timer keeps running on a weapon you are no longer holding
    // and fills it while you are somewhere else entirely.
    const w = world(64, [
      [8, 8],
      [24, 8],
    ]);
    const r = range([spec({ mag: 2, reserve: 8, reloadTime: 2 }), spec({ id: 'pistol' })]);
    fireAhead(r, w);
    r.requestReload();
    expect(r.selfState().reloading).toBe(true);
    r.select(1);
    expect(r.selfState().reloading).toBe(false);
  });
});

describe('TrainingRange self state', () => {
  it('reports the shape a snapshot would have carried', () => {
    // This is the whole trick: `ShotController` and the HUD take the same path
    // online and offline because they are handed the same object either way.
    const self = range().selfState();
    expect(self.alive).toBe(true);
    expect(self.weapon).toBe(0);
    expect(self.mag).toBe(20);
    expect(self.ammo).toBe(20);
  });
});
