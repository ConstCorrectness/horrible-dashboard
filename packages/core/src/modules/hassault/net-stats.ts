/**
 * What the NetGraph's second and third rows actually measure.
 *
 * Those rows used to be string literals — `tick: 20.0`, `in: 14.2 KB/s`,
 * `jitter: 0.8ms` — which survived the server going to 60 Hz without changing a
 * digit. A readout that cannot move is worse than none: it is the number people
 * quote when they report that the netcode is fine. Everything here is derived
 * from what arrives:
 *
 * - **Snapshot rate** — arrivals per second, measured on our clock.
 * - **Server tick rate** — ticks per second of *server* time, from the
 *   snapshot's own `tick` counter and `t` stamp. The two differ exactly when the
 *   connection is bunching packets, which is the point of showing both.
 * - **Jitter** — RFC 3550's interarrival jitter: a smoothed mean of how far each
 *   gap between arrivals differs from the gap between the server's stamps. Pure
 *   transit variation; it does not care what the tick rate is.
 * - **Loss** — ticks the counter skipped, over the last whole window. TCP never
 *   loses a packet, so on `/ws` this is ticks the *server* did not send us, which
 *   is still the number that decides whether interpolation has two snapshots to
 *   stand between.
 * - **In / out** — bytes on the channel per second, from `ws.ts`'s counters.
 *
 * The native client measures the same five things the same way
 * (`apps/native-fps/src/net_stats.rs`).
 */

/** RFC 3550's gain for the jitter estimate. */
export const JITTER_GAIN = 1 / 16;
/** Gain for the rate estimates: smooth enough to read, quick enough to follow. */
export const RATE_GAIN = 1 / 8;
/** Loss and bandwidth are reported over whole windows of this length. */
export const WINDOW_MS = 1000;

export interface NetStatsView {
  /** Snapshots arriving per second, our clock. `null` before two have arrived. */
  snapshotHz: number | null;
  /** Server ticks per second, server clock. */
  serverTickHz: number | null;
  /** Interarrival jitter, ms. */
  jitterMs: number | null;
  /** Ticks skipped, % of ticks expected over the last window. */
  lossPct: number | null;
  /** Bytes per second received and sent over the last window. */
  inBytesPerSec: number | null;
  outBytesPerSec: number | null;
}

export class NetStats {
  private lastTick: number | null = null;
  private lastServerT = 0;
  private lastArrival = 0;
  private snapshotHz: number | null = null;
  private serverTickHz: number | null = null;
  private jitterMs: number | null = null;

  private windowStart: number | null = null;
  private windowExpected = 0;
  private windowLost = 0;
  private windowIn = 0;
  private windowOut = 0;
  private lossPct: number | null = null;
  private inBytesPerSec: number | null = null;
  private outBytesPerSec: number | null = null;

  /** One snapshot: its `tick`, its server stamp `t` (ms) and when it arrived. */
  onSnapshot(tick: number, serverT: number, arrivalMs: number): void {
    if (this.lastTick !== null && tick > this.lastTick) {
      const gap = tick - this.lastTick;
      const serverDelta = serverT - this.lastServerT;
      const arrivalDelta = arrivalMs - this.lastArrival;
      if (arrivalDelta > 0) {
        this.snapshotHz = ewma(this.snapshotHz, 1000 / arrivalDelta, RATE_GAIN);
      }
      if (serverDelta > 0) {
        this.serverTickHz = ewma(this.serverTickHz, (gap * 1000) / serverDelta, RATE_GAIN);
      }
      this.jitterMs = ewma(this.jitterMs ?? 0, Math.abs(arrivalDelta - serverDelta), JITTER_GAIN);
      this.windowExpected += gap;
      this.windowLost += gap - 1;
    }
    // A counter that went backwards is a restart (new room, new match), not
    // loss: it simply becomes the new reference below.
    this.lastTick = tick;
    this.lastServerT = serverT;
    this.lastArrival = arrivalMs;
  }

  /**
   * Fold in the channel's cumulative byte counters and roll the window when it
   * is due. Called once per frame; cheap when the window is not over.
   */
  sample(totalIn: number, totalOut: number, nowMs: number): void {
    if (this.windowStart === null) {
      this.windowStart = nowMs;
      this.windowIn = totalIn;
      this.windowOut = totalOut;
      return;
    }
    const elapsed = nowMs - this.windowStart;
    if (elapsed < WINDOW_MS) return;
    this.inBytesPerSec = ((totalIn - this.windowIn) * 1000) / elapsed;
    this.outBytesPerSec = ((totalOut - this.windowOut) * 1000) / elapsed;
    this.lossPct =
      this.windowExpected > 0 ? (this.windowLost / this.windowExpected) * 100 : this.lossPct;
    this.windowStart = nowMs;
    this.windowIn = totalIn;
    this.windowOut = totalOut;
    this.windowExpected = 0;
    this.windowLost = 0;
  }

  view(): NetStatsView {
    return {
      snapshotHz: this.snapshotHz,
      serverTickHz: this.serverTickHz,
      jitterMs: this.jitterMs,
      lossPct: this.lossPct,
      inBytesPerSec: this.inBytesPerSec,
      outBytesPerSec: this.outBytesPerSec,
    };
  }
}

function ewma(prev: number | null, sample: number, gain: number): number {
  return prev === null ? sample : prev + (sample - prev) * gain;
}
