//! This client's seat at the shared-fixture table.
//!
//! `packages/core/src/modules/hassault/__tests__/physics-vectors.json` is replayed
//! by three suites now:
//!
//! - `backend/tests/test_hassault_physics.py` — the server's simulation,
//! - `packages/core/.../__tests__/conformance.test.ts` — the browser client's,
//! - this file — the native client's.
//!
//! The fixture pins **agreement**, not correctness: each side's own unit tests
//! are what argue any of it is right. What this catches is the failure mode that
//! has no other symptom — a client that predicts movement slightly differently
//! from the server it is predicting *for*. Nothing throws; the player simply ends
//! up a little away from where everyone else thinks they are, and it presents as
//! shots that miss things you are looking at.
//!
//! Two things here are copied rather than shared, and both are deliberate:
//!
//! - **`build_world` is a third copy** of the fixture's rect→grid expansion,
//!   mirroring `build_world` in the Python file and `buildWorld` in the vitest
//!   one. Sharing it would mean a Rust crate reaching into the TS package, and
//!   the expansion is a dozen lines whose disagreement the vectors themselves
//!   would immediately expose.
//! - **The path is relative to the crate**, so this fails loudly if the fixture
//!   moves rather than quietly testing nothing.

use std::path::PathBuf;

use hassault_native::api::MapInfo;
use hassault_native::physics::{apply_impulse, spawn_at, step, MoveInput, PlayerState, Spawn};
use hassault_native::trace::{aim_vector, ray_hits_body_sized, raycast_world, BODY_HEIGHT};
use hassault_native::world::{World, PLAYER_RADIUS, SOLID, SPACE};
use serde_json::Value;

const PLANES: [&str; 9] = [
    "type", "floor", "ceil", "wtex", "ftex", "ctex", "vdelta", "utex", "tag",
];

fn vectors() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../packages/core/src/modules/hassault/__tests__/physics-vectors.json");
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "conformance vectors missing at {} ({e}) — this test is the only thing \
             keeping the native client's physics in step with the server's",
            path.display()
        )
    });
    serde_json::from_str(&text).expect("vectors are not valid JSON")
}

fn f(v: &Value, key: &str, default: f32) -> f32 {
    v.get(key).and_then(Value::as_f64).unwrap_or(default as f64) as f32
}

fn b(v: &Value, key: &str, default: bool) -> bool {
    v.get(key).and_then(Value::as_bool).unwrap_or(default)
}

/// Build a world from the fixture's rect description.
///
/// Everything starts SOLID so a spec only has to describe the space it cares
/// about — the same convention the other two suites use.
fn build_world(spec: &Value) -> World {
    let ssize = spec["ssize"].as_i64().unwrap() as i32;
    let n = (ssize * ssize) as usize;
    let mut types = vec![SOLID; n];
    let mut floor = vec![0u8; n];
    let mut ceil = vec![16u8; n];
    let mut vdelta = vec![0u8; n];

    for rect in spec["rects"].as_array().unwrap() {
        let (x0, x1) = (rect["x0"].as_i64().unwrap(), rect["x1"].as_i64().unwrap());
        let (y0, y1) = (rect["y0"].as_i64().unwrap(), rect["y1"].as_i64().unwrap());
        for y in y0..=y1 {
            for x in x0..=x1 {
                let i = (y * ssize as i64 + x) as usize;
                types[i] = f(rect, "type", SPACE as f32) as u8;
                // `& 0xFF` because a floor can be negative and the plane is bytes;
                // `World` reads them back as i8, which is where the sign returns.
                floor[i] = (f(rect, "floor", 0.0) as i32 & 0xFF) as u8;
                ceil[i] = (f(rect, "ceil", 16.0) as i32 & 0xFF) as u8;
                vdelta[i] = f(rect, "vdelta", 0.0) as u8;
            }
        }
    }

    let mut bytes = Vec::with_capacity(n * 9);
    bytes.extend_from_slice(&types);
    bytes.extend_from_slice(&floor);
    bytes.extend_from_slice(&ceil);
    bytes.extend(std::iter::repeat_n(0u8, n * 3)); // wtex, ftex, ctex
    bytes.extend_from_slice(&vdelta);
    bytes.extend(std::iter::repeat_n(0u8, n * 2)); // utex, tag

    let info = MapInfo {
        ssize,
        cubic_size: n,
        plane_order: PLANES.iter().map(|s| s.to_string()).collect(),
        ..Default::default()
    };
    World::new(info, &bytes).expect("fixture world")
}

