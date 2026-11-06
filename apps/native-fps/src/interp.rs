//! Remote-player interpolation, and the render clock it defines.
//!
//! The server broadcasts at 60 Hz. Drawing each snapshot the instant it arrives
//! means every other player in the match moves in tick-sized jumps — and
//! because those jumps are *also* where jitter lands, the bodies snap backwards
//! and forwards around where they really are. That is the rubber-banding people
//! read as "the netcode is bad" even on a loopback connection, and it is a
//! rendering bug rather than a networking one: the data is fine, it is being
//! shown at the wrong time.
//!
//! So bodies are drawn **in the past** — far enough back that the two snapshots
//! either side of the moment being drawn have usually both arrived, and the
//! position between them is an interpolation rather than a guess.
//!
//! How far back is `InterpClock`'s job, and it follows the network: one tick
//! plus twice the measured jitter, eased so nobody visibly speeds up or slows
//! down, against a clock offset taken from the least-queued snapshot of the
//! last few seconds. Where everyone was at that instant is `SnapshotBuffer`'s:
//! on the straight line between the two snapshots around it — the same line the
//! server rewinds a shot along (`PositionHistory.rewind`).
//!
//! A port of `InterpClock` and `SnapshotBuffer` in
//! `packages/core/src/modules/hassault/net.ts`, and both are ports of the
//! reference `InterpClock` in `scripts/gen_hassault_vectors.py`.
//! `interp-vectors.json` holds all three to it (`tests/conformance.rs`): the
//! browser and this client must not be able to disagree about where a body is,
//! because the one number the server rewinds a shot to — `viewT` — comes from
//! `render_time` on both sides.

use crate::protocol::PlayerRow;

/// One server tick: the least a body can trail by and still be bracketed.
pub const TICK_MS: f64 = 1000.0 / 60.0;
/// The interpolation delay's floor and ceiling, ms.
pub const MIN_DELAY_MS: f64 = 25.0;
pub const MAX_DELAY_MS: f64 = 100.0;
/// RFC 3550's interarrival-jitter gain, as `NetStats` uses.
pub const JITTER_GAIN: f64 = 1.0 / 16.0;
/// The clock offset is the smallest `local - t` over this window, ms. Not of
/// all time: a local clock running slightly fast makes the true offset grow,
/// and an all-time minimum would leave the render clock creeping ahead of the
/// data for the rest of the match.
pub const WINDOW_MS: f64 = 5000.0;
/// ms of lag change per ms: the render clock runs at most 10% slow while
/// backing off and 4% fast while catching up.
pub const EASE_UP: f64 = 0.10;
pub const EASE_DOWN: f64 = 0.04;
/// A change this big is a new clock, not drift, and is taken at once.
pub const SNAP_MS: f64 = 250.0;
/// After the first snapshot (or a snap) the lag follows its target uneased for
/// this long: a connection's first frames often queue, and easing down from one
/// of those would draw every body late for seconds.
pub const WARMUP_MS: f64 = 1000.0;
/// Starved: carry a body on along its last velocity this long, horizontally.
pub const EXTRAPOLATE_MS: f64 = 50.0;
/// Cubes per second. Faster than a body can move is a teleport, and is held.
pub const EXTRAPOLATE_MAX_SPEED: f64 = 40.0;

/// Snapshot history kept, in ms. Enough to ride out a stall, bounded so a long
/// match does not accumulate one.
pub const SNAPSHOT_BUFFER_MS: f64 = 2000.0;

/// The render clock: which server instant remote bodies are drawn at.
#[derive(Default)]
pub struct InterpClock {
    /// `(local arrival, local - t)`, samples increasing: the head is the minimum.
    window: Vec<(f64, f64)>,
    jitter: f64,
    last: Option<(f64, f64)>,
    /// `None` until the first snapshot: seeding it with zero would put the
    /// render clock an entire server epoch away.
    lag: Option<f64>,
    anchor: f64,
    target: f64,
    since: f64,
}

