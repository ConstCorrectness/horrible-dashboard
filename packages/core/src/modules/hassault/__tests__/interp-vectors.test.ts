/**
 * `interp-vectors.json`: the curve a remote body is drawn along and a shot is
 * judged on, and the render clock that decides when it is drawn.
 *
 * `rewinds` come from the server's own `PositionHistory.rewind`, so passing
 * them means this client draws a body exactly where the server will rewind it
 * to. `extrapolations` and `interpClock` come from the reference `InterpClock`
 * in `scripts/gen_hassault_vectors.py`, which `interp.rs` replays too.
 */
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { INTERP, InterpClock, SnapshotBuffer, type PlayerRow, type Snapshot } from '../net';

type Vec3 = [number, number, number];
interface Frame {
  t: number;
  positions: Record<string, Vec3>;
}
interface Query {
  t: number;
  expect: Record<string, Vec3>;
}
interface ClockCase {
  name: string;
  pushes: Array<[number, number]>;
  probes: Array<{ after: number; at: number; renderTime: number; delay: number }>;
}
interface Vectors {
  interp: Record<string, number>;
  clockTolerance: number;
  positionTolerance: number;
  rewinds: { frames: Frame[]; queries: Query[] };
  extrapolations: { frames: Frame[]; queries: Query[] };
  interpClock: ClockCase[];
}

const vectors = JSON.parse(
  readFileSync(new URL('./interp-vectors.json', import.meta.url), 'utf-8'),
) as Vectors;

function row(id: string, [x, y, z]: Vec3): PlayerRow {
  return {
    id,
    name: id,
    team: 0,
    x,
    y,
    z,
    yaw: 0,
    pitch: 0,
    ground: true,
    stale: false,
    rtt: 0,
    hp: 100,
    alive: true,
    weapon: 2,
    kills: 0,
    deaths: 0,
    bot: false,
    crouch: 0,
  };
}

function buffered(frames: Frame[]): SnapshotBuffer {
  const buffer = new SnapshotBuffer();
  for (const frame of frames) {
    const players = Object.entries(frame.positions).map(([id, at]) => row(id, at));
    const snapshot: Snapshot = { room: 'r', tick: 0, t: frame.t, ack: 0, players };
    // Arrival time is irrelevant here: these replay `sampleAt`, not the clock.
    buffer.push(snapshot, frame.t);
  }
  return buffer;
}

function expectPositions(got: PlayerRow[], expected: Record<string, Vec3>, label: string) {
  const byId = new Map(got.map((r) => [r.id, r]));
  for (const [id, [x, y, z]] of Object.entries(expected)) {
    const r = byId.get(id);
    expect(r, `${label} ${id}`).toBeDefined();
    const tol = vectors.positionTolerance;
    expect(Math.abs(r!.x - x), `${label} ${id} x`).toBeLessThan(tol);
    expect(Math.abs(r!.y - y), `${label} ${id} y`).toBeLessThan(tol);
    expect(Math.abs(r!.z - z), `${label} ${id} z`).toBeLessThan(tol);
  }
}

describe('interp-vectors.json', () => {
  it('pins the same constants this client runs on', () => {
    expect({ ...INTERP }).toEqual(vectors.interp);
  });

  it("draws bodies on the server's rewind curve", () => {
    const buffer = buffered(vectors.rewinds.frames);
    for (const q of vectors.rewinds.queries) {
      // Past the newest frame the server holds and the client extrapolates;
      // the generator writes no such query, and this would catch one.
      expectPositions(buffer.sampleAt(q.t, 'nobody'), q.expect, `t=${q.t}`);
    }
  });

  it('extrapolates a starved body the way the reference does', () => {
    const buffer = buffered(vectors.extrapolations.frames);
    for (const q of vectors.extrapolations.queries) {
      expectPositions(buffer.sampleAt(q.t, 'nobody'), q.expect, `t=${q.t}`);
    }
  });

  for (const c of vectors.interpClock) {
    it(`runs the render clock: ${c.name}`, () => {
      const clock = new InterpClock();
      let next = 0;
      c.pushes.forEach(([t, local], i) => {
        clock.push(t, local);
        while (next < c.probes.length && c.probes[next].after === i) {
          const probe = c.probes[next++];
          const tol = vectors.clockTolerance;
          expect(Math.abs(clock.renderTime(probe.at)! - probe.renderTime)).toBeLessThan(tol);
          expect(Math.abs(clock.delay(probe.at)! - probe.delay)).toBeLessThan(tol);
        }
      });
      expect(next).toBe(c.probes.length);
    });
  }
});
