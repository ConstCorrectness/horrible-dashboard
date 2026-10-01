//! The inspect choreographies, sampled.
//!
//! The browser's `inspects.ts`, reading the same `models/inspects.json`
//! (`include_str!`). Both are held to `models/inspects.golden.json`, which the
//! reference sampler in `tools/blender/author_inspects.py` writes — so the two
//! clients cannot drift apart without a test saying so. What every channel
//! means is documented once, in that script.

use std::collections::HashMap;
use std::sync::OnceLock;

use glam::Vec3;
use serde_json::Value;

pub const INSPECTS_JSON: &str =
    include_str!("../../../packages/core/src/modules/hassault/models/inspects.json");

/// The clip a weapon with no clip of its own falls back to.
const FALLBACK: &str = "assault";

#[derive(Debug, Clone, Default)]
struct Pose {
    primary: Option<Vec3>,
    support: Option<Vec3>,
    primary_fingers: Option<[f32; 5]>,
    support_fingers: Option<[f32; 5]>,
}

#[derive(Debug, Clone)]
struct Key {
    t: f32,
    pos: Vec3,
    rot: Vec3,
    spin: f32,
    pose: Pose,
    nodes: HashMap<String, Vec3>,
}

#[derive(Debug, Clone)]
pub struct Clip {
    pub duration: f32,
    /// Where a repeated press jumps back to, as a fraction of the clip.
    pub repeat_from: Option<f32>,
    /// The prop node `spin` turns about; the prop's own origin when `None`.
    pub spin_pivot: Option<String>,
    /// The axis `spin` turns about, in weapon space.
    pub spin_axis: Vec3,
    keys: Vec<Key>,
}

/// A finger channel and how much of it to apply over the grip's own curl.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Fingers {
    pub curl: [f32; 5],
    pub weight: f32,
}

impl Fingers {
    pub fn over(sample: Option<Fingers>, grip: [f32; 5]) -> [f32; 5] {
        let Some(s) = sample else { return grip };
        let mut out = grip;
        for i in 0..5 {
            out[i] = grip[i] + (s.curl[i] - grip[i]) * s.weight;
        }
        out
    }
}

#[derive(Debug, Clone, Default)]
pub struct InspectSample {
    /// The whole weapon on the pivot, faded to zero at both ends.
    pub pos: Vec3,
    pub rot: Vec3,
    /// The weapon alone about the clip's spin axis. Not faded.
    pub spin: f32,
    /// Hand offsets from the grip; absent is zero.
    pub primary: Vec3,
    pub support: Vec3,
    pub primary_fingers: Option<Fingers>,
    pub support_fingers: Option<Fingers>,
    /// Named prop parts, rotated about their own origins (XYZ Euler).
    pub nodes: HashMap<String, Vec3>,
}

fn vec3(v: &Value) -> Option<Vec3> {
    let a = v.as_array()?;
    Some(Vec3::new(
        a.first()?.as_f64()? as f32,
        a.get(1)?.as_f64()? as f32,
        a.get(2)?.as_f64()? as f32,
    ))
}

fn curl(v: &Value) -> Option<[f32; 5]> {
    let a = v.as_array()?;
    let mut out = [0.0; 5];
    for (i, slot) in out.iter_mut().enumerate() {
        *slot = a.get(i)?.as_f64()? as f32;
    }
    Some(out)
}

fn parse_clip(v: &Value) -> Option<Clip> {
    let keys = v
        .get("keys")?
        .as_array()?
        .iter()
        .map(|k| {
            let pose = &k["pose"];
            Key {
                t: k["t"].as_f64().unwrap_or(0.0) as f32,
                pos: vec3(&k["pos"]).unwrap_or(Vec3::ZERO),
                rot: vec3(&k["rot"]).unwrap_or(Vec3::ZERO),
                spin: k["spin"].as_f64().unwrap_or(0.0) as f32,
                pose: Pose {
                    primary: vec3(&pose["primary"]),
                    support: vec3(&pose["support"]),
                    primary_fingers: curl(&pose["primaryFingers"]),
                    support_fingers: curl(&pose["supportFingers"]),
                },
                nodes: k["nodes"]
                    .as_object()
                    .map(|m| {
                        m.iter()
                            .filter_map(|(name, n)| Some((name.clone(), vec3(&n["rot"])?)))
                            .collect()
                    })
                    .unwrap_or_default(),
            }
        })
        .collect::<Vec<_>>();
    if keys.len() < 2 {
        return None;
    }
    Some(Clip {
        duration: v["duration"].as_f64().unwrap_or(1.5) as f32,
        repeat_from: v["repeatFrom"].as_f64().map(|r| r as f32),
        spin_pivot: v["spinPivot"].as_str().map(str::to_string),
        spin_axis: vec3(&v["spinAxis"]).unwrap_or(Vec3::X),
        keys,
    })
}

