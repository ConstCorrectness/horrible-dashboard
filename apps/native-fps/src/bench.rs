//! The frame-time benchmark: fly a fixed camera path over a map and report
//! frame-time, CPU and GPU percentiles and draw calls.
//!
//! The path is `packages/core/src/modules/hassault/bench-paths.json`, generated
//! from the map JSONs by `scripts/gen_hassault_bench_paths.py` and flown by the
//! browser's `bench` console command too (`bench.ts`). One path for both is the
//! point: a number from one client is comparable with the other's only if both
//! looked at the same things for the same time.
//!
//! Each framing is held for `seconds`, turning at `spin` degrees per second.
//! Framings cut rather than blend, so a framing's cost never depends on its
//! neighbours. The first `warmupSeconds` hold the first framing unrecorded:
//! pipeline compiles and uploads are a load cost, not a frame cost.
//!
//! Started with `--bench=<map>` (`main.rs`); the result is printed as one JSON
//! line on stdout, prefixed `BENCH `, and the client exits.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::profile::{Phase, PhaseTimes};

const PATHS_JSON: &str =
    include_str!("../../../packages/core/src/modules/hassault/bench-paths.json");

#[derive(Clone, Debug, Deserialize)]
pub struct Framing {
    pub label: String,
    /// Map cubes: plan x, plan y, absolute eye height.
    pub eye: [f32; 3],
    /// Degrees; 0 = +x, 90 = +y (the server's convention).
    pub yaw: f32,
    /// Degrees; positive looks up.
    pub pitch: f32,
    pub seconds: f32,
    /// Degrees per second.
    pub spin: f32,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Paths {
    warmup_seconds: f32,
    maps: std::collections::BTreeMap<String, Vec<Framing>>,
}

fn paths() -> Paths {
    serde_json::from_str(PATHS_JSON).expect("bench-paths.json is generated and committed")
}

pub fn path_for(map: &str) -> Option<Vec<Framing>> {
    paths().maps.remove(map)
}

pub fn maps() -> Vec<String> {
    paths().maps.into_keys().collect()
}

pub fn warmup_seconds() -> f32 {
    paths().warmup_seconds
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Pose {
    pub x: f32,
    pub y: f32,
    pub z: f32,
    /// Radians, the player's convention.
    pub yaw: f32,
    pub pitch: f32,
    /// Index into the framings, or `None` during warm-up.
    pub index: Option<usize>,
}

pub fn path_seconds(framings: &[Framing]) -> f32 {
    framings.iter().map(|f| f.seconds).sum()
}

/// Where the camera is `t` seconds into the path, or `None` past its end.
pub fn pose_at(framings: &[Framing], t: f32) -> Option<Pose> {
    let mut start = 0.0;
    for (i, f) in framings.iter().enumerate() {
        if t < start + f.seconds {
            let local = t - start;
            return Some(Pose {
                x: f.eye[0],
                y: f.eye[1],
                z: f.eye[2],
                yaw: (f.yaw + f.spin * local).to_radians(),
                pitch: f.pitch.to_radians(),
                index: Some(i),
            });
        }
        start += f.seconds;
    }
    None
}

/// Nearest-rank percentile of an unsorted list.
pub fn percentile(values: &[f64], p: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut sorted = values.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let rank = ((p / 100.0) * sorted.len() as f64).ceil() as isize - 1;
    sorted[rank.clamp(0, sorted.len() as isize - 1) as usize]
}

#[derive(Clone, Copy, Debug)]
pub struct Sample {
    /// Interval since the previous frame.
    pub frame_ms: f64,
    /// CPU time spent building and submitting this frame.
    pub cpu_ms: f64,
    pub draw_calls: u32,
    pub triangles: u64,
    /// `cpu_ms`, split by phase (`profile.rs`).
    pub phases: PhaseTimes,
}

#[derive(Serialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct FramingStats {
    pub label: String,
    pub frames: usize,
    pub p50: f64,
    pub p99: f64,
}

/// The same shape the browser's `BenchResult` has, so the two can be tabulated
/// together.
#[derive(Serialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct BenchResult {
    pub map: String,
    pub client: &'static str,
    pub tier: String,
    /// The drawing surface, in physical pixels.
    pub width: u32,
    pub height: u32,
    pub frames: usize,
    pub seconds: f64,
    pub fps: f64,
    pub p50: f64,
    pub p95: f64,
    pub p99: f64,
    pub max: f64,
    pub cpu_p50: f64,
    pub cpu_p99: f64,
    pub gpu_p50: Option<f64>,
    pub gpu_p99: Option<f64>,
    pub draw_calls_max: u32,
    pub draw_calls_mean: f64,
    pub triangles_max: u64,
    /// Mean milliseconds per CPU phase, native only. Means rather than
    /// percentiles because means add up: the phases sum to the mean `cpu_ms`,
    /// so the table says what share of the frame each one is.
    pub cpu_phases_mean: BTreeMap<&'static str, f64>,
    /// The 99th percentile per CPU phase: where a frame-time tail lives, which
    /// the means cannot say. These do **not** add up — each is its own phase's
    /// worst frames, not the worst frames' phases.
    pub cpu_phases_p99: BTreeMap<&'static str, f64>,
    pub framings: Vec<FramingStats>,
}

