/**
 * Building a conformance world from the fixture's rect description.
 *
 * Mirrored by `build_world` in the Python suite and `build_world` in the Rust
 * one — three copies, deliberately, because sharing it across languages would
 * mean a crate reaching into a TS package. Within *this* language one copy is
 * enough, which is why it lives here rather than in each test file: it was
 * already needed by `conformance.test.ts` and `arc.test.ts`, and a second
 * TypeScript copy would be drift for nothing.
 *
 * Everything starts SOLID, so a spec only has to describe the space it cares
 * about.
 */
import { World, LADDER_ENTITY, NO_WATER, SOLID, SPACE } from '../world';
import type { MapEntity, MapInfo } from '../api';

export const PLANES = [
  'type',
  'floor',
  'ceil',
  'wtex',
  'ftex',
  'ctex',
  'vdelta',
  'utex',
  'tag',
];

export interface Rect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  type?: number;
  floor?: number;
  ceil?: number;
  vdelta?: number;
}

export interface WorldSpec {
  ssize: number;
  rects: Rect[];
  waterlevel?: number;
  ladders?: { x: number; y: number; height: number }[];
}

/** Mirrored by `build_world` in the Python suite. Everything starts SOLID. */
export function buildWorld(spec: WorldSpec): World {
  const { ssize } = spec;
  const n = ssize * ssize;
  const buf = new ArrayBuffer(n * PLANES.length);
  const plane = (name: string) => {
    const off = PLANES.indexOf(name) * n;
    return name === 'floor' || name === 'ceil'
      ? new Int8Array(buf, off, n)
      : new Uint8Array(buf, off, n);
  };
  const type = plane('type');
  const floor = plane('floor');
  const ceil = plane('ceil');
  const vdelta = plane('vdelta');
  type.fill(SOLID);
  ceil.fill(16);

  for (const rect of spec.rects) {
    for (let y = rect.y0; y <= rect.y1; y++) {
      for (let x = rect.x0; x <= rect.x1; x++) {
        const i = y * ssize + x;
        type[i] = rect.type ?? SPACE;
        floor[i] = rect.floor ?? 0;
        ceil[i] = rect.ceil ?? 16;
        vdelta[i] = rect.vdelta ?? 0;
      }
    }
  }

  const ladderEntities: MapEntity[] = (spec.ladders ?? []).map((l) => ({
    type: LADDER_ENTITY,
    name: 'ladder',
    x: l.x,
    y: l.y,
    z: 0,
    yaw: null,
    attrs: [l.height, 0, 0, 0, 0, 0, 0],
  }));

  const info: MapInfo = {
    name: 'conformance',
    title: 'conformance',
    magic: 'ACMP',
    version: 10,
    sfactor: Math.log2(ssize),
    ssize,
    cubic_size: n,
    waterlevel: spec.waterlevel ?? NO_WATER,
    watercolor: [0, 0, 0, 0],
    maprevision: 1,
    ambient: 0,
    flags: 0,
    timestamp: 0,
    entity_count: ladderEntities.length,
    // Ladders go in as *entities*, so the World's own `laddersFrom` resolves
    // them exactly as it does for a real map — the derivation is part of what
    // these vectors pin, not a span handed to both sides.
    entities: ladderEntities,
    spawns: {},
    truncated: false,
    legacy_unscaled_attrs: false,
    plane_order: PLANES,
    items: [],
  };
  return new World(info, buf);
}
