//! What the hands do, as two layers of keyframed offsets.
//!
//! The browser's `viewclips.ts`, reading the **same** `models/viewclips.json` —
//! compiled in with `include_str!` rather than retyped, because two hand-typed
//! copies of forty keyframes drift on the very first tweak.
//!
//! ## Offsets, not positions
//!
//! Every pose is an offset from the weapon's grip anchor (`arms.rs`), in the
//! weapon's own space. That is what makes a partial keyframe legal: a track that
//! says nothing about a hand leaves it on the gun, so `reload` is written
//! entirely in terms of the support hand and never mentions the trigger hand it
//! is not moving.
//!
//! ## Two layers, and why they must be channel-disjoint
//!
//! Locomotion is the base layer — looping, phase-driven off the view model's own
//! bob so it cannot drift out of step with it. Actions are the upper layer, and
//! they **replace** the channels they name rather than blending. `clips.rs`
//! already makes that argument for the third-person rig: two poses averaged on
//! one bone give you half a reload, a motion belonging to neither animation.
//!
//! ## `t` is a fraction, never seconds
//!
//! The reload track stretches to whatever `reloadTime` the server served — the
//! same argument `RELOAD_DIP_IN` makes, and the reason one authored motion
//! serves a 1.4s pistol and a 2.6s shotgun.

use std::sync::OnceLock;

use glam::Vec3;

const CLIPS_JSON: &str =
    include_str!("../../../packages/core/src/modules/hassault/models/viewclips.json");

/// One frame of hand motion. Every channel is optional: absent means "leave that
/// hand where the grip anchor put it".
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct ArmPose {
    pub primary: Option<Vec3>,
    pub support: Option<Vec3>,
    pub primary_roll: Option<f32>,
    pub support_roll: Option<f32>,
}

impl ArmPose {
    /// Lay an action over a locomotion pose.
    ///
    /// **Replace, not blend** — see the module header.
    pub fn merged_with(self, action: ArmPose) -> ArmPose {
        ArmPose {
            primary: action.primary.or(self.primary),
            support: action.support.or(self.support),
            primary_roll: action.primary_roll.or(self.primary_roll),
            support_roll: action.support_roll.or(self.support_roll),
        }
    }

