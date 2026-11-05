//! What the NetGraph's second and third rows measure.
//!
//! A port of the browser's `net-stats.ts`; the two measure the same five things
//! the same way, so a number read off either client means the same thing. Those
//! rows used to be string literals here too — `TICK: 15.6MS`, `IN: 14.2 KB/S`,
//! `JITTER: 0.6MS` — which survived the server going to 60 Hz without changing a
//! digit.
//!
//! - **Snapshot rate**: arrivals per second, on our clock.
//! - **Server tick rate**: ticks per second of *server* time, from the
//!   snapshot's own `tick` counter and `t` stamp.
//! - **Jitter**: RFC 3550 interarrival jitter, how far each gap between
//!   arrivals differs from the gap between the server's stamps.
//! - **Loss**: ticks the counter skipped over the last whole window.
//! - **In / out**: bytes on the socket per second, from `net::Traffic`.
//!
//! Arrival is stamped when the game loop drains the socket, not when the socket
//! thread read the frame, so jitter includes up to one frame of quantisation —
//! under a millisecond at the frame rates this client runs at, and the same
//! clock the interpolation buffer reads.

/// RFC 3550's gain for the jitter estimate.
pub const JITTER_GAIN: f64 = 1.0 / 16.0;
/// Gain for the rate estimates.
pub const RATE_GAIN: f64 = 1.0 / 8.0;
/// Loss and bandwidth are reported over whole windows of this length.
pub const WINDOW_MS: f64 = 1000.0;

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct NetStatsView {
    pub snapshot_hz: Option<f64>,
    pub server_tick_hz: Option<f64>,
    pub jitter_ms: Option<f64>,
    pub loss_pct: Option<f64>,
    pub in_bytes_per_sec: Option<f64>,
    pub out_bytes_per_sec: Option<f64>,
}

#[derive(Debug, Default)]
pub struct NetStats {
    last_tick: Option<u64>,
    last_server_t: f64,
    last_arrival: f64,
    snapshot_hz: Option<f64>,
    server_tick_hz: Option<f64>,
    jitter_ms: Option<f64>,
    window_start: Option<f64>,
    window_expected: u64,
    window_lost: u64,
    window_in: u64,
    window_out: u64,
    loss_pct: Option<f64>,
    in_bytes_per_sec: Option<f64>,
    out_bytes_per_sec: Option<f64>,
}

fn ewma(prev: Option<f64>, sample: f64, gain: f64) -> f64 {
    match prev {
        None => sample,
        Some(p) => p + (sample - p) * gain,
    }
}

impl NetStats {
    /// One snapshot: its `tick`, its server stamp `t` (ms) and when it arrived (ms).
    pub fn on_snapshot(&mut self, tick: u64, server_t: f64, arrival_ms: f64) {
        if let Some(last) = self.last_tick {
            if tick > last {
                let gap = tick - last;
                let server_delta = server_t - self.last_server_t;
                let arrival_delta = arrival_ms - self.last_arrival;
                if arrival_delta > 0.0 {
                    self.snapshot_hz =
                        Some(ewma(self.snapshot_hz, 1000.0 / arrival_delta, RATE_GAIN));
                }
                if server_delta > 0.0 {
                    self.server_tick_hz = Some(ewma(
                        self.server_tick_hz,
                        gap as f64 * 1000.0 / server_delta,
                        RATE_GAIN,
                    ));
                }
                self.jitter_ms = Some(ewma(
                    Some(self.jitter_ms.unwrap_or(0.0)),
                    (arrival_delta - server_delta).abs(),
                    JITTER_GAIN,
                ));
                self.window_expected += gap;
                self.window_lost += gap - 1;
            }
            // A counter that went backwards is a restart (new room, new
            // match), not loss: it simply becomes the new reference below.
        }
        self.last_tick = Some(tick);
        self.last_server_t = server_t;
        self.last_arrival = arrival_ms;
    }