impl InterpClock {
    pub fn push(&mut self, t: f64, local: f64) {
        let sample = local - t;
        while self.window.last().is_some_and(|&(_, s)| s >= sample) {
            self.window.pop();
        }
        self.window.push((local, sample));
        let cutoff = local - WINDOW_MS;
        while self.window[0].0 < cutoff {
            self.window.remove(0);
        }
        if let Some((last_local, last_t)) = self.last {
            let d = ((local - last_local) - (t - last_t)).abs();
            self.jitter += (d - self.jitter) * JITTER_GAIN;
        }
        self.last = Some((local, t));
        let delay = (TICK_MS + 2.0 * self.jitter)
            .max(MIN_DELAY_MS)
            .min(MAX_DELAY_MS);
        let mut target = self.window[0].1 + delay;
        match self.lag_at(local) {
            None => {
                self.lag = Some(target);
                self.since = local;
            }
            Some(current) if (target - current).abs() > SNAP_MS => {
                // A new clock, not a late packet: the step it put into the
                // jitter estimate is not jitter.
                self.jitter = 0.0;
                target = self.window[0].1 + TICK_MS.max(MIN_DELAY_MS);
                self.lag = Some(target);
                self.since = local;
            }
            Some(_) if local - self.since < WARMUP_MS => self.lag = Some(target),
            Some(current) => self.lag = Some(current),
        }
        self.anchor = local;
        self.target = target;
    }

    /// The lag at `local`, eased from where the last push left it. Pure.
    fn lag_at(&self, local: f64) -> Option<f64> {
        let lag = self.lag?;
        let dt = (local - self.anchor).max(0.0);
        let diff = self.target - lag;
        Some(if diff > 0.0 {
            lag + diff.min(EASE_UP * dt)
        } else {
            lag + diff.max(-EASE_DOWN * dt)
        })
    }

    /// The server-clock instant to draw at `local`; `None` before any snapshot.
    pub fn render_time(&self, local: f64) -> Option<f64> {
        self.lag_at(local).map(|lag| local - lag)
    }

    /// How far behind the newest data bodies are drawn, ms. For the net graph.
    pub fn delay(&self, local: f64) -> Option<f64> {
        self.lag_at(local).map(|lag| lag - self.window[0].1)
    }

    pub fn reset(&mut self) {
        *self = InterpClock::default();
    }
}

struct Frame {
    t: f64,
    players: Vec<PlayerRow>,
}

#[derive(Default)]
pub struct SnapshotBuffer {
    frames: Vec<Frame>,
    clock: InterpClock,
}

impl SnapshotBuffer {
    pub fn new() -> SnapshotBuffer {
        SnapshotBuffer::default()
    }

    /// File one snapshot. `local_now` is this process's clock in ms — any
    /// monotonic source, as long as it is the same one `sample` is given.
    pub fn push(&mut self, server_t: f64, players: Vec<PlayerRow>, local_now: f64) {
        self.clock.push(server_t, local_now);
        self.frames.push(Frame {
            t: server_t,
            players,
        });
        // Sorted rather than assumed ordered: snapshots arrive out of order on a
        // lossy link, and an out-of-order frame appended blind makes the search
        // below pick the wrong pair and jerk every body.
        self.frames.sort_by(|a, b| a.t.total_cmp(&b.t));
        let cutoff = server_t - SNAPSHOT_BUFFER_MS;
        while self.frames.len() > 2 && self.frames[0].t < cutoff {
            self.frames.remove(0);
        }
    }

    /// The server-clock instant the renderer is currently showing.
    ///
    /// This is what a shot must be stamped with (`viewT`): the server rewinds
    /// its position history to this moment to decide whether the body you were
    /// looking at was really there. Without it, every shot is resolved against
    /// positions newer than the ones you aimed at, and hitting a moving target
    /// means leading it by a body width.
    pub fn render_time(&self, local_now: f64) -> Option<f64> {
        self.clock.render_time(local_now)
    }

    /// The current interpolation delay, ms; `None` before the first snapshot.
    pub fn delay_ms(&self, local_now: f64) -> Option<f64> {
        self.clock.delay(local_now)
    }

    /// Every remote body at `local_now`, interpolated.
    ///
    /// `self_id` is excluded: our own body comes from prediction, and drawing the
    /// interpolated copy as well would render us a tenth of a second behind
    /// ourselves — visible as a second player standing in our own footprints.
    pub fn sample(&self, local_now: f64, self_id: &str) -> Vec<PlayerRow> {
        match self.render_time(local_now) {
            Some(target) => self.sample_at(target, self_id),
            None => Vec::new(),
        }
    }