/// Every clip, parsed once. The file is compiled in, so this cannot fail at
/// runtime — but an empty map rather than a panic keeps a malformed constant
/// from taking the renderer down.
fn clips() -> &'static HashMap<String, Clip> {
    static CLIPS: OnceLock<HashMap<String, Clip>> = OnceLock::new();
    CLIPS.get_or_init(|| {
        let file: Value = serde_json::from_str(INSPECTS_JSON).unwrap_or_default();
        file.as_object()
            .map(|m| {
                m.iter()
                    .filter(|(k, _)| !k.starts_with('_'))
                    .filter_map(|(k, v)| Some((k.clone(), parse_clip(v)?)))
                    .collect()
            })
            .unwrap_or_default()
    })
}

pub fn clip(id: &str) -> Option<&'static Clip> {
    clips().get(id).or_else(|| clips().get(FALLBACK))
}

pub fn clip_ids() -> impl Iterator<Item = &'static str> {
    clips().keys().map(String::as_str)
}

pub fn catmull_rom(p0: f32, p1: f32, p2: f32, p3: f32, u: f32) -> f32 {
    0.5 * ((2.0 * p1)
        + (-p0 + p2) * u
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * (u * u)
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * (u * u * u))
}

pub fn smootherstep(u: f32) -> f32 {
    let x = u.clamp(0.0, 1.0);
    x * x * x * (x * (x * 6.0 - 15.0) + 10.0)
}

fn round5(v: f32) -> f32 {
    (v * 1e5).round() / 1e5
}

fn fingers(a: Option<[f32; 5]>, b: Option<[f32; 5]>, u: f32) -> Option<Fingers> {
    match (a, b) {
        (Some(a), Some(b)) => {
            let mut curl = a;
            for i in 0..5 {
                curl[i] = a[i] + (b[i] - a[i]) * u;
            }
            Some(Fingers { curl, weight: 1.0 })
        }
        // One side has none: hold the side that does and fade its weight.
        (Some(a), None) => Some(Fingers {
            curl: a,
            weight: 1.0 - u,
        }),
        (None, Some(b)) => Some(Fingers { curl: b, weight: u }),
        (None, None) => None,
    }
}

/// Sample a clip at `t_norm`, a fraction of its duration.
pub fn sample_inspect(clip_id: &str, t_norm: f32) -> InspectSample {
    let Some(clip) = clip(clip_id) else {
        return InspectSample::default();
    };
    let keys = &clip.keys;
    let t = t_norm.clamp(0.0, 1.0);
    let mut idx = 0;
    while idx < keys.len() - 2 && keys[idx + 1].t < t {
        idx += 1;
    }
    let (k1, k2) = (&keys[idx], &keys[idx + 1]);
    let k0 = &keys[idx.saturating_sub(1)];
    let k3 = &keys[(idx + 2).min(keys.len() - 1)];
    let span = k2.t - k1.t;
    let u = if span <= 1e-9 {
        0.0
    } else {
        ((t - k1.t) / span).clamp(0.0, 1.0)
    };

    let fade = if t < 0.05 {
        smootherstep(t / 0.05)
    } else if t > 0.95 {
        smootherstep((1.0 - t) / 0.05)
    } else {
        1.0
    };
    let curve3 = |pick: fn(&Key) -> Vec3| {
        let (a, b, c, d) = (pick(k0), pick(k1), pick(k2), pick(k3));
        Vec3::new(
            catmull_rom(a.x, b.x, c.x, d.x, u),
            catmull_rom(a.y, b.y, c.y, d.y, u),
            catmull_rom(a.z, b.z, c.z, d.z, u),
        )
    };
    let round3 = |v: Vec3| Vec3::new(round5(v.x), round5(v.y), round5(v.z));
    let pos = round3(curve3(|k| k.pos) * fade);
    let rot = round3(curve3(|k| k.rot) * fade);
    let spin = round5(catmull_rom(k0.spin, k1.spin, k2.spin, k3.spin, u));

    let lerp = |a: Option<Vec3>, b: Option<Vec3>| {
        let a = a.unwrap_or(Vec3::ZERO);
        a + (b.unwrap_or(Vec3::ZERO) - a) * u
    };
    let mut nodes = HashMap::new();
    for name in k1.nodes.keys().chain(k2.nodes.keys()) {
        if nodes.contains_key(name) {
            continue;
        }
        let at = |k: &Key| k.nodes.get(name).copied().unwrap_or(Vec3::ZERO);
        let (a, b, c, d) = (at(k0), at(k1), at(k2), at(k3));
        nodes.insert(
            name.clone(),
            Vec3::new(
                catmull_rom(a.x, b.x, c.x, d.x, u),
                catmull_rom(a.y, b.y, c.y, d.y, u),
                catmull_rom(a.z, b.z, c.z, d.z, u),
            ),
        );
    }

    InspectSample {
        pos,
        rot,
        spin,
        primary: lerp(k1.pose.primary, k2.pose.primary),
        support: lerp(k1.pose.support, k2.pose.support),
        primary_fingers: fingers(k1.pose.primary_fingers, k2.pose.primary_fingers, u),
        support_fingers: fingers(k1.pose.support_fingers, k2.pose.support_fingers, u),
        nodes,
    }
}

