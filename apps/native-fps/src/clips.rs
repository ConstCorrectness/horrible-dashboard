//! Which animation an operator should be playing, given what the server says.
//!
//! A line-for-line port of `packages/core/src/modules/hassault/models/clips.ts`.
//! The character now exists **twice** — once in the browser pane and once here —
//! for the same reason the physics does: the native client is not a second view
//! of the web client, it is a second client. The duplication is made safe the
//! same way, with vectors both suites replay
//! (`packages/core/src/modules/hassault/__tests__/clip-vectors.json`); the
//! fixture pins *agreement*, each side's unit tests pin correctness.
//!
//! Deliberately free of `wgpu`, `gltf` and every other renderer type, so
//! choosing a clip is unit tested headless rather than eyeballed in a match.

/// The 23 clips baked into `hassault-operator.glb`.
///
/// Named exactly as the GLB names them, because that is the key the asset is
/// looked up by — `scripts/build_hassault_character.mjs` derives them from the
/// Mixamo filenames, and a rename on either side is a clip that silently
/// resolves to `None` and a character that stops animating.
pub const OPERATOR_CLIPS: [&str; 23] = [
    "crouch_to_standing_with_rifle",
    "crouch_walking",
    "crouched_to_standing",
    "death_from_back_headshot",
    "death_from_the_front",
    "dying",
    "firing_rifle",
    "injured_run",
    "reloading",
    "rifle_aiming_idle",
    "rifle_crouch_walk_to_kneel",
    "rifle_side_step",
    "rifle_turn",
    "rifle_walk",
    "right_strafe_walking",
    "running_jump",
    "running_up_stairs",
    "standard_walk",
    "strafing",
    "turn_90_left",
    "walk_crouching_forward",
    "walk_forward_right",
    "walking_backwards",
];

/// The bones an upper-body action is allowed to drive.
///
/// Firing and reloading have to layer over whatever the legs are doing, because
/// standing still to reload is not how anyone plays. Two layers writing the same
/// bone would have to be averaged, and a fire animation averaged with a walk
/// comes out as a half-shrug — so the layering works by making the bone sets
/// *disjoint*: the action drives these, locomotion drives everything else, and
/// neither has an opinion about the other's bones.
const UPPER_BODY_BONES: [&str; 13] = [
    "Spine1",
    "Spine2",
    "Neck",
    "Head",
    "HeadTop_End",
    "LeftShoulder",
    "LeftArm",
    "LeftForeArm",
    "LeftHand",
    "RightShoulder",
    "RightArm",
    "RightForeArm",
    "RightHand",
];

/// Strip Mixamo's `mixamorig` prefix and any `_N` uniquifier glTF export added.
///
/// glTF sanitises `mixamorig:Hips` to `mixamorig_Hips` or `mixamorigHips`
/// depending on the exporter, and appends `_1`, `_2` where a name collides
/// across the nine skins. Matching on the raw node name therefore misses bones
/// on some exports and not others — which reads as "the rig is inconsistent"
/// rather than as a string bug.
pub fn bone_key(name: &str) -> &str {
    let node = name.split('.').next().unwrap_or(name);
    let stripped = node
        .strip_prefix("mixamorig:")
        .or_else(|| node.strip_prefix("mixamorig_"))
        .or_else(|| node.strip_prefix("mixamorig"))
        .unwrap_or(node);
    // Trim a trailing `_<digits>`, but only when the whole tail is digits —
    // `HeadTop_End` must survive this untouched.
    match stripped.rsplit_once('_') {
        Some((head, tail)) if !tail.is_empty() && tail.bytes().all(|b| b.is_ascii_digit()) => head,
        _ => stripped,
    }
}

/// Whether a bone belongs to the upper body an action clip may drive.
pub fn is_upper_body(name: &str) -> bool {
    let key = bone_key(name);
    UPPER_BODY_BONES.contains(&key) || key.starts_with("LeftHand") || key.starts_with("RightHand")
}

