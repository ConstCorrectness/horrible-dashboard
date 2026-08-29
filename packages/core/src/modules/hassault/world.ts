/**
 * The cube world: the byte planes adopted as typed arrays, plus the height rules
 * the renderer and the collision code both read.
 *
 * Cube 1 worlds are a flat grid of columns — each cell has a type, a floor and a
 * ceiling height, texture ids, and a vertex delta. Both consumers must agree on
 * exactly how a heightfield resolves to a height, so those rules live here once
 * rather than being reimplemented on each side.
 *
 * Coordinates: the engine's grid is `(x, y)` with `z` as height. Three.js is
 * y-up, so world space maps as `three.x = cube.x`, `three.y = height`,
 * `three.z = cube.y`. One cube is one world unit.
 */
import type { MapEntity, MapInfo } from './api';

// Cube types — the on-disk encoding, from world.h.
export const SOLID = 0;
export const CORNER = 1;
export const FHF = 2; // floor heightfield
export const CHF = 3; // ceiling heightfield
export const SPACE = 4;
export const SEMISOLID = 5;

/** Two cubes from the edge of the world are always solid (`MINBORD` in world.h). */
export const MINBORD = 2;

/** Player dimensions, from AssaultCube's `entity.h` defaults. */
export const PLAYER_RADIUS = 1.1;
export const PLAYER_EYE_HEIGHT = 4.5;
export const PLAYER_ABOVE_EYE = 0.7;

/** `cgz.ENTITY_NAMES` index of a `ladder`, named so the one reader says what it reads. */
export const LADDER_ENTITY = 12;

/**
 * Water plane for a map that has none. Far below any floor a `.cgz` can hold
 * (`floor` is a signed byte), so "is this body in water" is one comparison with
 * no special case for the absence of water.
 */
export const NO_WATER = -1e9;

/**
 * One climbable volume, resolved against the floor beneath it.
 *
 * Derived rather than served, because the entity carries a *height* and the
 * simulation needs a span whose base is the floor of the cell. Mirrors `Ladder`
 * in `physics.py`, and the derivation is pinned by the conformance vectors: the
 * two sides must agree on where a ladder starts and ends or a climb desyncs on
 * the first frame.
 */
export interface Ladder {
  x: number;
  y: number;
  base: number;
  top: number;
}

/**
 * Resolve every `ladder` entity into a span. Mirrors `ladders_from`.
 *
 * A height of zero is **dropped**, not treated as unbounded: a mapper who never
 * set the attribute meant "I did not finish this", and a ladder of infinite
 * height in the middle of a room would be a hole in the map's physics.
 */
export function laddersFrom(
  ssize: number,
  floorAt: (x: number, y: number) => number,
  entities: MapEntity[],
): Ladder[] {
  const out: Ladder[] = [];
  for (const entity of entities) {
    if (entity.type !== LADDER_ENTITY) continue;
    const height = entity.attrs?.[0] ?? 0;
    if (height <= 0) continue;
    const x = Math.trunc(entity.x);
    const y = Math.trunc(entity.y);
    if (x < 0 || y < 0 || x >= ssize || y >= ssize) continue;
    const base = floorAt(x, y);
    out.push({ x: x + 0.5, y: y + 0.5, base, top: base + height });
  }
  return out;
}

export class World {
  readonly info: MapInfo;
  readonly ssize: number;
  readonly type: Uint8Array;
  /** Signed: a floor can sit below zero. */
  readonly floor: Int8Array;
  readonly ceil: Int8Array;
  readonly wtex: Uint8Array;
  readonly ftex: Uint8Array;
  readonly ctex: Uint8Array;
  readonly vdelta: Uint8Array;
  readonly utex: Uint8Array;
  readonly tag: Uint8Array;
  /** The map's water plane, or `NO_WATER` when it has none. */
  readonly waterlevel: number;
  /** Climbable spans, from the map's `ladder` entities. */
  readonly ladders: Ladder[];

  constructor(info: MapInfo, cubes: ArrayBuffer) {
    this.info = info;
    this.ssize = info.ssize;
    const n = info.cubic_size;
    const expected = n * info.plane_order.length;
    if (cubes.byteLength < expected) {
      throw new Error(`cube payload is ${cubes.byteLength} bytes, expected ${expected}`);
    }
    // Slice by the order the backend reports rather than a hardcoded list, so the
    // two sides cannot drift.
    const at = (plane: string) => info.plane_order.indexOf(plane) * n;
    this.type = new Uint8Array(cubes, at('type'), n);
    this.floor = new Int8Array(cubes, at('floor'), n);
    this.ceil = new Int8Array(cubes, at('ceil'), n);
    this.wtex = new Uint8Array(cubes, at('wtex'), n);
    this.ftex = new Uint8Array(cubes, at('ftex'), n);
    this.ctex = new Uint8Array(cubes, at('ctex'), n);
    this.vdelta = new Uint8Array(cubes, at('vdelta'), n);
    this.utex = new Uint8Array(cubes, at('utex'), n);
    this.tag = new Uint8Array(cubes, at('tag'), n);
    // A map with no water stores a level far below its floors already, so this
    // needs no sentinel of its own — but a `MapInfo` from an older backend has
    // no field at all, and reading that as zero would flood the map to the
    // height of an ordinary floor.
    this.waterlevel = Number.isFinite(info.waterlevel) ? info.waterlevel : NO_WATER;
    // Derived here rather than in the caller: `floorAt` is a method of the world
    // being constructed, and a ladder's base is the floor of its cell.
    this.ladders = laddersFrom(this.ssize, (x, y) => this.floorAt(x, y), info.entities ?? []);
  }