fn round2(v: f64) -> f64 {
    (v * 100.0).round() / 100.0
}

/// One run: `advance(dt)` says where the camera goes, `record` takes the frame
/// just drawn, `done` says when to stop.
pub struct BenchRun {
    pub map: String,
    pub tier: String,
    /// The surface the frames were drawn at; the caller keeps it current.
    pub size: (u32, u32),
    framings: Vec<Framing>,
    warmup: f32,
    elapsed: f32,
    current: Option<usize>,
    samples: Vec<Sample>,
    by_framing: Vec<Vec<f64>>,
    gpu: Vec<f64>,
}

impl BenchRun {
    pub fn new(map: &str, framings: Vec<Framing>, tier: &str, warmup: f32) -> BenchRun {
        let n = framings.len();
        BenchRun {
            map: map.to_string(),
            tier: tier.to_string(),
            size: (0, 0),
            framings,
            warmup,
            elapsed: 0.0,
            current: None,
            samples: Vec::new(),
            by_framing: vec![Vec::new(); n],
            gpu: Vec::new(),
        }
    }

    pub fn advance(&mut self, dt: f32) -> Option<Pose> {
        self.elapsed += dt;
        if self.elapsed < self.warmup {
            self.current = None;
            return pose_at(&self.framings, 0.0).map(|p| Pose { index: None, ..p });
        }
        let pose = pose_at(&self.framings, self.elapsed - self.warmup);
        self.current = pose.and_then(|p| p.index);
        pose
    }

    pub fn record(&mut self, sample: Sample) {
        if let Some(i) = self.current {
            self.samples.push(sample);
            self.by_framing[i].push(sample.frame_ms);
        }
    }

    pub fn record_gpu(&mut self, gpu_ms: f64) {
        if self.current.is_some() {
            self.gpu.push(gpu_ms);
        }
    }

    pub fn done(&self) -> bool {
        self.elapsed >= self.warmup + path_seconds(&self.framings)
    }

    pub fn result(&self) -> BenchResult {
        let ms: Vec<f64> = self.samples.iter().map(|s| s.frame_ms).collect();
        let cpu: Vec<f64> = self.samples.iter().map(|s| s.cpu_ms).collect();
        let seconds = ms.iter().sum::<f64>() / 1000.0;
        let calls: Vec<u32> = self.samples.iter().map(|s| s.draw_calls).collect();
        BenchResult {
            map: self.map.clone(),
            client: "native",
            tier: self.tier.clone(),
            width: self.size.0,
            height: self.size.1,
            frames: ms.len(),
            seconds: round2(seconds),
            fps: if seconds > 0.0 {
                round2(ms.len() as f64 / seconds)
            } else {
                0.0
            },
            p50: round2(percentile(&ms, 50.0)),
            p95: round2(percentile(&ms, 95.0)),
            p99: round2(percentile(&ms, 99.0)),
            max: round2(ms.iter().cloned().fold(0.0, f64::max)),
            cpu_p50: round2(percentile(&cpu, 50.0)),
            cpu_p99: round2(percentile(&cpu, 99.0)),
            gpu_p50: (!self.gpu.is_empty()).then(|| round2(percentile(&self.gpu, 50.0))),
            gpu_p99: (!self.gpu.is_empty()).then(|| round2(percentile(&self.gpu, 99.0))),
            draw_calls_max: calls.iter().copied().max().unwrap_or(0),
            draw_calls_mean: if calls.is_empty() {
                0.0
            } else {
                round2(calls.iter().map(|&c| c as f64).sum::<f64>() / calls.len() as f64)
            },
            triangles_max: self.samples.iter().map(|s| s.triangles).max().unwrap_or(0),
            cpu_phases_mean: Phase::ALL
                .iter()
                .map(|&phase| {
                    let sum: f64 = self.samples.iter().map(|s| s.phases.get(phase)).sum();
                    let n = self.samples.len().max(1) as f64;
                    (phase.name(), (sum / n * 1000.0).round() / 1000.0)
                })
                .collect(),
            cpu_phases_p99: Phase::ALL
                .iter()
                .map(|&phase| {
                    let v: Vec<f64> = self.samples.iter().map(|s| s.phases.get(phase)).collect();
                    (phase.name(), (percentile(&v, 99.0) * 1000.0).round() / 1000.0)
                })
                .collect(),
            framings: self
                .framings
                .iter()
                .zip(&self.by_framing)
                .map(|(f, v)| FramingStats {
                    label: f.label.clone(),
                    frames: v.len(),
                    p50: round2(percentile(v, 50.0)),
                    p99: round2(percentile(v, 99.0)),
                })
                .collect(),
        }
    }
}

