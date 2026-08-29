/**
 * The client half of items: what the training range gives and what it refuses to.
 *
 * The range is the only place the client decides a pickup at all — in a match
 * the server owns every one of these calls and this file is not consulted — so
 * what these pin is that Train behaves the way a match does, the same contract
 * `training.test.ts` has for the trigger.
 *
 * `ItemPool` is not tested here: it needs a three scene and everything it does
 * is drawing.
 */
import { describe, expect, it } from 'vitest';

import type { ItemReach, ItemSpec, MapEntity, MapInfo, WeaponSpec } from '../api';
import type { ItemRow } from '../net';
import { TrainingRange } from '../training';
import { PLAYER_EYE_HEIGHT, SPACE, World } from '../world';

const PLANES = ['type', 'floor', 'ceil', 'wtex', 'ftex', 'ctex', 'vdelta', 'utex', 'tag'];

/** An open box, so a shot has nothing to stop against but its own range. */
function world(ssize = 64): World {
  const n = ssize * ssize;
  const buf = new ArrayBuffer(n * PLANES.length);
  const plane = (name: string) => {
    const off = PLANES.indexOf(name) * n;
    return name === 'floor' || name === 'ceil'
      ? new Int8Array(buf, off, n)
      : new Uint8Array(buf, off, n);
  };
  plane('type').fill(SPACE);
  plane('ceil').fill(32);
  const entities: MapEntity[] = [];
  return new World(
    {
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
      entity_count: 0,
      entities,
      spawns: {},
      truncated: false,
      legacy_unscaled_attrs: false,
      plane_order: PLANES,
      items: [],
    } as unknown as MapInfo,
    buf,
  );
}

/**
 * Spend rounds the only way the range lets you: by firing them.
 *
 * Worth doing the long way rather than reaching into the reserve, because "an
 * ammo pickup does nothing until you have actually spent something" is the
 * behaviour under test, not an obstacle to it.
 */
function spend(range: TrainingRange, shots: number): void {
  const w = world();
  for (let i = 0; i < shots; i += 1) {
    range.fire(w, 8.5, 8.5, 0, PLAYER_EYE_HEIGHT, 0, 0, 0, () => 0.5);
    range.requestReload();
    range.update(10);
  }
}

const REACH: ItemReach = { radius: 1.8, below: 1.25, above: 4.5 };

function spec(kind: string, over: Partial<ItemSpec> = {}): ItemSpec {
  return {
    kind,
    name: kind,
    respawn: 20,
    health: 0,
    armour: 0,
    armourCap: 0,
    mags: 0,
    nade: null,
    ...over,
  };
}

const KINDS: ItemSpec[] = [
  spec('ammo', { mags: 2 }),
  spec('health', { health: 25 }),
  spec('armour', { armour: 50, armourCap: 100, respawn: 40 }),
];

function weapon(id: string, over: Partial<WeaponSpec> = {}): WeaponSpec {
  return {
    id,
    name: id,
    damage: 30,
    headMultiplier: 2,
    rpm: 600,
    interval: 0.1,
    mag: 20,
    reserve: 60,
    reloadTime: 2,
    spread: 0.01,
    pellets: 1,
    range: 200,
    auto: true,
    kickback: 0,
    zoomLevels: [],
    hipfireSpread: 0.01,
    ...over,
  } as WeaponSpec;
}

function rangeWith(rows: ItemRow[]): TrainingRange {
  const range = new TrainingRange();
  range.setWeapons([weapon('pistol', { reserve: -1 }), weapon('rifle')], 1);
  range.placeItems(rows, KINDS, REACH);
  return range;
}

const AMMO_AT = (x: number, y: number, kind = 'ammo'): ItemRow => ({
  id: 1,
  kind,
  x,
  y,
  z: 0,
});

describe('the training range and items', () => {
  it('draws every item but only ever gives you the ammunition', () => {
    const range = rangeWith([
      AMMO_AT(10, 10),
      { id: 2, kind: 'health', x: 12, y: 10, z: 0 },
      { id: 3, kind: 'armour', x: 14, y: 10, z: 0 },
    ]);
    // All three are on the map: the layout is a real thing to learn, and a range
    // missing two thirds of it would teach a map that does not exist.
    expect(range.placements()).toHaveLength(3);

    // Standing on the health pack takes nothing — nothing shoots back here.
    range.collect(12, 10, 0);
    range.collect(14, 10, 0);
    expect(range.takenIds()).toEqual([]);
  });

  it('refills every finite reserve and leaves the bottomless one alone', () => {
    const range = rangeWith([AMMO_AT(10, 10)]);
    spend(range, 4);
    const before = range.selfState().reserve;
    expect(before).toBeLessThan(60);

    range.collect(10, 10, 0);
    expect(range.selfState().reserve).toBeGreaterThan(before);

    // And the pistol, whose reserve is unlimited, is still unlimited rather than
    // having become a very large number.
    range.select(0);
    expect(range.selfState().reserve).toBe(-1);
  });

  it('does not consume an item that can give nothing', () => {
    const range = rangeWith([AMMO_AT(10, 10)]);
    range.collect(10, 10, 0); // full reserves
    expect(range.takenIds()).toEqual([]);
  });

  it('respects the served reach rather than a radius of its own', () => {
    const range = rangeWith([AMMO_AT(10, 10)]);
    spend(range, 3);

    range.collect(10 + REACH.radius + 0.1, 10, 0);
    expect(range.takenIds()).toEqual([]);
    range.collect(10 + REACH.radius - 0.1, 10, 0);
    expect(range.takenIds()).toEqual([1]);
  });

  it('brings an item back rather than removing it', () => {
    const range = rangeWith([AMMO_AT(10, 10)]);
    spend(range, 3);
    range.collect(10, 10, 0);
    expect(range.takenIds()).toEqual([1]);

    range.update(19);
    expect(range.takenIds()).toEqual([1]);
    range.update(2);
    expect(range.takenIds()).toEqual([]);
  });

  it('reports what a pickup gave, once', () => {
    const range = rangeWith([AMMO_AT(10, 10)]);
    spend(range, 3);
    range.collect(10, 10, 0);

    const picked = range.selfState().picked ?? [];
    expect(picked).toHaveLength(1);
    expect(picked[0].rounds).toBeGreaterThan(0);
    // Drained, like hitmarkers: a pickup is announced once, not every frame
    // until something else happens.
    expect(range.selfState().picked).toEqual([]);
  });

  it('keeps the items placed across a reset but restocks the player', () => {
    const range = rangeWith([AMMO_AT(10, 10)]);
    spend(range, 3);
    range.collect(10, 10, 0);

    range.reset();
    expect(range.placements()).toHaveLength(1);
    // Back on the floor: a reset is about you, and leaving an item down would
    // hide the cycle the range exists to let you learn.
    expect(range.takenIds()).toEqual([]);
  });
});
