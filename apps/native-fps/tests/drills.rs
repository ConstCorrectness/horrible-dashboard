//! The training range's drills against `drill-vectors.json`, which
//! `scripts/gen_hassault_drill_vectors.py` writes from `drills.py`.
//!
//! The same two claims `training-drill.test.ts` makes of the browser: a served
//! schedule replayed through this crate's own `physics::step` lands where the
//! server's simulation did, and `summarise` scores a run as the Python does.

use std::path::PathBuf;

use hassault_native::drills::{summarise, DrillEvent, DrillMover, DrillSegment, DrillTargetSpec};
use hassault_native::api::MapInfo;
use hassault_native::world::{World, SOLID, SPACE};
use serde_json::Value;

fn vectors() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../packages/core/src/modules/hassault/__tests__/drill-vectors.json");
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("drill vectors missing at {} ({e})", path.display()));
    serde_json::from_str(&text).expect("drill vectors are not valid JSON")
}

/// The fixture's open room. The rect convention of `tests/conformance.rs`:
/// everything starts SOLID, rects carve space.
fn build_world(spec: &Value) -> World {
    let ssize = spec["ssize"].as_i64().unwrap() as i32;
    let n = (ssize * ssize) as usize;
    let mut types = vec![SOLID; n];
    let mut floor = vec![0u8; n];
    let mut ceil = vec![16u8; n];
    for rect in spec["rects"].as_array().unwrap() {
        let get = |k: &str, d: i64| rect.get(k).and_then(Value::as_i64).unwrap_or(d);
        for y in get("y0", 0)..=get("y1", 0) {
            for x in get("x0", 0)..=get("x1", 0) {
                let i = (y * ssize as i64 + x) as usize;
                types[i] = get("type", SPACE as i64) as u8;
                floor[i] = (get("floor", 0) as i32 & 0xFF) as u8;
                ceil[i] = (get("ceil", 16) as i32 & 0xFF) as u8;
            }
        }
    }
    let mut bytes = Vec::with_capacity(n * 9);
    bytes.extend_from_slice(&types);
    bytes.extend_from_slice(&floor);
    bytes.extend_from_slice(&ceil);
    bytes.extend(std::iter::repeat_n(0u8, n * 6)); // wtex ftex ctex vdelta utex tag
    let planes = [
        "type", "floor", "ceil", "wtex", "ftex", "ctex", "vdelta", "utex", "tag",
    ];
    let info = MapInfo {
        ssize,
        cubic_size: n,
        plane_order: planes.iter().map(|s| s.to_string()).collect(),
        ..Default::default()
    };
    World::new(info, &bytes).expect("fixture world")
}

#[test]
fn a_replayed_schedule_lands_where_the_server_simulated() {
    let v = vectors();
    let world = build_world(&v["world"]);
    let tol = v["nativeTolerance"].as_f64().unwrap() as f32;
    let tick_dt = 1.0 / v["tickRate"].as_f64().unwrap() as f32;
    let replays = v["replays"].as_array().expect("replays");
    assert!(!replays.is_empty());
    for replay in replays {
        let name = replay["name"].as_str().unwrap();
        let start = &replay["start"];
        let segments: Vec<DrillSegment> =
            serde_json::from_value(replay["segments"].clone()).expect("segments");
        let spec = DrillTargetSpec {
            id: "t".into(),
            x: start["x"].as_f64().unwrap() as f32,
            y: start["y"].as_f64().unwrap() as f32,
            z: start["z"].as_f64().unwrap() as f32,
            yaw: start["yaw"].as_f64().unwrap() as f32,
            segments,
        };
        let mut mover = DrillMover::new(&spec, tick_dt);
        let ticks = replay["ticks"].as_u64().unwrap() as i64;
        let samples = replay["samples"].as_array().unwrap();
        let mut next = 0usize;
        for k in 0..=ticks {
            if let Some(sample) = samples.get(next) {
                if sample[0].as_i64() == Some(k) {
                    let want = [
                        sample[1].as_f64().unwrap() as f32,
                        sample[2].as_f64().unwrap() as f32,
                        sample[3].as_f64().unwrap() as f32,
                    ];
                    let got = [mover.state.x, mover.state.y, mover.state.z];
                    for axis in 0..3 {
                        assert!(
                            (got[axis] - want[axis]).abs() < tol,
                            "{name} tick {k}: {got:?} != server {want:?}"
                        );
                    }
                    next += 1;
                }
            }
            if k < ticks {
                mover.advance(&world);
            }
        }
        assert_eq!(next, samples.len(), "{name}: every sample was checked");
    }
}

#[test]
fn summarise_matches_the_python_reference() {
    let v = vectors();
    let cases = v["stats"].as_array().expect("stats");
    assert!(!cases.is_empty());
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let events: Vec<DrillEvent> =
            serde_json::from_value(case["events"].clone()).expect("events");
        let got = summarise(&events, case["seconds"].as_f64().unwrap());
        let want = &case["expect"];
        let close = |field: &str, value: f64| {
            let expect = want[field].as_f64().unwrap();
            assert!(
                (value - expect).abs() < 1e-9,
                "{name}: {field} {value} != {expect}"
            );
        };
        let maybe = |field: &str, value: Option<f64>| match (want[field].as_f64(), value) {
            (None, None) => {}
            (Some(expect), Some(value)) => {
                assert!(
                    (value - expect).abs() < 1e-9,
                    "{name}: {field} {value} != {expect}"
                )
            }
            (expect, value) => panic!("{name}: {field} {value:?} != {expect:?}"),
        };
        assert_eq!(got.shots as u64, want["shots"].as_u64().unwrap(), "{name}");
        assert_eq!(
            got.hit_shots as u64,
            want["hitShots"].as_u64().unwrap(),
            "{name}"
        );
        assert_eq!(got.hits as u64, want["hits"].as_u64().unwrap(), "{name}");
        assert_eq!(
            got.head_hits as u64,
            want["headHits"].as_u64().unwrap(),
            "{name}"
        );
        assert_eq!(got.kills as u64, want["kills"].as_u64().unwrap(), "{name}");
        close("accuracy", got.accuracy);
        close("headshotShare", got.headshot_share);
        close("killsPerMinute", got.kills_per_minute);
        maybe("medianTtk", got.median_ttk);
        maybe("medianReaction", got.median_reaction);
    }
}

#[test]
fn a_served_drill_deserializes_with_its_renames() {
    // `tickRate` read as zero would be a drill that never advances.
    let spec: hassault_native::drills::DrillSpec = serde_json::from_value(serde_json::json!({
        "id": "aim_strafe", "name": "Strafe Gallery", "description": "", "pattern": "wide",
        "map": "hd_aim", "station": "gallery", "seconds": 60.0, "hp": 100, "headOnly": true,
        "respawn": 1.0, "tickRate": 60, "ticks": 3600,
        "shooter": {"x": 10.5, "y": 48.5, "z": 1.0, "yaw": 0.0},
        "targets": [{"id": "t0", "x": 40.5, "y": 38.5, "z": 0.0, "yaw": 3.1, "segments": [[0, 1, 0, 0, 1]]}]
    }))
    .expect("a served drill");
    assert_eq!(spec.tick_rate, 60);
    assert!(spec.head_only);
    assert_eq!(spec.targets[0].segments[0], [0, 1, 0, 0, 1]);
}