/// What the renderer knows about a player this frame.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct OperatorState {
    pub alive: bool,
    /// Standing on something.
    pub ground: bool,
    /// 0 standing, 1 fully crouched.
    pub crouch: f32,
    /// Ground speed in cubes/second.
    pub speed: f32,
    /// Velocity along the way the player is facing, -1 (backwards) to 1.
    pub forward: f32,
    /// Velocity across the way the player is facing, -1 (left) to 1.
    pub strafe: f32,
    /// Wounded enough that the limp reads as information, not decoration.
    pub hurt: bool,
}

/// Below this, a player is standing still rather than walking slowly.
///
/// Interpolated remote positions jitter by a fraction of a cube even at rest,
/// and without a floor that jitter drives the walk cycle — an idle enemy
/// shuffling on the spot, which reads as movement and is worse than none.
pub const IDLE_SPEED: f32 = 0.6;

/// Above this, the run cycle rather than the walk cycle.
pub const RUN_SPEED: f32 = 9.0;

/// The locomotion clip for a state — the full-body base layer.
///
/// Death is checked first and jump second because both override direction
/// entirely: a player killed mid-strafe is dying, not strafing.
pub fn select_locomotion(state: &OperatorState) -> &'static str {
    if !state.alive {
        return "death_from_the_front";
    }
    if !state.ground {
        return "running_jump";
    }

    let crouched = state.crouch > 0.5;
    let moving = state.speed > IDLE_SPEED;

    if crouched {
        // There is no crouched idle in the set, so the crouch-walk cycle stands
        // in; held at weight it still reads as a braced low posture.
        return if moving {
            "crouch_walking"
        } else {
            "rifle_crouch_walk_to_kneel"
        };
    }

    if !moving {
        return "rifle_aiming_idle";
    }

    // Direction is decided by whichever axis dominates, not by blending: two
    // walk cycles at half weight each land both feet in the wrong place,
    // whereas one cycle played whole keeps its foot contacts.
    if state.strafe.abs() > state.forward.abs() {
        return if state.strafe > 0.0 {
            "right_strafe_walking"
        } else {
            "strafing"
        };
    }
    if state.forward < 0.0 {
        return "walking_backwards";
    }
    if state.hurt && state.speed > RUN_SPEED {
        return "injured_run";
    }
    if state.speed > RUN_SPEED {
        "rifle_walk"
    } else {
        "standard_walk"
    }
}

/// The death animation for how a player was killed.
pub fn select_death(headshot: bool, from_behind: bool) -> &'static str {
    if headshot && from_behind {
        return "death_from_back_headshot";
    }
    if headshot {
        "death_from_the_front"
    } else {
        "dying"
    }
}

/// How long to crossfade into a clip, in seconds.
///
/// Death does not fade — a body snapping from a walk into a slump is the point,
/// and easing it makes the kill feel unacknowledged. Everything else fades fast
/// enough not to feel like a transition and slow enough not to pop.
pub fn fade_for(clip: &str) -> f32 {
    if clip == "dying" || clip.starts_with("death_") {
        return 0.05;
    }
    if clip == "running_jump" {
        return 0.08;
    }
    0.18
}

