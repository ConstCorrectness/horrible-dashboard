//! `WeaponSpec::shot_cone` against `accuracy-vectors.json`, which
//! `scripts/gen_hassault_accuracy_vectors.py` writes from `weapons.shot_cone`.
//! The same claim `accuracy.test.ts` makes of the browser: the training range
//! fires the cone a match does.

use std::path::PathBuf;

use hassault_native::api::WeaponSpec;
use hassault_native::physics::MOVE_SPEED;
use hassault_native::trace::{ShotContext, ACCURATE_SPEED_SCALE};
use serde_json::Value;

fn vectors() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../packages/core/src/modules/hassault/__tests__/accuracy-vectors.json");
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("accuracy vectors missing at {} ({e})", path.display()));
    serde_json::from_str(&text).expect("accuracy vectors are not valid JSON")
}

#[test]
fn shot_cone_matches_the_server() {
    let v = vectors();
    assert_eq!(MOVE_SPEED as f64, v["moveSpeed"].as_f64().unwrap());
    assert!((ACCURATE_SPEED_SCALE as f64 - v["accurateSpeedScale"].as_f64().unwrap()).abs() < 1e-6);
    let table = v["weapons"].as_object().expect("weapons");
    let cases = v["cases"].as_array().expect("cases");
    assert!(!cases.is_empty());
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let id = case["weapon"].as_str().unwrap();
        // Through the client's own `WeaponSpec`, so the renames are tested too:
        // a missing one is a silent zero, not an error.
        let weapon: WeaponSpec = serde_json::from_value(table[id].clone()).expect("a weapon");
        let ctx = &case["ctx"];
        let shot = ShotContext {
            speed: ctx["speed"].as_f64().unwrap() as f32,
            airborne: ctx["airborne"].as_bool().unwrap(),
            crouch: ctx["crouch"].as_f64().unwrap() as f32,
            scoped: ctx["scoped"].as_i64().unwrap() as i32,
            since_last_shot: ctx["sinceLastShot"].as_f64().unwrap() as f32,
        };
        let got = weapon.shot_cone(&shot);
        let want = case["expect"].as_f64().unwrap() as f32;
        assert!((got - want).abs() < 1e-6, "{name}: {got} != server {want}");
    }
}
