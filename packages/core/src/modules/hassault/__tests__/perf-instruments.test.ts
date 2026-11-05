import { describe, expect, it } from 'vitest';

import mapsIndex from '../../../../../../backend/modules/hassault/maps/hd_dust2.json';
import { BenchRun, benchMaps, benchPath, pathSeconds, percentile, poseAt } from '../bench';
import { NetStats, WINDOW_MS } from '../net-stats';
import { FrameStats } from '../perf-stats';

describe('NetStats', () => {
  it('measures a steady 60 Hz stream as 60 Hz with no jitter and no loss', () => {
    const s = new NetStats();
    for (let i = 0; i < 120; i++) s.onSnapshot(i, i * (1000 / 60), 5000 + i * (1000 / 60));
    s.sample(0, 0, 0);
    s.sample(60_000, 1_000, WINDOW_MS);
    const v = s.view();
    expect(v.snapshotHz).toBeCloseTo(60, 5);
    expect(v.serverTickHz).toBeCloseTo(60, 5);
    expect(v.jitterMs).toBeCloseTo(0, 5);
    expect(v.lossPct).toBe(0);
    expect(v.inBytesPerSec).toBe(60_000);
    expect(v.outBytesPerSec).toBe(1_000);
  });

  it('counts skipped ticks as loss over the window', () => {
    const s = new NetStats();
    s.sample(0, 0, 0);
    // Ticks 0..19 with 5 and 6 missing: 19 expected after the first, 2 lost.
    for (let i = 0; i < 20; i++) if (i !== 5 && i !== 6) s.onSnapshot(i, i * 16, i * 16);
    s.sample(0, 0, WINDOW_MS);
    expect(s.view().lossPct).toBeCloseTo((2 / 19) * 100, 5);
  });

  it('reads bunched arrivals as jitter but not as a tick-rate change', () => {
    const s = new NetStats();
    // Server stamps every 16.7 ms; arrivals alternate 6 and 27 ms apart.
    let arrival = 0;
    for (let i = 0; i < 200; i++) {
      arrival += i % 2 === 0 ? 6 : 27.33;
      s.onSnapshot(i, i * (1000 / 60), arrival);
    }
    const v = s.view();
    expect(v.serverTickHz).toBeCloseTo(60, 3);
    expect(v.jitterMs!).toBeGreaterThan(8);
    expect(v.jitterMs!).toBeLessThan(12);
  });

  it('treats a counter that went backwards as a new match, not loss', () => {
    const s = new NetStats();
    s.sample(0, 0, 0);
    for (let i = 500; i < 510; i++) s.onSnapshot(i, i * 16, i * 16);
    for (let i = 0; i < 10; i++) s.onSnapshot(i, 20_000 + i * 16, 20_000 + i * 16);
    s.sample(0, 0, WINDOW_MS);
    expect(s.view().lossPct).toBe(0);
  });

  it('reports nothing it has not measured', () => {
    const v = new NetStats().view();
    expect(Object.values(v).every((x) => x === null)).toBe(true);
  });
});

describe('FrameStats', () => {
  it('reports nearest-rank percentiles over the ring', () => {
    const f = new FrameStats(100);
    for (let i = 1; i <= 100; i++) f.push(i);
    expect(f.percentile(50)).toBe(50);
    expect(f.percentile(99)).toBe(99);
    expect(f.percentile(100)).toBe(100);
    expect(f.mean()).toBe(50.5);
  });

  it('forgets frames older than the ring', () => {
    const f = new FrameStats(4);
    for (const ms of [100, 100, 100, 100, 1, 2, 3, 4]) f.push(ms);
    expect(f.percentile(100)).toBe(4);
    expect(f.frames).toBe(4);
  });

  it('has no GPU numbers until some arrive', () => {
    const f = new FrameStats();
    expect(f.gpuPercentile(50)).toBeNull();
    f.pushGpu(3.5);
    expect(f.gpuPercentile(50)).toBe(3.5);
  });
});

describe('bench paths', () => {
  it('covers every shipped map', () => {
    expect(benchMaps().sort()).toEqual(
      [
        'hd_aim',
        'hd_assault',
        'hd_bank',
        'hd_dust2',
        'hd_facility',
        'hd_inferno',
        'hd_junkflea',
        'hd_mirage',
        'hd_nuke',
        'hd_office',
      ].sort(),
    );
  });

  it('turns on each site of a defuse map', () => {
    const labels = benchPath('hd_dust2')!.map((f) => f.label);
    const sites = (mapsIndex as { objectives: { sites: { id: string }[] } }).objectives.sites;
    for (const site of sites) expect(labels).toContain(`site ${site.id} turn`);
  });

  it('cuts between framings and spins within one', () => {
    const path = benchPath('hd_dust2')!;
    const first = poseAt(path, 0)!;
    expect(first.index).toBe(0);
    const turn = path.findIndex((f) => f.spin > 0);
    const start = path.slice(0, turn).reduce((s, f) => s + f.seconds, 0);
    const a = poseAt(path, start)!;
    const b = poseAt(path, start + 1)!;
    expect(b.yaw - a.yaw).toBeCloseTo((path[turn].spin * Math.PI) / 180, 6);
    expect(poseAt(path, pathSeconds(path) + 0.01)).toBeNull();
  });

  it('records nothing during warm-up and finishes at the end of the path', () => {
    const path = benchPath('hd_aim')!;
    const run = new BenchRun('hd_aim', path, 'high', 1);
    const dt = 1 / 100;
    let frames = 0;
    while (!run.done) {
      const pose = run.advance(dt);
      if (!pose) break;
      run.record({ frameMs: pose.index < 0 ? 999 : 10, cpuMs: 2, drawCalls: 50, triangles: 1000 });
      frames++;
    }
    const r = run.result();
    expect(frames).toBeGreaterThan(0);
    expect(r.max).toBe(10);
    expect(r.p99).toBe(10);
    expect(r.cpuP99).toBe(2);
    expect(r.framings.every((f) => f.frames > 0)).toBe(true);
    expect(r.seconds).toBeCloseTo(pathSeconds(path), 0);
  });

  it('percentile is nearest-rank', () => {
    expect(percentile([5, 1, 3, 2, 4], 50)).toBe(3);
    expect(percentile([], 99)).toBe(0);
  });
});