/// Frames the NetGraph's percentiles are taken over: ten seconds at 60, the
/// browser's `FRAME_RING` (`perf-stats.ts`).
pub const FRAME_RING: usize = 600;

/// A ring of recent frame times for the NetGraph's percentiles.
pub struct FrameRing {
    ring: Vec<f64>,
    head: usize,
    count: usize,
}

impl FrameRing {
    pub fn new(capacity: usize) -> FrameRing {
        FrameRing {
            ring: vec![0.0; capacity.max(1)],
            head: 0,
            count: 0,
        }
    }

    pub fn push(&mut self, ms: f64) {
        self.ring[self.head] = ms;
        self.head = (self.head + 1) % self.ring.len();
        self.count = (self.count + 1).min(self.ring.len());
    }

    pub fn percentile(&self, p: f64) -> Option<f64> {
        (self.count > 0).then(|| percentile(&self.ring[..self.count], p))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_shipped_map_has_a_path() {
        let maps = maps();
        for m in [
            "hd_aim",
            "hd_assault",
            "hd_bank",
            "hd_dust2",
            "hd_facility",
            "hd_inferno",
            "hd_junkflea",
            "hd_mirage",
            "hd_nuke",
            "hd_office",
        ] {
            assert!(maps.iter().any(|x| x == m), "{m} has no bench path");
        }
    }

    #[test]
    fn spins_within_a_framing_and_ends_after_the_last() {
        let path = path_for("hd_dust2").unwrap();
        let turn = path.iter().position(|f| f.spin > 0.0).unwrap();
        let start: f32 = path[..turn].iter().map(|f| f.seconds).sum();
        let a = pose_at(&path, start).unwrap();
        let b = pose_at(&path, start + 1.0).unwrap();
        assert!((b.yaw - a.yaw - path[turn].spin.to_radians()).abs() < 1e-5);
        assert!(pose_at(&path, path_seconds(&path) + 0.01).is_none());
    }

    #[test]
    fn warm_up_is_drawn_but_not_recorded() {
        let path = path_for("hd_aim").unwrap();
        let total = path_seconds(&path) as f64;
        let mut run = BenchRun::new("hd_aim", path, "high", 1.0);
        while !run.done() {
            let Some(pose) = run.advance(0.01) else { break };
            let frame_ms = if pose.index.is_none() { 999.0 } else { 10.0 };
            run.record(Sample {
                frame_ms,
                cpu_ms: 2.0,
                draw_calls: 40,
                triangles: 1000,
                phases: {
                    let mut p = PhaseTimes::default();
                    p.add(Phase::Encode, 0.5);
                    p
                },
            });
        }
        let r = run.result();
        assert_eq!(r.max, 10.0);
        assert_eq!(r.cpu_p99, 2.0);
        assert_eq!(r.cpu_phases_mean["encode"], 0.5);
        assert_eq!(r.cpu_phases_mean.len(), crate::profile::PHASE_COUNT);
        assert!(r.framings.iter().all(|f| f.frames > 0));
        assert!((r.seconds - total).abs() < 0.1);
    }

    #[test]
    fn percentile_is_nearest_rank() {
        assert_eq!(percentile(&[5.0, 1.0, 3.0, 2.0, 4.0], 50.0), 3.0);
        assert_eq!(percentile(&[], 99.0), 0.0);
        let mut ring = FrameRing::new(4);
        for v in [100.0, 100.0, 100.0, 100.0, 1.0, 2.0, 3.0, 4.0] {
            ring.push(v);
        }
        assert_eq!(ring.percentile(100.0), Some(4.0));
    }
}