    /// Fold in the socket's cumulative byte counters, rolling the window when due.
    pub fn sample(&mut self, total_in: u64, total_out: u64, now_ms: f64) {
        let Some(start) = self.window_start else {
            self.window_start = Some(now_ms);
            self.window_in = total_in;
            self.window_out = total_out;
            return;
        };
        let elapsed = now_ms - start;
        if elapsed < WINDOW_MS {
            return;
        }
        self.in_bytes_per_sec =
            Some(total_in.saturating_sub(self.window_in) as f64 * 1000.0 / elapsed);
        self.out_bytes_per_sec =
            Some(total_out.saturating_sub(self.window_out) as f64 * 1000.0 / elapsed);
        if self.window_expected > 0 {
            self.loss_pct = Some(self.window_lost as f64 / self.window_expected as f64 * 100.0);
        }
        self.window_start = Some(now_ms);
        self.window_in = total_in;
        self.window_out = total_out;
        self.window_expected = 0;
        self.window_lost = 0;
    }

    pub fn view(&self) -> NetStatsView {
        NetStatsView {
            snapshot_hz: self.snapshot_hz,
            server_tick_hz: self.server_tick_hz,
            jitter_ms: self.jitter_ms,
            loss_pct: self.loss_pct,
            in_bytes_per_sec: self.in_bytes_per_sec,
            out_bytes_per_sec: self.out_bytes_per_sec,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // The same cases as the browser's `perf-instruments.test.ts`, so the two
    // ports are held to the same behaviour.

    #[test]
    fn a_steady_60hz_stream_reads_as_60hz_with_no_jitter_or_loss() {
        let mut s = NetStats::default();
        for i in 0..120u64 {
            let t = i as f64 * (1000.0 / 60.0);
            s.on_snapshot(i, t, 5000.0 + t);
        }
        s.sample(0, 0, 0.0);
        s.sample(60_000, 1_000, WINDOW_MS);
        let v = s.view();
        assert!((v.snapshot_hz.unwrap() - 60.0).abs() < 1e-6);
        assert!((v.server_tick_hz.unwrap() - 60.0).abs() < 1e-6);
        assert!(v.jitter_ms.unwrap().abs() < 1e-6);
        assert_eq!(v.loss_pct, Some(0.0));
        assert_eq!(v.in_bytes_per_sec, Some(60_000.0));
        assert_eq!(v.out_bytes_per_sec, Some(1_000.0));
    }

    #[test]
    fn skipped_ticks_count_as_loss_over_the_window() {
        let mut s = NetStats::default();
        s.sample(0, 0, 0.0);
        for i in 0..20u64 {
            if i != 5 && i != 6 {
                s.on_snapshot(i, i as f64 * 16.0, i as f64 * 16.0);
            }
        }
        s.sample(0, 0, WINDOW_MS);
        assert!((s.view().loss_pct.unwrap() - 2.0 / 19.0 * 100.0).abs() < 1e-9);
    }

    #[test]
    fn bunched_arrivals_are_jitter_not_a_tick_rate_change() {
        let mut s = NetStats::default();
        let mut arrival = 0.0;
        for i in 0..200u64 {
            arrival += if i % 2 == 0 { 6.0 } else { 27.33 };
            s.on_snapshot(i, i as f64 * (1000.0 / 60.0), arrival);
        }
        let v = s.view();
        assert!((v.server_tick_hz.unwrap() - 60.0).abs() < 1e-3);
        let j = v.jitter_ms.unwrap();
        assert!(j > 8.0 && j < 12.0, "jitter {j}");
    }

    #[test]
    fn a_counter_that_went_backwards_is_a_new_match_not_loss() {
        let mut s = NetStats::default();
        s.sample(0, 0, 0.0);
        for i in 500..510u64 {
            s.on_snapshot(i, i as f64 * 16.0, i as f64 * 16.0);
        }
        for i in 0..10u64 {
            s.on_snapshot(i, 20_000.0 + i as f64 * 16.0, 20_000.0 + i as f64 * 16.0);
        }
        s.sample(0, 0, WINDOW_MS);
        assert_eq!(s.view().loss_pct, Some(0.0));
    }

    #[test]
    fn nothing_is_reported_before_it_is_measured() {
        assert_eq!(NetStats::default().view(), NetStatsView::default());
    }
}