#[test]
fn movement_matches_the_server_and_the_browser_client() {
    let data = vectors();
    let cases = data["cases"].as_array().expect("cases");
    let tol = data["tolerance"].as_f64().unwrap() as f32;
    assert!(!cases.is_empty(), "the fixture has no movement cases");

    for case in cases {
        let name = case["name"].as_str().unwrap_or("?");
        let world = build_world(&data["worlds"][case["world"].as_str().unwrap()]);
        let start = &case["start"];
        let mut player = PlayerState {
            x: f(start, "x", 0.0),
            y: f(start, "y", 0.0),
            z: f(start, "z", 0.0),
            vel_x: f(start, "vel_x", 0.0),
            vel_y: f(start, "vel_y", 0.0),
            vel_z: f(start, "vel_z", 0.0),
            yaw: f(start, "yaw", 0.0),
            pitch: f(start, "pitch", 0.0),
            on_ground: b(start, "on_ground", false),
            crouch: f(start, "crouch", 0.0),
            ..Default::default()
        };

        for raw in case["steps"].as_array().unwrap() {
            if raw.get("yaw").is_some() {
                player.yaw = f(raw, "yaw", 0.0);
            }
            step(
                &world,
                &mut player,
                &MoveInput {
                    forward: f(raw, "forward", 0.0),
                    strafe: f(raw, "strafe", 0.0),
                    jump: b(raw, "jump", false),
                    crouch: b(raw, "crouch", false),
                },
                f(raw, "dt", 0.0),
            );
            // **After** the step, which is where the match server applies weapon
            // recoil (`simulate` steps, then `_handle_combat` fires). Applying it
            // before would put the kick into the same frame's integration and
            // shift every shoot-jump by one step.
            if let Some(imp) = raw.get("impulse").and_then(Value::as_array) {
                apply_impulse(
                    &mut player,
                    imp[0].as_f64().unwrap() as f32,
                    imp[1].as_f64().unwrap() as f32,
                    imp[2].as_f64().unwrap() as f32,
                );
            }
        }

        let expect = &case["expect"];
        for (label, got, want) in [
            ("x", player.x, f(expect, "x", 0.0)),
            ("y", player.y, f(expect, "y", 0.0)),
            ("z", player.z, f(expect, "z", 0.0)),
            ("velX", player.vel_x, f(expect, "velX", 0.0)),
            ("velY", player.vel_y, f(expect, "velY", 0.0)),
            ("velZ", player.vel_z, f(expect, "velZ", 0.0)),
            ("crouch", player.crouch, f(expect, "crouch", 0.0)),
        ] {
            assert!(
                (got - want).abs() <= tolerance(tol),
                "{name}: {label} was {got}, the other implementations get {want}"
            );
        }
        assert_eq!(
            player.on_ground,
            b(expect, "onGround", false),
            "{name}: onGround"
        );
    }
}

#[test]
fn spawn_placement_matches() {
    // In the fixture for the same reason `step` is: it is one rule with three
    // implementations, and a disagreement about where a player starts is a desync
    // from the very first frame.
    let data = vectors();
    let spawns = data["spawns"].as_array().expect("spawns");
    let tol = data["tolerance"].as_f64().unwrap() as f32;
    assert!(!spawns.is_empty(), "the fixture has no spawn cases");

    for case in spawns {
        let name = case["name"].as_str().unwrap_or("?");
        let world = build_world(&data["worlds"][case["world"].as_str().unwrap()]);
        let e = &case["entity"];
        let placed = spawn_at(
            &world,
            &Spawn {
                x: f(e, "x", 0.0),
                y: f(e, "y", 0.0),
                z: f(e, "z", 0.0),
                yaw: f(e, "yaw", 0.0),
            },
        );
        let expect = &case["expect"];
        for (label, got, want) in [
            ("x", placed.x, f(expect, "x", 0.0)),
            ("y", placed.y, f(expect, "y", 0.0)),
            ("z", placed.z, f(expect, "z", 0.0)),
            ("yaw", placed.yaw, f(expect, "yaw", 0.0)),
        ] {
            assert!(
                (got - want).abs() <= tolerance(tol),
                "{name}: {label} was {got}, expected {want}"
            );
        }
        assert_eq!(
            placed.on_ground,
            b(expect, "onGround", true),
            "{name}: onGround"
        );
    }
}

