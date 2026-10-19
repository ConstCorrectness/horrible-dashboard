//! Aim drills on the training range: replaying a served schedule, and scoring.
//!
//! A port of `packages/core/src/modules/hassault/training-drill.ts`, for the
//! reason that file gives. The decisions are the server's
//! (`backend/modules/hassault/drills.py`): when a target turns back, when a
//! pop-up shows. What a target *does* with an input is this crate's own
//! `physics::step`, one fixed tick at a time, so it moves exactly like a player
//! because it runs the same legs. `tests/drills.rs` pins both halves against
//! `drill-vectors.json`: the replayed positions, and `summarise`.

use serde::Deserialize;

use crate::physics::{create_player, step, MoveInput, PlayerState};
use crate::world::World;

/// One input segment, `[tick, strafe, crouch, jump, visible]`, in force from
/// that tick on.
pub type DrillSegment = [i32; 5];

#[derive(Debug, Clone, Deserialize)]
pub struct DrillTargetSpec {
    pub id: String,
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub yaw: f32,
    pub segments: Vec<DrillSegment>,
}

#[derive(Debug, Clone, Copy, Deserialize)]
pub struct DrillShooter {
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub yaw: f32,
}

/// A drill expanded for one run: `GET /api/hassault/drills/{id}`.
///
/// **`camelCase` renames on every compound name.** Read without them, `tickRate`
/// deserializes to zero and the drill never advances a tick, with nothing
/// anywhere saying why: the `reloadTime` trap in `api.rs`.
#[derive(Debug, Clone, Deserialize)]
pub struct DrillSpec {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub description: String,
    pub map: String,
    pub seconds: f32,
    pub hp: f32,
    #[serde(rename = "headOnly", default)]
    pub head_only: bool,
    #[serde(default = "default_respawn")]
    pub respawn: f32,
    #[serde(rename = "tickRate")]
    pub tick_rate: u32,
    pub ticks: u32,
    #[serde(default)]
    pub shooter: Option<DrillShooter>,
    pub targets: Vec<DrillTargetSpec>,
}

fn default_respawn() -> f32 {
    1.0
}

/// One target being replayed.
#[derive(Debug, Clone)]
pub struct DrillMover {
    pub state: PlayerState,
    segments: Vec<DrillSegment>,
    seg: usize,
    tick: i32,
    tick_dt: f32,
}

impl DrillMover {
    pub fn new(spec: &DrillTargetSpec, tick_dt: f32) -> DrillMover {
        let mut state = create_player(spec.x, spec.y, spec.z, spec.yaw);
        // `drills._stand` places the body on the ground, as `spawn_at` does. A
        // fresh `create_player` is airborne, and the first replayed step would
        // then be a fall rather than a strafe.
        state.on_ground = true;
        DrillMover {
            state,
            segments: spec.segments.clone(),
            seg: 0,
            tick: 0,
            tick_dt,
        }
    }

    fn current(&self) -> DrillSegment {
        self.segments
            .get(self.seg)
            .copied()
            .unwrap_or([0, 0, 0, 0, 1])
    }

    /// Whether the schedule shows this target right now.
    pub fn visible(&self) -> bool {
        self.current()[4] != 0
    }

    /// Run one fixed tick: pick the input for this tick, then step.
    pub fn advance(&mut self, world: &World) {
        while self.seg + 1 < self.segments.len() && self.segments[self.seg + 1][0] <= self.tick {
            self.seg += 1;
        }
        let [_, strafe, crouch, jump, _] = self.current();
        let input = MoveInput {
            strafe: strafe as f32,
            crouch: crouch != 0,
            jump: jump != 0,
            ..Default::default()
        };
        step(world, &mut self.state, &input, self.tick_dt);
        self.tick += 1;
    }
}

// ---- scoring ---------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct DrillHit {
    pub target: String,
    pub head: bool,
    pub killed: bool,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum DrillEvent {
    Appear { t: f64, target: String },
    Hide { t: f64, target: String },
    Shot { t: f64, hits: Vec<DrillHit> },
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct DrillStats {
    pub shots: u32,
    pub hit_shots: u32,
    pub accuracy: f64,
    pub hits: u32,
    pub head_hits: u32,
    pub headshot_share: f64,
    pub kills: u32,
    pub kills_per_minute: f64,
    pub median_ttk: Option<f64>,
    pub median_reaction: Option<f64>,
}

fn median(values: &mut [f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    values.sort_by(|a, b| a.total_cmp(b));
    let mid = values.len() / 2;
    Some(if values.len() % 2 == 1 {
        values[mid]
    } else {
        (values[mid - 1] + values[mid]) / 2.0
    })
}

/// Score one run from its event log. A port of `drills.summarise`; see its
/// docstring for what each number means. Times are `f64` so the medians agree
/// with Python's to the last digit the fixture carries.
pub fn summarise(events: &[DrillEvent], seconds: f64) -> DrillStats {
    use std::collections::HashMap;

    let mut stats = DrillStats::default();
    let mut shown_at: HashMap<&str, f64> = HashMap::new();
    let mut first_hit: HashMap<&str, f64> = HashMap::new();
    let mut ttks = Vec::new();
    let mut reactions = Vec::new();
    for event in events {
        match event {
            DrillEvent::Appear { t, target } => {
                shown_at.insert(target, *t);
                first_hit.remove(target.as_str());
            }
            DrillEvent::Hide { target, .. } => {
                shown_at.remove(target.as_str());
                first_hit.remove(target.as_str());
            }
            DrillEvent::Shot { t, hits } => {
                stats.shots += 1;
                if !hits.is_empty() {
                    stats.hit_shots += 1;
                }
                for hit in hits {
                    let target = hit.target.as_str();
                    stats.hits += 1;
                    if hit.head {
                        stats.head_hits += 1;
                    }
                    if let Some(shown) = shown_at.get(target) {
                        if !first_hit.contains_key(target) {
                            first_hit.insert(target, *t);
                            reactions.push(t - shown);
                        }
                    }
                    if hit.killed {
                        stats.kills += 1;
                        if let Some(first) = first_hit.get(target) {
                            ttks.push(t - first);
                        }
                        shown_at.remove(target);
                        first_hit.remove(target);
                    }
                }
            }
        }
    }
    let ratio = |a: u32, b: u32| if b == 0 { 0.0 } else { a as f64 / b as f64 };
    stats.accuracy = ratio(stats.hit_shots, stats.shots);
    stats.headshot_share = ratio(stats.head_hits, stats.hits);
    stats.kills_per_minute = if seconds > 0.0 {
        stats.kills as f64 * 60.0 / seconds
    } else {
        0.0
    };
    stats.median_ttk = median(&mut ttks);
    stats.median_reaction = median(&mut reactions);
    stats
}