    /// Scale every channel. Used to fade a walk cycle in with the bob.
    pub fn scaled(self, k: f32) -> ArmPose {
        ArmPose {
            primary: self.primary.map(|v| v * k),
            support: self.support.map(|v| v * k),
            primary_roll: self.primary_roll.map(|r| r * k),
            support_roll: self.support_roll.map(|r| r * k),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Locomotion {
    Idle,
    Walk,
    Run,
    Jump,
    Land,
}

impl Locomotion {
    fn key(self) -> &'static str {
        match self {
            Locomotion::Idle => "idle",
            Locomotion::Walk => "walk",
            Locomotion::Run => "run",
            Locomotion::Jump => "jump",
            Locomotion::Land => "land",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    Reload,
    Inspect,
    Throw,
    Draw,
}

impl Action {
    fn key(self) -> &'static str {
        match self {
            Action::Reload => "reload",
            Action::Inspect => "inspect",
            Action::Throw => "throw",
            Action::Draw => "draw",
        }
    }
}

/// How long the land dip lasts, in seconds. Short — it is a jolt, not a pose.
pub const LAND_DURATION: f32 = 0.22;
/// How long a draw takes. Matches the view model's own `DRAW_TIME`.
pub const DRAW_DURATION: f32 = 0.25;
/// The grenade throw's three phases, end to end.
pub const THROW_DURATION: f32 = 0.55;

#[derive(Debug, Clone, Copy)]
struct Keyframe {
    t: f32,
    pose: ArmPose,
}

type Track = Vec<Keyframe>;

struct Clips {
    locomotion: std::collections::HashMap<String, Track>,
    actions: std::collections::HashMap<String, Track>,
}

fn parse_pose(value: &serde_json::Value) -> ArmPose {
    let vec3 = |key: &str| -> Option<Vec3> {
        let a = value.get(key)?.as_array()?;
        Some(Vec3::new(
            a.first()?.as_f64()? as f32,
            a.get(1)?.as_f64()? as f32,
            a.get(2)?.as_f64()? as f32,
        ))
    };
    ArmPose {
        primary: vec3("primary"),
        support: vec3("support"),
        primary_roll: value
            .get("primaryRoll")
            .and_then(|v| v.as_f64())
            .map(|v| v as f32),
        support_roll: value
            .get("supportRoll")
            .and_then(|v| v.as_f64())
            .map(|v| v as f32),
    }
}

fn parse_tracks(value: &serde_json::Value) -> std::collections::HashMap<String, Track> {
    let mut out = std::collections::HashMap::new();
    let Some(map) = value.as_object() else {
        return out;
    };
    for (name, track) in map {
        // The file documents itself in `_comment` keys, which are not clips.
        if name.starts_with('_') {
            continue;
        }
        let Some(frames) = track.as_array() else {
            continue;
        };
        out.insert(
            name.clone(),
            frames
                .iter()
                .map(|f| Keyframe {
                    t: f["t"].as_f64().unwrap_or(0.0) as f32,
                    pose: parse_pose(&f["pose"]),
                })
                .collect(),
        );
    }
    out
}

fn clips() -> &'static Clips {
    static CLIPS: OnceLock<Clips> = OnceLock::new();
    CLIPS.get_or_init(|| {
        let file: serde_json::Value = serde_json::from_str(CLIPS_JSON).unwrap_or_default();
        Clips {
            locomotion: parse_tracks(&file["locomotion"]),
            actions: parse_tracks(&file["actions"]),
        }
    })
}

fn lerp_opt(a: Option<Vec3>, b: Option<Vec3>, k: f32) -> Option<Vec3> {
    match (a, b) {
        (None, None) => None,
        // A channel present in only one keyframe interpolates against zero,
        // since zero *is* the grip anchor.
        (from, to) => {
            let from = from.unwrap_or(Vec3::ZERO);
            Some(from + (to.unwrap_or(Vec3::ZERO) - from) * k)
        }
    }
}

fn lerp_opt_f32(a: Option<f32>, b: Option<f32>, k: f32) -> Option<f32> {
    match (a, b) {
        (None, None) => None,
        (from, to) => {
            let from = from.unwrap_or(0.0);
            Some(from + (to.unwrap_or(0.0) - from) * k)
        }
    }
}

/// Sample one track at a normalised time.
///
/// A channel absent from **both** surrounding keyframes stays absent — that is
/// the whole of "a partial track leaves that hand alone".
fn sample(track: &Track, t: f32) -> ArmPose {
    if track.is_empty() {
        return ArmPose::default();
    }
    let t = t.clamp(0.0, 1.0);
    if track.len() == 1 || t <= track[0].t {
        return track[0].pose;
    }
    let last = track[track.len() - 1];
    if t >= last.t {
        return last.pose;
    }
    let mut i = 0;
    while i < track.len() - 2 && track[i + 1].t < t {
        i += 1;
    }
    let (a, b) = (track[i], track[i + 1]);
    let span = b.t - a.t;
    let k = if span <= 1e-9 { 0.0 } else { (t - a.t) / span };
    ArmPose {
        primary: lerp_opt(a.pose.primary, b.pose.primary, k),
        support: lerp_opt(a.pose.support, b.pose.support, k),
        primary_roll: lerp_opt_f32(a.pose.primary_roll, b.pose.primary_roll, k),
        support_roll: lerp_opt_f32(a.pose.support_roll, b.pose.support_roll, k),
    }
}

/// The looping base layer, at a phase. Wrapped rather than clamped: a phase that
/// ran past 1 would freeze the cycle at its last keyframe.
pub fn locomotion_pose(clip: Locomotion, phase: f32) -> ArmPose {
    match clips().locomotion.get(clip.key()) {
        Some(track) => sample(track, phase - phase.floor()),
        None => ArmPose::default(),
    }
}

/// The one-shot upper layer, at a fraction of its own length.
pub fn action_pose(clip: Action, t: f32) -> ArmPose {
    match clips().actions.get(clip.key()) {
        Some(track) => sample(track, t),
        None => ArmPose::default(),
    }
}

/// Which locomotion clip this frame wants.
///
/// Airborne beats everything: a walk cycle in mid-air reads as a bug — the same
/// call the view model already makes about the bob.
pub fn select_locomotion(
    speed: f32,
    move_speed: f32,
    on_ground: bool,
    since_landed: f32,
) -> Locomotion {
    if !on_ground {
        return Locomotion::Jump;
    }
    if since_landed < LAND_DURATION {
        return Locomotion::Land;
    }
    let fraction = if move_speed > 0.0 {
        speed / move_speed
    } else {
        0.0
    };
    if fraction < 0.15 {
        Locomotion::Idle
    } else if fraction < 0.7 {
        Locomotion::Walk
    } else {
        Locomotion::Run
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const LOCOMOTIONS: [Locomotion; 5] = [
        Locomotion::Idle,
        Locomotion::Walk,
        Locomotion::Run,
        Locomotion::Jump,
        Locomotion::Land,
    ];
    const ACTIONS: [Action; 4] = [Action::Reload, Action::Inspect, Action::Throw, Action::Draw];

    #[test]
    fn every_clip_the_file_promises_is_actually_there() {
        // A missing track samples to "no offset", which is an animation that
        // silently does nothing rather than an error.
        for clip in LOCOMOTIONS {
            assert!(
                clips().locomotion.contains_key(clip.key()),
                "no {} track",
                clip.key()
            );
        }
        for clip in ACTIONS {
            assert!(
                clips().actions.contains_key(clip.key()),
                "no {} track",
                clip.key()
            );
        }
    }

    #[test]
    fn every_locomotion_clip_loops_continuously() {
        // `t = 0` and `t = 1` must be the same pose, or the cycle ticks once per
        // stride — visible, but easy to mistake for the bob.
        for clip in LOCOMOTIONS {
            assert_eq!(
                locomotion_pose(clip, 0.0),
                locomotion_pose(clip, 1.0),
                "{} does not loop",
                clip.key()
            );
        }
    }

    #[test]
    fn every_action_ends_at_rest() {
        // An action that ends somewhere other than the grip leaves the hand
        // there until the next one runs.
        for clip in ACTIONS {
            assert_eq!(
                action_pose(clip, 1.0),
                ArmPose::default(),
                "{} does not end at rest",
                clip.key()
            );
        }
    }

    #[test]
    fn an_action_replaces_only_the_channels_it_names() {
        let walking = ArmPose {
            primary: Some(Vec3::ONE),
            support: Some(Vec3::splat(2.0)),
            ..ArmPose::default()
        };
        let reloading = ArmPose {
            support: Some(Vec3::splat(9.0)),
            ..ArmPose::default()
        };
        let merged = walking.merged_with(reloading);
        assert_eq!(merged.primary, Some(Vec3::ONE));
        assert_eq!(merged.support, Some(Vec3::splat(9.0)));
    }

    #[test]
    fn airborne_beats_everything() {
        assert_eq!(select_locomotion(20.0, 20.0, false, 99.0), Locomotion::Jump);
    }

    #[test]
    fn the_landing_dip_plays_and_then_stops() {
        assert_eq!(select_locomotion(0.0, 20.0, true, 0.0), Locomotion::Land);
        assert_eq!(
            select_locomotion(0.0, 20.0, true, LAND_DURATION + 0.01),
            Locomotion::Idle
        );
    }

    #[test]
    fn speed_picks_the_gait() {
        assert_eq!(select_locomotion(0.0, 20.0, true, 99.0), Locomotion::Idle);
        assert_eq!(select_locomotion(8.0, 20.0, true, 99.0), Locomotion::Walk);
        assert_eq!(select_locomotion(20.0, 20.0, true, 99.0), Locomotion::Run);
    }
}