/// The fixture's tolerance is 1e-9, which is a **double**'s tolerance.
///
/// This implementation is `f32`, matching the wire and the renderer, so it cannot
/// meet it — and pretending otherwise by loosening the fixture would loosen it for
/// the two implementations that *can*. The floor here is single-precision epsilon
/// scaled for the magnitudes involved (positions up to ~512, velocities up to
/// ~30): about six significant figures, which is far tighter than any drift that
/// could affect play and far looser than f64 noise.
fn tolerance(fixture: f32) -> f32 {
    fixture.max(1e-3)
}

/// The shot geometry, replayed from the same fixture.
///
/// Added when the training range was ported: `trace.rs` is a fourth copy of
/// arithmetic that already exists in `weapons.py`, `trace.ts` and this crate's
/// own physics, and the fixture carried `traces`/`bodies` for a year with only
/// two of the three implementations reading them. A copy with no seat at this
/// table is a copy that drifts, and the drift has no symptom except shots that
/// miss things you are looking at.
#[test]
fn shot_geometry_matches_the_server_and_the_browser_client() {
    let vectors = vectors();
    let worlds = &vectors["worlds"];
    let tol = f(&vectors, "tolerance", 1e-9);

    let cases = vectors["traces"].as_array().expect("traces");
    assert!(!cases.is_empty(), "the fixture lost its trace cases");
    for case in cases {
        let name = case["name"].as_str().unwrap_or("unnamed");
        let world = build_world(&worlds[case["world"].as_str().expect("world")]);
        let origin = vec3(&case["origin"]);
        let direction = aim_vector(f(case, "yaw", 0.0), f(case, "pitch", 0.0));
        let got = raycast_world(&world, origin, direction, f(case, "max_distance", 100.0));
        let want = f(case, "expect", 0.0);
        assert!(
            (got - want).abs() <= tolerance(tol),
            "{name}: stopped at {got}, the other two clients say {want}"
        );
    }

    let bodies = vectors["bodies"].as_array().expect("bodies");
    assert!(!bodies.is_empty(), "the fixture lost its body cases");
    for case in bodies {
        let name = case["name"].as_str().unwrap_or("unnamed");
        let origin = vec3(&case["origin"]);
        let direction = aim_vector(f(case, "yaw", 0.0), f(case, "pitch", 0.0));
        // A case may name a shorter body: crouching is a real height change,
        // and the case that pins it ("the same shot sails over it") is exactly
        // the one a replay that assumed the standing height would pass wrongly.
        let got = ray_hits_body_sized(
            origin,
            direction,
            vec3(&case["feet"]),
            f(case, "radius", PLAYER_RADIUS),
            f(case, "height", BODY_HEIGHT),
        );
        match case["expect"].as_f64() {
            // A miss is a distinct answer, not a large distance: the fixture
            // spells it `null` and reading that as 0.0 would turn every miss in
            // the table into a point-blank hit.
            None => assert!(got.is_none(), "{name}: hit at {got:?}, expected a miss"),
            Some(want) => {
                let want = want as f32;
                let got = got.unwrap_or_else(|| panic!("{name}: missed, expected {want}"));
                assert!(
                    (got - want).abs() <= tolerance(tol),
                    "{name}: entered at {got}, the other two clients say {want}"
                );
            }
        }
    }
}

fn vec3(v: &Value) -> [f32; 3] {
    let a = v.as_array().expect("a three-component point");
    [
        a[0].as_f64().unwrap() as f32,
        a[1].as_f64().unwrap() as f32,
        a[2].as_f64().unwrap() as f32,
    ]
}