    /// Every remote body at server instant `target`: the curve itself.
    pub fn sample_at(&self, target: f64, self_id: &str) -> Vec<PlayerRow> {
        if self.frames.is_empty() {
            return Vec::new();
        }

        let mut older = 0;
        let mut newer: Option<&Frame> = None;
        for (i, frame) in self.frames.iter().enumerate() {
            if frame.t <= target {
                older = i;
            } else {
                newer = Some(frame);
                break;
            }
        }

        let Some(newer) = newer else {
            return self.extrapolate(older, target, self_id);
        };
        let older = &self.frames[older];

        let span = newer.t - older.t;
        let t = if span > 0.0 {
            ((target - older.t) / span).clamp(0.0, 1.0) as f32
        } else {
            0.0
        };

        older
            .players
            .iter()
            .filter(|p| p.id != self_id)
            .map(|from| {
                match newer.players.iter().find(|p| p.id == from.id) {
                    // Present in the older frame and gone from the newer one: they
                    // left. Holding the last position is right for this frame —
                    // the roster, not the interpolator, is what removes a body.
                    None => from.clone(),
                    Some(to) => {
                        let mut out = to.clone();
                        out.x = lerp(from.x, to.x, t);
                        out.y = lerp(from.y, to.y, t);
                        out.z = lerp(from.z, to.z, t);
                        out.yaw = lerp_angle(from.yaw, to.yaw, t);
                        out.pitch = lerp(from.pitch, to.pitch, t);
                        // Crouch decides how tall the box is drawn, and a body is
                        // only honest if it is drawn the height it can be hit at.
                        out.crouch = lerp(from.crouch, to.crouch, t);
                        out
                    }
                }
            })
            .collect()
    }

    /// Past the newest snapshot: starved. Each body carries on along its last
    /// velocity for up to `EXTRAPOLATE_MS`, then holds.
    ///
    /// Bounded three ways, because a guess is a body drawn where nobody is: in
    /// time (50 ms is about one body width at a run), to the horizontal (a
    /// falling body carried on would sink into the floor it lands on), and in
    /// speed (a respawn between two snapshots reads as a sprint across the map,
    /// and is held instead).
    fn extrapolate(&self, newest: usize, target: f64, self_id: &str) -> Vec<PlayerRow> {
        let frame = &self.frames[newest];
        let prev = newest.checked_sub(1).map(|i| &self.frames[i]);
        let span = prev.map_or(0.0, |p| frame.t - p.t);
        let ahead = (target - frame.t).min(EXTRAPOLATE_MS);
        frame
            .players
            .iter()
            .filter(|p| p.id != self_id)
            .map(|row| {
                let mut out = row.clone();
                let Some(from) = prev.and_then(|p| p.players.iter().find(|q| q.id == row.id))
                else {
                    return out;
                };
                if span <= 0.0 || ahead <= 0.0 {
                    return out;
                }
                let vx = (f64::from(row.x) - f64::from(from.x)) / span;
                let vy = (f64::from(row.y) - f64::from(from.y)) / span;
                if vx.hypot(vy) * 1000.0 > EXTRAPOLATE_MAX_SPEED {
                    return out;
                }
                out.x = (f64::from(row.x) + vx * ahead) as f32;
                out.y = (f64::from(row.y) + vy * ahead) as f32;
                out
            })
            .collect()
    }

    pub fn clear(&mut self) {
        self.frames.clear();
        self.clock.reset();
    }
}

fn lerp(a: f32, b: f32, t: f32) -> f32 {
    a + (b - a) * t
}

/// Interpolate a heading the short way round.
///
/// **Radians**, because that is what a `PlayerRow` carries: the server stores
/// `state.yaw` in radians and puts it on the wire unconverted. The camera is the
/// only thing in this client that works in degrees, and it is not what this
/// interpolates.
///
/// Wrapping on 360 instead — which this did — is not a smaller mistake than
/// having no wrap at all, it is the same one: a radian difference never exceeds
/// 2π, so neither branch could ever fire, and a body turning past ±π took the
/// long way round between two snapshots. The one interpolation bug everybody
/// writes once, wearing the fix for it as a disguise.
fn lerp_angle(a: f32, b: f32, t: f32) -> f32 {
    let mut diff = (b - a) % std::f32::consts::TAU;
    if diff > std::f32::consts::PI {
        diff -= std::f32::consts::TAU;
    }
    if diff < -std::f32::consts::PI {
        diff += std::f32::consts::TAU;
    }
    a + diff * t
}

/// Round-trip time as a median of recent samples.
///
/// Median rather than mean: one reply that queued behind a garbage collection
/// should not define the reading the player is shown, and a mean has no way to
/// ignore it.
#[derive(Default)]
pub struct PingTracker {
    samples: Vec<f32>,
}

impl PingTracker {
    pub fn record(&mut self, rtt_ms: f32) {
        self.samples.push(rtt_ms);
        if self.samples.len() > 8 {
            self.samples.remove(0);
        }
    }

    pub fn rtt(&self) -> Option<f32> {
        if self.samples.is_empty() {
            return None;
        }
        let mut sorted = self.samples.clone();
        sorted.sort_by(f32::total_cmp);
        Some(sorted[sorted.len() / 2])
    }

