/**
 * Frame-time and GPU-time measurement for the browser client.
 *
 * Average fps hides exactly what players feel: one 40 ms frame in a second of
 * 5 ms frames still reads as "180 fps". So frames go into a ring and are
 * reported as percentiles; p99 is the number the 144 Hz target is held to.
 *
 * GPU time comes from `EXT_disjoint_timer_query_webgl2` where the browser
 * exposes it (Chrome on desktop does; Safari and most mobiles do not). Queries
 * are read back a few frames late, which is how timer queries work, and a
 * "disjoint" frame — the GPU was preempted, clocks changed — is thrown away
 * rather than averaged in. Without the extension the GPU column is `null`, never
 * a guess.
 */

import type { NetStatsView } from './net-stats';

/** Frames kept: about four seconds at 144 Hz. */
export const FRAME_RING = 600;

/**
 * What the pane exposes to its overlays. The NetGraph polls this a couple of
 * times a second instead of the pane pushing numbers through React state —
 * pushing would re-render a 6000-line component at display rate to update a
 * dozen characters.
 */
export interface PerfProbe {
  /** Interval between frames, ms: pacing. */
  readonly frames: FrameStats;
  /** Main-thread time spent producing each frame, ms: cost. */
  readonly cpu: FrameStats;
  /** Whether GPU timer queries are available on this context. */
  readonly gpuTimed: boolean;
  /** Draw calls and triangles in the last frame, across every pass. */
  drawCalls(): number;
  triangles(): number;
  /** The match's network numbers, or `null` offline. */
  net(): NetStatsView | null;
  /** How far behind the newest snapshot remote players are drawn, ms. */
  interpDelayMs(): number;
}

export class FrameStats {
  private readonly ring: Float64Array;
  private readonly gpu: Float64Array;
  private readonly scratch: Float64Array;
  private count = 0;
  private gpuCount = 0;
  private head = 0;
  private gpuHead = 0;

  constructor(capacity = FRAME_RING) {
    this.ring = new Float64Array(capacity);
    this.gpu = new Float64Array(capacity);
    this.scratch = new Float64Array(capacity);
  }

  push(frameMs: number): void {
    this.ring[this.head] = frameMs;
    this.head = (this.head + 1) % this.ring.length;
    this.count = Math.min(this.count + 1, this.ring.length);
  }

  pushGpu(gpuMs: number): void {
    this.gpu[this.gpuHead] = gpuMs;
    this.gpuHead = (this.gpuHead + 1) % this.gpu.length;
    this.gpuCount = Math.min(this.gpuCount + 1, this.gpu.length);
  }

  /** `p` in [0, 100], nearest-rank. `null` with nothing recorded. */
  percentile(p: number): number | null {
    return percentileOf(this.ring, this.count, p, this.scratch);
  }

  gpuPercentile(p: number): number | null {
    return percentileOf(this.gpu, this.gpuCount, p, this.scratch);
  }

  mean(): number | null {
    if (this.count === 0) return null;
    let sum = 0;
    for (let i = 0; i < this.count; i++) sum += this.ring[i];
    return sum / this.count;
  }

  get frames(): number {
    return this.count;
  }

  clear(): void {
    this.count = 0;
    this.gpuCount = 0;
    this.head = 0;
    this.gpuHead = 0;
  }
}

function percentileOf(
  ring: Float64Array,
  count: number,
  p: number,
  scratch: Float64Array,
): number | null {
  if (count === 0) return null;
  const view = scratch.subarray(0, count);
  view.set(ring.subarray(0, count));
  view.sort();
  const rank = Math.min(count - 1, Math.max(0, Math.ceil((p / 100) * count) - 1));
  return view[rank];
}

/** The subset of `EXT_disjoint_timer_query_webgl2` used here. */
interface TimerExt {
  TIME_ELAPSED_EXT: number;
  GPU_DISJOINT_EXT: number;
}

/**
 * One `TIME_ELAPSED` query per frame, read back when ready.
 *
 * `begin()` before the frame's draws and `end()` after them; `poll()` drains
 * whatever has finished into `onResult`. At most `MAX_IN_FLIGHT` queries are
 * outstanding — a GPU that falls further behind than that skips measuring a
 * frame rather than growing a queue.
 */
export class GpuTimer {
  static readonly MAX_IN_FLIGHT = 4;
  private readonly pending: WebGLQuery[] = [];
  private active: WebGLQuery | null = null;

  private constructor(
    private readonly gl: WebGL2RenderingContext,
    private readonly ext: TimerExt,
  ) {}

  static create(gl: WebGLRenderingContext | WebGL2RenderingContext): GpuTimer | null {
    if (typeof WebGL2RenderingContext === 'undefined' || !(gl instanceof WebGL2RenderingContext)) {
      return null;
    }
    const ext = gl.getExtension('EXT_disjoint_timer_query_webgl2') as TimerExt | null;
    return ext ? new GpuTimer(gl, ext) : null;
  }

  begin(): void {
    if (this.active || this.pending.length >= GpuTimer.MAX_IN_FLIGHT) return;
    const query = this.gl.createQuery();
    if (!query) return;
    this.gl.beginQuery(this.ext.TIME_ELAPSED_EXT, query);
    this.active = query;
  }

  end(): void {
    if (!this.active) return;
    this.gl.endQuery(this.ext.TIME_ELAPSED_EXT);
    this.pending.push(this.active);
    this.active = null;
  }

  poll(onResult: (gpuMs: number) => void): void {
    const gl = this.gl;
    const disjoint = Boolean(gl.getParameter(this.ext.GPU_DISJOINT_EXT));
    while (this.pending.length > 0) {
      const query = this.pending[0];
      if (!gl.getQueryParameter(query, gl.QUERY_RESULT_AVAILABLE)) break;
      this.pending.shift();
      const ns = gl.getQueryParameter(query, gl.QUERY_RESULT) as number;
      gl.deleteQuery(query);
      if (!disjoint) onResult(ns / 1e6);
    }
  }

  dispose(): void {
    if (this.active) {
      this.gl.endQuery(this.ext.TIME_ELAPSED_EXT);
      this.gl.deleteQuery(this.active);
      this.active = null;
    }
    for (const query of this.pending) this.gl.deleteQuery(query);
    this.pending.length = 0;
  }
}
