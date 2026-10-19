/**
 * The training range's drills against `drill-vectors.json`, which
 * `scripts/gen_hassault_drill_vectors.py` writes from `drills.py`.
 *
 * Two claims. A served schedule replayed through this client's own `step` lands
 * where the server's simulation did, so a target moves the way the server
 * decided it would. And `summarise` scores a run exactly as the Python reference
 * does, so the results card means the same number in both clients.
 */
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import {
  DrillMover,
  summarise,
  type DrillEvent,
  type DrillSegment,
  type DrillStats,
} from '../training-drill';
import { TrainingRange, DRILL_COUNTDOWN } from '../training';
import type { DrillSpec } from '../training-drill';
import type { WeaponSpec } from '../api';
import { buildWorld, type WorldSpec } from './build-world';

interface Vectors {
  tickRate: number;
  tolerance: number;
  world: WorldSpec;
  replays: {
    name: string;
    start: { x: number; y: number; z: number; yaw: number };
    ticks: number;
    segments: DrillSegment[];
    samples: [number, number, number, number][];
  }[];
  stats: { name: string; seconds: number; events: DrillEvent[]; expect: DrillStats }[];
}

const vectors: Vectors = JSON.parse(
  readFileSync(new URL('./drill-vectors.json', import.meta.url), 'utf-8'),
);

describe('drill schedule replay', () => {
  const world = buildWorld(vectors.world);
  const digits = -Math.log10(vectors.tolerance);

  for (const replay of vectors.replays) {
    it(replay.name, () => {
      const mover = new DrillMover(
        { id: 't', ...replay.start, segments: replay.segments },
        1 / vectors.tickRate,
      );
      const samples = new Map(replay.samples.map((s) => [s[0], s]));
      for (let k = 0; k <= replay.ticks; k++) {
        const want = samples.get(k);
        if (want) {
          expect(mover.state.x).toBeCloseTo(want[1], digits);
          expect(mover.state.y).toBeCloseTo(want[2], digits);
          expect(mover.state.z).toBeCloseTo(want[3], digits);
        }
        if (k < replay.ticks) mover.advance(world);
      }
    });
  }
});

describe('drill scoring', () => {
  for (const testCase of vectors.stats) {
    it(testCase.name, () => {
      const got = summarise(testCase.events, testCase.seconds);
      for (const [key, want] of Object.entries(testCase.expect)) {
        const value = got[key as keyof DrillStats];
        if (want === null) expect(value).toBeNull();
        else expect(value as number).toBeCloseTo(want as number, 9);
      }
    });
  }
});

function rifle(): WeaponSpec {
  return {
    id: 'assault',
    name: 'Rifle',
    damage: 60,
    headMultiplier: 2,
    rpm: 600,
    interval: 0.1,
    mag: 30,
    reserve: 90,
    reloadTime: 2,
    spread: 0,
    pellets: 1,
    range: 200,
    auto: true,
    kickback: 0,
    zoomLevels: [],
    hipfireSpread: 0,
    spray: [],
    sprayReset: 0.35,
    residualSpread: 0,
    falloffStart: 200,
  } as WeaponSpec;
}

/** A one-target drill on the vectors' open room, standing still in front of x = 5. */
function standingDrill(overrides: Partial<DrillSpec> = {}): DrillSpec {
  const replay = vectors.replays.find((r) => r.name === 'static')!;
  return {
    id: 'test',
    name: 'Test',
    description: '',
    pattern: 'static',
    map: 'room',
    station: null,
    seconds: 2,
    hp: 100,
    headOnly: false,
    respawn: 0.5,
    tickRate: vectors.tickRate,
    ticks: 2 * vectors.tickRate,
    shooter: null,
    targets: [{ id: 't0', ...replay.start, segments: replay.segments }],
    ...overrides,
  };
}

describe('a drill on the range', () => {
  const world = buildWorld(vectors.world);

  function run(spec: DrillSpec) {
    const range = new TrainingRange();
    range.setWeapons([rifle()], 0);
    range.startDrill(spec, world);
    return range;
  }

  /** Fire level, from x = 5, straight at the target's chest. */
  function fireAt(range: TrainingRange, pitch = 0) {
    return range.fire(world, 5.5, 20.5, 0, 4.5, 0, pitch, 0, () => 0.5);
  }

  it('counts down, runs for its length, then scores', () => {
    const range = run(standingDrill());
    expect(range.drillStatus()?.phase).toBe('countdown');
    range.update(DRILL_COUNTDOWN + 0.01);
    expect(range.drillStatus()?.phase).toBe('running');
    fireAt(range);
    fireAt(range);
    for (let i = 0; i < 200; i++) range.update(1 / 60);
    const status = range.drillStatus()!;
    expect(status.phase).toBe('done');
    expect(status.stats?.shots).toBe(2);
    expect(status.stats?.kills).toBe(1);
    expect(status.stats?.medianReaction).toBeCloseTo(0, 6);
  });

  it('logs nothing during the countdown', () => {
    const range = run(standingDrill());
    fireAt(range);
    range.update(DRILL_COUNTDOWN + 2.5);
    for (let i = 0; i < 200; i++) range.update(1 / 60);
    expect(range.drillStatus()?.stats?.shots).toBe(0);
  });

  it('heads only: a body shot is a hit that does no damage', () => {
    const range = run(standingDrill({ headOnly: true }));
    range.update(DRILL_COUNTDOWN + 0.01);
    // The eye (4.5) is above the head line (5.2 - 0.85), so aim at the chest.
    const shot = fireAt(range, -0.1);
    expect(shot?.hits).toHaveLength(1);
    expect(shot?.hits[0].damage).toBe(0);
    expect(shot?.hits[0].head).toBe(false);
  });

  it('a hidden pop-up is neither drawn nor hittable', () => {
    const hidden: DrillSegment[] = [[0, 0, 0, 0, 0]];
    const replay = vectors.replays.find((r) => r.name === 'static')!;
    const range = run(
      standingDrill({ targets: [{ id: 't0', ...replay.start, segments: hidden }] }),
    );
    range.update(DRILL_COUNTDOWN + 0.01);
    range.update(1 / 60);
    expect(range.rows()).toHaveLength(0);
    expect(fireAt(range)?.hits).toHaveLength(0);
  });
});