    pub fn reset(&mut self) {
        self.samples.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(id: &str, x: f32, yaw: f32) -> PlayerRow {
        PlayerRow {
            id: id.into(),
            x,
            yaw,
            alive: true,
            ..Default::default()
        }
    }

    #[test]
    fn a_body_is_drawn_between_the_two_snapshots_around_the_render_time() {
        let mut buf = SnapshotBuffer::new();
        // Offset is pinned by the first push: local 1000 for server 1000.
        buf.push(1000.0, vec![row("a", 0.0, 0.0)], 1000.0);
        buf.push(1100.0, vec![row("a", 10.0, 0.0)], 1100.0);
        // Server instant 1050: halfway between the two frames.
        let out = buf.sample_at(1050.0, "me");
        assert_eq!(out.len(), 1);
        assert!((out[0].x - 5.0).abs() < 0.001, "{}", out[0].x);
    }

    #[test]
    fn a_starved_body_carries_on_for_at_most_50_ms_then_holds() {
        let mut buf = SnapshotBuffer::new();
        // 2 cubes in 100 ms: a run.
        buf.push(1000.0, vec![row("a", 0.0, 0.0)], 1000.0);
        buf.push(1100.0, vec![row("a", 2.0, 0.0)], 1100.0);
        let out = buf.sample_at(1120.0, "me");
        assert!((out[0].x - 2.4).abs() < 1e-4, "{}", out[0].x);
        // A stall: well past the newest frame. 50 ms of guess, never more.
        let out = buf.sample_at(5000.0, "me");
        assert!((out[0].x - 3.0).abs() < 1e-4, "{}", out[0].x);
    }

    #[test]
    fn a_body_faster_than_anyone_can_move_is_held_not_extrapolated() {
        let mut buf = SnapshotBuffer::new();
        // A respawn between two snapshots: 40 cubes in 100 ms.
        buf.push(1000.0, vec![row("a", 0.0, 0.0)], 1000.0);
        buf.push(1100.0, vec![row("a", 40.0, 0.0)], 1100.0);
        assert_eq!(buf.sample_at(1130.0, "me")[0].x, 40.0);
    }

    #[test]
    fn our_own_body_is_never_sampled() {
        let mut buf = SnapshotBuffer::new();
        buf.push(
            1000.0,
            vec![row("me", 0.0, 0.0), row("a", 0.0, 0.0)],
            1000.0,
        );
        let out = buf.sample(1200.0, "me");
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].id, "a");
    }

    #[test]
    fn a_heading_wraps_the_short_way() {
        use std::f32::consts::{PI, TAU};
        // Just under a full turn to just over zero is a short step forward, not
        // most of a circle backwards. In **radians**, which is what a row carries.
        let out = lerp_angle(TAU - 0.1, 0.1, 0.5);
        assert!((out - TAU).abs() < 1e-4, "{out}");

        // The half of it that a 360° wrap could never reach: a radian difference
        // never exceeds 2π, so wrapping on 360 left both branches dead and this
        // case took the long way round. Crossing ±π is where that shows.
        let out = lerp_angle(PI - 0.1, -PI + 0.1, 0.5);
        assert!(
            (out.abs() - PI).abs() < 1e-4,
            "crossed the wrap the long way: {out}"
        );
    }

    #[test]
    fn the_offset_is_the_smallest_delay_seen_not_the_latest() {
        let mut buf = SnapshotBuffer::new();
        // A snapshot that queued for 200 ms, then one that did not queue at all.
        buf.push(1000.0, vec![row("a", 0.0, 0.0)], 1200.0);
        buf.push(1100.0, vec![row("a", 0.0, 0.0)], 1100.0);
        // Offset settles on 0, not 200 — otherwise one slow packet would put the
        // render clock permanently behind and every body with it. Inside the
        // warm-up, so at once; the delay is one tick plus twice the one jitter
        // sample (|-100 - 100| / 16).
        let expect = 1300.0 - (TICK_MS + 2.0 * 12.5);
        let got = buf.render_time(1300.0).unwrap();
        assert!((got - expect).abs() < 1e-9, "{got} vs {expect}");
    }

    #[test]
    fn there_is_no_render_time_before_the_first_snapshot() {
        // Not zero: a caller stamping a shot with a fabricated render time asks
        // the server to rewind to a moment that never existed.
        assert_eq!(SnapshotBuffer::new().render_time(1000.0), None);
    }

    #[test]
    fn the_median_ignores_one_stalled_reply() {
        let mut ping = PingTracker::default();
        for sample in [20.0, 22.0, 400.0, 21.0, 23.0] {
            ping.record(sample);
        }
        assert_eq!(ping.rtt(), Some(22.0));
    }
}