  /** Flat index of a cell, matching the engine's `SWS(w,x,y,s)` macro. */
  index(x: number, y: number): number {
    return y * this.ssize + x;
  }

  inBounds(x: number, y: number): boolean {
    return x >= 0 && y >= 0 && x < this.ssize && y < this.ssize;
  }

  /**
   * Whether a cell blocks movement and hides its neighbours' faces.
   *
   * Out of bounds counts as solid: the engine guarantees a solid border, and
   * treating the outside as open would let a player walk off the map.
   */
  isSolid(x: number, y: number): boolean {
    if (!this.inBounds(x, y)) return true;
    const t = this.type[this.index(x, y)];
    return t === SOLID || t === SEMISOLID;
  }

  typeAt(x: number, y: number): number {
    return this.inBounds(x, y) ? this.type[this.index(x, y)] : SOLID;
  }

  /** The raw vertex delta stored at a grid vertex (clamped at the far edge). */
  vdeltaAt(vx: number, vy: number): number {
    const cx = Math.min(Math.max(vx, 0), this.ssize - 1);
    const cy = Math.min(Math.max(vy, 0), this.ssize - 1);
    return this.vdelta[this.index(cx, cy)];
  }

  /**
   * The floor height of cell `(x, y)` at one of its corners.
   *
   * The split matters and is easy to get backwards: the **base height comes from
   * the cell**, while the **delta comes from the cell owning that corner vertex**.
   * `physics.cpp:287` shows both halves — it starts from `s->floor` and subtracts
   * an average of the four corner cells' deltas. Taking the base from the corner
   * cell instead tears adjacent heightfields apart at every seam.
   *
   * A non-heightfield cell ignores deltas entirely, so flat and sloped cells can
   * share one code path.
   */
  cornerFloor(x: number, y: number, vx: number, vy: number): number {
    if (!this.inBounds(x, y)) return 0;
    const i = this.index(x, y);
    const base = this.floor[i];
    return this.type[i] === FHF ? base - this.vdeltaAt(vx, vy) / 4 : base;
  }

  cornerCeil(x: number, y: number, vx: number, vy: number): number {
    if (!this.inBounds(x, y)) return 0;
    const i = this.index(x, y);
    const base = this.ceil[i];
    return this.type[i] === CHF ? base + this.vdeltaAt(vx, vy) / 4 : base;
  }

  /**
   * The floor height a body standing in this cell rests on.
   *
   * Averaged across the cell's four corners — `(sum of four vdeltas) / 16`, which
   * is physics.cpp line 287. Note this is the average of four `vdelta/4` terms,
   * not a different constant; using `/4` here would sink the player into slopes.
   */
  floorAt(x: number, y: number): number {
    if (!this.inBounds(x, y)) return 0;
    const i = this.index(x, y);
    let f = this.floor[i];
    if (this.type[i] === FHF) f -= this.cornerDeltaSum(x, y) / 16;
    return f;
  }

  ceilAt(x: number, y: number): number {
    if (!this.inBounds(x, y)) return 0;
    const i = this.index(x, y);
    let c = this.ceil[i];
    if (this.type[i] === CHF) c += this.cornerDeltaSum(x, y) / 16;
    return c;
  }

  private cornerDeltaSum(x: number, y: number): number {
    const d = (cx: number, cy: number) =>
      this.inBounds(cx, cy) ? this.vdelta[this.index(cx, cy)] : 0;
    return d(x, y) + d(x + 1, y) + d(x, y + 1) + d(x + 1, y + 1);
  }

  /** Cells a body of `radius` at `(x, y)` overlaps, as inclusive grid bounds. */
  cellsInRadius(x: number, y: number, radius: number) {
    return {
      x0: Math.floor(x - radius),
      x1: Math.floor(x + radius),
      y0: Math.floor(y - radius),
      y1: Math.floor(y + radius),
    };
  }

  /** Player spawn points, optionally for one team (attr2: 0 CLA, 1 RVSF). */
  spawns(team?: number) {
    const all = this.info.entities.filter((e) => e.name === 'playerstart');
    return team === undefined ? all : all.filter((e) => e.attrs[1] === team);
  }
}
