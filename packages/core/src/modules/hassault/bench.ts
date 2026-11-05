/**
 * The frame-time benchmark: fly a fixed camera path over a map and report
 * frame-time percentiles, GPU time and draw calls.
 *
 * The path is `bench-paths.json`, generated from the map JSONs by
 * `scripts/gen_hassault_bench_paths.py` and replayed by the native client's
 * `--bench <map>` too (`apps/native-fps/src/bench.rs`). One path for both is the
 * whole point: a number from one client is comparable with the other's only if
 * both looked at the same things for the same time.
 *
 * Each framing is held for `seconds`, turning at `spin` degrees per second from
 * its starting yaw. Framings cut rather than blend: a cut is the honest worst
 * case (everything in view changes at once), and a blend would make the cost
 * of a framing depend on its neighbours.
 *
 * Pure: the panel drives it from its own frame loop and owns the camera.
 */
import benchPaths from './bench-paths.json';

export interface BenchFraming {
  label: string;
  /** Map cubes: plan x, plan y, absolute eye height. */
  eye: [number, number, number];
  /** Degrees; 0 = +x, 90 = +y (the server's convention). */
  yaw: number;
  /** Degrees; positive looks up. */
  pitch: number;
  seconds: number;
  /** Degrees per second. */
  spin: number;
}

export interface BenchPose {
  x: number;
  y: number;
  z: number;
  /** Radians, the player's convention. */
  yaw: number;
  pitch: number;
  label: string;
  /** Index into the framings, or -1 during warm-up. */
  index: number;
}

export interface BenchSample {
  /** Interval since the previous frame: pacing, capped by the display in a browser. */
  frameMs: number;
  /** Main-thread time spent producing this frame: the cost a faster display would see. */
  cpuMs: number;
  drawCalls: number;
  triangles: number;
}

export interface FramingStats {
  label: string;
  frames: number;
  p50: number;
  p99: number;
}

export interface BenchResult {
  map: string;
  client: 'browser' | 'native';
  tier: string;
  /** The drawing buffer, in physical pixels. */
  width: number;
  height: number;
  frames: number;
  seconds: number;
  fps: number;
  p50: number;
  p95: number;
  p99: number;
  max: number;
  cpuP50: number;
  cpuP99: number;
  gpuP50: number | null;
  gpuP99: number | null;
  drawCallsMax: number;
  drawCallsMean: number;
  trianglesMax: number;
  framings: FramingStats[];
}

// Through `unknown`: JSON imports type `eye` as `number[]`, and the generator is
// what guarantees it is three long (`scripts/gen_hassault_bench_paths.py`).
const PATHS = benchPaths as unknown as {
  warmupSeconds: number;
  maps: Record<string, BenchFraming[]>;
};

export const WARMUP_SECONDS = PATHS.warmupSeconds;

export function benchMaps(): string[] {
  return Object.keys(PATHS.maps);
}

export function benchPath(map: string): BenchFraming[] | null {
  return PATHS.maps[map] ?? null;
}

export function pathSeconds(framings: readonly BenchFraming[]): number {
  return framings.reduce((sum, f) => sum + f.seconds, 0);
}

/** Where the camera is `t` seconds into the path (after warm-up), or `null` past the end. */
export function poseAt(framings: readonly BenchFraming[], t: number): BenchPose | null {
  let start = 0;
  for (let i = 0; i < framings.length; i++) {
    const f = framings[i];
    if (t < start + f.seconds) {
      const local = t - start;
      return {
        x: f.eye[0],
        y: f.eye[1],
        z: f.eye[2],
        yaw: ((f.yaw + f.spin * local) * Math.PI) / 180,
        pitch: (f.pitch * Math.PI) / 180,
        label: f.label,
        index: i,
      };
    }
    start += f.seconds;
  }
  return null;
}

/** Nearest-rank percentile of an unsorted list. */
export function percentile(values: readonly number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const rank = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[rank];
}

const round2 = (v: number) => Math.round(v * 100) / 100;

/**
 * One benchmark run. `advance(dt)` moves the clock and says where the camera
 * goes this frame; `record` is fed the frame that was just drawn. The warm-up
 * (the first framing held still) is drawn but not recorded: it absorbs shader
 * compiles and texture uploads, which are a load cost, not a frame cost.
 */
export class BenchRun {
  private elapsed = 0;
  private readonly samples: BenchSample[] = [];
  private readonly byFraming: number[][];
  private readonly gpu: number[] = [];
  private current = -1;
  /** The drawing buffer the frames were drawn at; the caller keeps it current. */
  size: [number, number] = [0, 0];

  constructor(
    readonly map: string,
    readonly framings: readonly BenchFraming[],
    readonly tier: string,
    readonly warmup = WARMUP_SECONDS,
  ) {
    this.byFraming = framings.map(() => []);
  }

  /** The pose for this frame, or `null` once the path is over. */
  advance(dt: number): BenchPose | null {
    this.elapsed += dt;
    if (this.elapsed < this.warmup) {
      const first = poseAt(this.framings, 0);
      this.current = -1;
      return first ? { ...first, index: -1 } : null;
    }
    const pose = poseAt(this.framings, this.elapsed - this.warmup);
    this.current = pose ? pose.index : -1;
    return pose;
  }

  record(sample: BenchSample): void {
    if (this.current < 0) return;
    this.samples.push(sample);
    this.byFraming[this.current].push(sample.frameMs);
  }

  recordGpu(gpuMs: number): void {
    if (this.current >= 0) this.gpu.push(gpuMs);
  }

  get done(): boolean {
    return this.elapsed >= this.warmup + pathSeconds(this.framings);
  }

  result(): BenchResult {
    const ms = this.samples.map((s) => s.frameMs);
    const seconds = ms.reduce((a, b) => a + b, 0) / 1000;
    const calls = this.samples.map((s) => s.drawCalls);
    const cpu = this.samples.map((s) => s.cpuMs);
    return {
      map: this.map,
      client: 'browser',
      tier: this.tier,
      width: this.size[0],
      height: this.size[1],
      frames: ms.length,
      seconds: round2(seconds),
      fps: seconds > 0 ? round2(ms.length / seconds) : 0,
      p50: round2(percentile(ms, 50)),
      p95: round2(percentile(ms, 95)),
      p99: round2(percentile(ms, 99)),
      max: round2(ms.length ? Math.max(...ms) : 0),
      cpuP50: round2(percentile(cpu, 50)),
      cpuP99: round2(percentile(cpu, 99)),
      gpuP50: this.gpu.length ? round2(percentile(this.gpu, 50)) : null,
      gpuP99: this.gpu.length ? round2(percentile(this.gpu, 99)) : null,
      drawCallsMax: calls.length ? Math.max(...calls) : 0,
      drawCallsMean: calls.length ? round2(calls.reduce((a, b) => a + b, 0) / calls.length) : 0,
      trianglesMax: this.samples.reduce((m, s) => Math.max(m, s.triangles), 0),
      framings: this.framings.map((f, i) => ({
        label: f.label,
        frames: this.byFraming[i].length,
        p50: round2(percentile(this.byFraming[i], 50)),
        p99: round2(percentile(this.byFraming[i], 99)),
      })),
    };
  }
}
