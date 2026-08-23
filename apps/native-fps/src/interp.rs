//! Remote-player interpolation, and the render clock it defines.
//!
//! The server broadcasts at 20 Hz. Drawing each snapshot the instant it arrives
//! means every other player in the match moves in fifty-millisecond jumps — and
//! because those jumps are *also* where jitter lands, the bodies snap backwards
//! and forwards around where they really are. That is the rubber-banding people
//! read as "the netcode is bad" even on a loopback connection, and it is a
//! rendering bug rather than a networking one: the data is fine, it is being
//! shown at the wrong time.
//!
//! So bodies are drawn **in the past** — far enough back that the two snapshots
//! either side of the moment being drawn have both already arrived, and the
//! position between them is an interpolation rather than a guess.
//!
//! The clocks are the subtle part. The server's `t` and this process's clock
//! have no relation to each other, so rather than synchronising them this tracks
//! the **smallest** `local_arrival - server_t` ever seen. The minimum is the
//! sample that queued the least, which is the best estimate of the true offset
//! available without a clock-sync protocol — and, more importantly, it is
//! *stable*. An average would wander with the network and drag every body on
//! screen with it.
//!
//! This is a port of `SnapshotBuffer` in `packages/core/src/modules/hassault/net.ts`,
//! kept deliberately field-for-field: the browser and this client must not be
//! able to disagree about where a body is, because the one number the server
//! rewinds a shot to — `viewT` — comes from `render_time` on both sides.

use crate::protocol::PlayerRow;

/// How far behind the newest snapshot remote players are drawn, in ms.
///
/// Two snapshot intervals at 20 Hz. Below one interval there is routinely no
/// later snapshot to interpolate *towards*, which is exactly the moment a
/// renderer starts extrapolating and walking people through walls.
pub const INTERP_DELAY_MS: f64 = 100.0;

/// Snapshot history kept, in ms. Enough to ride out a stall, bounded so a long
/// match does not accumulate one.
pub const SNAPSHOT_BUFFER_MS: f64 = 2000.0;

struct Frame {
    t: f64,
    players: Vec<PlayerRow>,
}

#[derive(Default)]
pub struct SnapshotBuffer {
    frames: Vec<Frame>,
    /// Smallest observed `local - server_t`. `None` until the first snapshot:
    /// seeding it with zero would put the render clock an entire server epoch
    /// away and hold every body at its first known position.
    offset: Option<f64>,
}

impl SnapshotBuffer {
    pub fn new() -> SnapshotBuffer {
        SnapshotBuffer::default()
    }

    /// File one snapshot. `local_now` is this process's clock in ms — any
    /// monotonic source, as long as it is the same one `sample` is given.
    pub fn push(&mut self, server_t: f64, players: Vec<PlayerRow>, local_now: f64) {
        let seen = local_now - server_t;
        self.offset = Some(match self.offset {
            Some(current) => current.min(seen),
            None => seen,
        });
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
    /// positions `INTERP_DELAY_MS` newer than the ones you aimed at, and hitting
    /// a moving target means leading it by a body width.
    pub fn render_time(&self, local_now: f64) -> Option<f64> {
        self.offset
            .map(|offset| local_now - offset - INTERP_DELAY_MS)
    }

    /// Every remote body at `local_now`, interpolated.
    ///
    /// `self_id` is excluded: our own body comes from prediction, and drawing the
    /// interpolated copy as well would render us a tenth of a second behind
    /// ourselves — visible as a second player standing in our own footprints.
    pub fn sample(&self, local_now: f64, self_id: &str) -> Vec<PlayerRow> {
        let Some(target) = self.render_time(local_now) else {
            return Vec::new();
        };
        if self.frames.is_empty() {
            return Vec::new();
        }

        let mut older = &self.frames[0];
        let mut newer: Option<&Frame> = None;
        for frame in &self.frames {
            if frame.t <= target {
                older = frame;
            } else {
                newer = Some(frame);
                break;
            }
        }

        // Past the newest snapshot: hold the last known position rather than
        // extrapolate. A brief freeze is honest; a guess is a body drawn where
        // nobody is, and at worst inside a wall.
        let Some(newer) = newer else {
            return older
                .players
                .iter()
                .filter(|p| p.id != self_id)
                .cloned()
                .collect();
        };

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
                        // Popping it at 20 Hz would show a standing target for up
                        // to 50 ms after it went down.
                        out.crouch = lerp(from.crouch, to.crouch, t);
                        out
                    }
                }
            })
            .collect()
    }

    pub fn clear(&mut self) {
        self.frames.clear();
        self.offset = None;
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
        // Render time is local - offset(0) - 100, so local 1150 draws server
        // 1050 — halfway between the two frames.
        let out = buf.sample(1150.0, "me");
        assert_eq!(out.len(), 1);
        assert!((out[0].x - 5.0).abs() < 0.001, "{}", out[0].x);
    }

    #[test]
    fn past_the_newest_snapshot_a_body_holds_rather_than_extrapolating() {
        let mut buf = SnapshotBuffer::new();
        buf.push(1000.0, vec![row("a", 0.0, 0.0)], 1000.0);
        buf.push(1100.0, vec![row("a", 10.0, 0.0)], 1100.0);
        // A stall: render time is now well past the newest frame.
        let out = buf.sample(2000.0, "me");
        assert_eq!(out[0].x, 10.0, "extrapolated instead of holding");
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
        // render clock permanently behind and every body with it.
        assert_eq!(buf.render_time(1300.0), Some(1200.0));
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