/// Clips that play once and hold their last frame rather than looping.
pub fn is_one_shot(clip: &str) -> bool {
    clip.starts_with("death_")
        || clip == "dying"
        || clip == "reloading"
        || clip == "firing_rifle"
        || clip == "running_jump"
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The vectors the browser client replays too.
    ///
    /// `include_str!` rather than a runtime read: the path is resolved at
    /// compile time, so a fixture that moves is a build error rather than a test
    /// that quietly stops running. It is deliberately the *same file* the
    /// TypeScript suite names — a copy under `apps/native-fps` would be two
    /// fixtures that agree until one of them is edited.
    const VECTORS: &str =
        include_str!("../../../packages/core/src/modules/hassault/__tests__/clip-vectors.json");

    fn vectors() -> serde_json::Value {
        serde_json::from_str(VECTORS).expect("clip-vectors.json should parse")
    }

    fn state_from(value: &serde_json::Value) -> OperatorState {
        OperatorState {
            alive: value["alive"].as_bool().expect("alive"),
            ground: value["ground"].as_bool().expect("ground"),
            crouch: value["crouch"].as_f64().expect("crouch") as f32,
            speed: value["speed"].as_f64().expect("speed") as f32,
            forward: value["forward"].as_f64().expect("forward") as f32,
            strafe: value["strafe"].as_f64().expect("strafe") as f32,
            hurt: value["hurt"].as_bool().expect("hurt"),
        }
    }

    #[test]
    fn locomotion_matches_the_browser_client() {
        let vectors = vectors();
        let cases = vectors["locomotion"].as_array().expect("locomotion");
        assert!(cases.len() >= 19, "the fixture has lost cases");
        for case in cases {
            let state = state_from(&case["state"]);
            let expected = case["clip"].as_str().expect("clip");
            let why = case["why"].as_str().unwrap_or("");
            assert_eq!(
                select_locomotion(&state),
                expected,
                "state {state:?} should select {expected} ({why})"
            );
        }
    }

    #[test]
    fn death_selection_matches_the_browser_client() {
        let vectors = vectors();
        for case in vectors["death"].as_array().expect("death") {
            let headshot = case["headshot"].as_bool().expect("headshot");
            let behind = case["fromBehind"].as_bool().expect("fromBehind");
            assert_eq!(
                select_death(headshot, behind),
                case["clip"].as_str().expect("clip"),
                "headshot={headshot} fromBehind={behind}"
            );
        }
    }

    #[test]
    fn fades_match_the_browser_client() {
        let vectors = vectors();
        for case in vectors["fade"].as_array().expect("fade") {
            let clip = case["clip"].as_str().expect("clip");
            let seconds = case["seconds"].as_f64().expect("seconds") as f32;
            assert!(
                (fade_for(clip) - seconds).abs() < 1e-6,
                "{clip} fades over {} here and {seconds} there",
                fade_for(clip)
            );
        }
    }

    #[test]
    fn one_shots_match_the_browser_client() {
        let vectors = vectors();
        for case in vectors["oneShot"].as_array().expect("oneShot") {
            let clip = case["clip"].as_str().expect("clip");
            assert_eq!(
                is_one_shot(clip),
                case["once"].as_bool().expect("once"),
                "{clip}"
            );
        }
    }

    #[test]
    fn bone_names_sanitise_the_same_way() {
        // The one with real teeth: glTF export writes the prefix three different
        // ways and uniquifies collisions, so a client that strips it differently
        // silently loses the aim pitch and the upper-body layer on some bones.
        let vectors = vectors();
        for case in vectors["boneKeys"].as_array().expect("boneKeys") {
            let name = case["name"].as_str().expect("name");
            assert_eq!(bone_key(name), case["key"].as_str().expect("key"), "{name}");
            assert_eq!(
                is_upper_body(name),
                case["upper"].as_bool().expect("upper"),
                "{name}"
            );
        }
    }

    #[test]
    fn the_clip_list_matches_the_manifest() {
        // `clips.json` is the build script's manifest and the browser's union is
        // checked against it; this is the third copy, so it gets the same check.
        let manifest: serde_json::Value = serde_json::from_str(include_str!(
            "../../../packages/core/src/modules/hassault/models/clips.json"
        ))
        .expect("clips.json should parse");
        let names: Vec<&str> = manifest["clips"]
            .as_array()
            .map(|entries| {
                entries
                    .iter()
                    .filter_map(|e| e.get("name").and_then(|n| n.as_str()))
                    .collect()
            })
            .unwrap_or_default();
        if names.is_empty() {
            // The manifest's shape is the build script's business, not ours. If
            // it stops being a list of named entries, say so rather than passing
            // an assertion over an empty set.
            panic!("clips.json is not a list of named clips any more");
        }
        for name in &names {
            assert!(
                OPERATOR_CLIPS.contains(name),
                "{name} is in the manifest but not in OPERATOR_CLIPS"
            );
        }
        assert_eq!(names.len(), OPERATOR_CLIPS.len());
    }
}