/// Which knife a skin is, from its id **and** its name — the same rule the
/// prop is picked by, so a knife is inspected as the knife it is drawn as.
pub fn knife_archetype(skin_id: &str, skin_name: &str) -> &'static str {
    let s = format!("{skin_id} {skin_name}").to_ascii_lowercase();
    if s.contains("karambit") {
        "knife-karambit"
    } else if s.contains("butterfly") {
        "knife-butterfly"
    } else if s.contains("bayonet") || s.contains("lore") {
        "knife-bayonet"
    } else if s.contains("skeleton") {
        "knife-skeleton"
    } else if s.contains("huntsman") {
        "knife-huntsman"
    } else {
        "knife-tactical"
    }
}

/// The prop id for a knife archetype, as `prop.rs` keys them.
pub fn knife_prop_id(archetype: &str) -> &'static str {
    match archetype {
        "knife-karambit" => "knife_karambit",
        "knife-butterfly" => "knife_butterfly",
        "knife-bayonet" => "knife_bayonet",
        "knife-skeleton" => "knife_skeleton",
        "knife-huntsman" => "knife_huntsman",
        _ => "knife",
    }
}

pub fn inspect_clip_for(weapon_id: &str, skin_id: &str, skin_name: &str) -> String {
    if weapon_id == "knife" {
        return knife_archetype(skin_id, skin_name).to_string();
    }
    if weapon_id.starts_with("nade") || weapon_id.starts_with("grenade") {
        return "nade".into();
    }
    if clips().contains_key(weapon_id) {
        weapon_id.to_string()
    } else {
        FALLBACK.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const GOLDEN_JSON: &str =
        include_str!("../../../packages/core/src/modules/hassault/models/inspects.golden.json");

    #[test]
    fn every_sample_matches_the_reference_sampler() {
        let golden: Value = serde_json::from_str(GOLDEN_JSON).expect("golden parses");
        let golden = golden.as_object().expect("golden is a map");
        assert!(!golden.is_empty());
        for (clip_id, points) in golden {
            for p in points.as_array().expect("points") {
                let t = p["t"].as_f64().unwrap() as f32;
                let s = sample_inspect(clip_id, t);
                let pos = vec3(&p["pos"]).unwrap();
                let rot = vec3(&p["rot"]).unwrap();
                let spin = p["spin"].as_f64().unwrap() as f32;
                assert!(
                    (s.pos - pos).abs().max_element() < 1e-4,
                    "{clip_id} @{t} pos {} vs {pos}",
                    s.pos
                );
                assert!(
                    (s.rot - rot).abs().max_element() < 1e-4,
                    "{clip_id} @{t} rot {} vs {rot}",
                    s.rot
                );
                assert!(
                    (s.spin - spin).abs() < 1e-4,
                    "{clip_id} @{t} spin {} vs {spin}",
                    s.spin
                );
            }
        }
    }

    #[test]
    fn every_clip_starts_and_ends_at_rest() {
        for id in clip_ids() {
            for t in [0.0, 1.0] {
                let s = sample_inspect(id, t);
                assert!(s.pos.abs().max_element() < 1e-5, "{id} @{t} pos {}", s.pos);
                assert!(s.rot.abs().max_element() < 1e-5, "{id} @{t} rot {}", s.rot);
                let turns = s.spin / std::f32::consts::TAU;
                assert!(
                    (turns - turns.round()).abs() < 1e-4,
                    "{id} @{t} spin {}",
                    s.spin
                );
            }
        }
    }

    #[test]
    fn a_knife_is_named_from_its_id_and_its_name() {
        assert_eq!(
            knife_archetype("k_1042", "Karambit | Fade"),
            "knife-karambit"
        );
        assert_eq!(knife_archetype("knife-lore", ""), "knife-bayonet");
        assert_eq!(knife_archetype("", ""), "knife-tactical");
        assert_eq!(knife_prop_id("knife-tactical"), "knife");
        assert_eq!(inspect_clip_for("railgun", "", ""), "assault");
        assert_eq!(inspect_clip_for("pistol", "", ""), "pistol");
    }

    #[test]
    fn the_butterfly_swings_its_handles() {
        let s = sample_inspect("knife-butterfly", 0.28);
        assert!(s.nodes["handle_bite"].x.abs() > 2.5);
    }
}
