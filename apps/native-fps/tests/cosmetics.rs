//! How players look, against `cosmetics-vectors.json`, which
//! `scripts/gen_hassault_cosmetics_vectors.py` writes from `cosmetics.py`.
//!
//! The same claims `cosmetics.test.ts` makes of the browser: this crate's
//! `palette_for` lands a skin's colours where the reference does, it reads the
//! bundle wire the server writes, and its range carries the loadout the server
//! would hand out on each side.

use std::path::PathBuf;

use hassault_native::api::{loadout_slots, WeaponSpec};
use hassault_native::cosmetics::{side_of, Cosmetics, CosmeticsMap};
use hassault_native::protocol::{classify, Event};
use hassault_native::viewmodel::{palette_rgb, Skin};
use serde_json::{json, Value};

fn vectors() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../packages/core/src/modules/hassault/__tests__/cosmetics-vectors.json");
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("cosmetics vectors missing at {} ({e})", path.display()));
    serde_json::from_str(&text).expect("cosmetics vectors are not valid JSON")
}

#[test]
fn the_palette_matches_the_reference_for_every_skin() {
    let v = vectors();
    let cases = v["palettes"].as_array().unwrap();
    assert!(cases.len() > 10);
    for case in cases {
        let skin = (!case["skin"].is_null()).then(|| Skin {
            id: None,
            base_color: case["skin"]["baseColor"].as_str().unwrap().to_string(),
            accent_color: case["skin"]["accentColor"].as_str().unwrap().to_string(),
            pattern_type: case["skin"]["patternType"].as_str().unwrap().to_string(),
            float_value: case["skin"]["floatValue"].as_f64().unwrap() as f32,
        });
        let got = palette_rgb(skin.as_ref());
        for (i, part) in ["body", "dark", "grip", "accent"].iter().enumerate() {
            for c in 0..3 {
                let want = case["palette"][part][c].as_f64().unwrap() as f32;
                assert!(
                    (got[i][c] - want).abs() < 1e-3,
                    "{}: {part}[{c}] is {} not {want}",
                    case["name"],
                    got[i][c]
                );
            }
        }
    }
}

#[test]
fn the_bundle_wire_reads_from_every_event_that_carries_it() {
    let v = vectors();
    let wire = &v["wire"];
    let id = wire["playerId"].as_str().unwrap();

    let welcome: CosmeticsMap =
        serde_json::from_value(wire["welcome"]["cosmetics"].clone()).unwrap();
    let look = &welcome[id];

    let joined = classify(
        &json!({"channel": "hassault", "event": "joined", "data": wire["joined"]}).to_string(),
    );
    let Some(Event::Joined(j)) = joined else {
        panic!("not a joined event")
    };
    assert_eq!(j.cosmetics.as_ref(), Some(look));

    let changed = classify(
        &json!({"channel": "hassault", "event": "cosmetics", "data": wire["cosmetics"]})
            .to_string(),
    );
    let Some(Event::Cosmetics(c)) = changed else {
        panic!("not a cosmetics event")
    };
    assert_eq!(c.player_id, id);
    assert_eq!(c.cosmetics.as_ref(), Some(look));

    // The half for the team a player is on.
    assert_eq!(side_of(0), "t");
    assert_eq!(side_of(1), "ct");
    assert_eq!(look.t.agent.id, "scavenger_prime");
    assert!(look.skin_for(0, "pistol").is_none());
    assert!((look.skin_for(1, "pistol").unwrap().float_value - 0.62).abs() < 1e-6);
    assert_eq!(look.skins_for_team(1).len(), 2);
    assert!(look.armor(0).is_some());
}

#[test]
fn an_older_server_sends_no_bundle_and_nothing_breaks() {
    let joined = classify(
        r#"{"channel":"hassault","event":"joined","data":{"room":"r","player":{"id":"p"}}}"#,
    );
    let Some(Event::Joined(j)) = joined else {
        panic!("not a joined event")
    };
    assert!(j.cosmetics.is_none());
    assert!(Cosmetics::default().skin_for(0, "assault").is_none());
    assert!(Cosmetics::default().armor(0).is_none());
}

#[test]
fn the_range_carries_each_sides_loadout_as_the_server_would() {
    let v = vectors();
    let weapons: Vec<WeaponSpec> = v["weapons"]
        .as_array()
        .unwrap()
        .iter()
        .map(|w| {
            serde_json::from_value(json!({
                "id": w["id"], "name": w["id"], "slot": w["slot"], "side": w["side"],
            }))
            .unwrap()
        })
        .collect();
    for case in v["loadouts"].as_array().unwrap() {
        // The stored setting arrives as a string and is parsed once at launch.
        let stored = match &case["choice"] {
            Value::String(raw) => serde_json::from_str(raw).unwrap(),
            other => other.clone(),
        };
        for side in ["t", "ct"] {
            let slots = loadout_slots(&weapons, &stored, side);
            let id = |i: i32| weapons[i as usize].id.clone();
            let want = &case["sides"][side];
            assert_eq!(
                id(slots.primary),
                want["primary"],
                "{} ({side})",
                case["name"]
            );
            assert_eq!(
                id(slots.secondary),
                want["secondary"],
                "{} ({side})",
                case["name"]
            );
            assert_eq!(id(slots.melee), want["melee"], "{} ({side})", case["name"]);
        }
    }
}

#[test]
fn the_loadout_page_steps_one_side_and_keeps_the_other() {
    use hassault_native::controls::Controls;
    use hassault_native::menu::{Action, LoadoutAgent, LoadoutWeapon, Menu, Page};
    use hassault_native::settings::Settings;

    let weapon = |id: &str, slot: &str, side: &str| LoadoutWeapon {
        id: id.into(),
        name: id.into(),
        slot: slot.into(),
        side: side.into(),
    };
    let mut menu = Menu::default();
    let settings = Settings::default();
    let keys = Controls::default();
    let root = menu.rows(&settings, &keys, false);
    assert!(!root.iter().any(|r| r.label == "LOADOUT"), "hidden with no weapons");

    menu.loadout.weapons = vec![
        weapon("assault", "primary", "any"),
        weapon("raider", "primary", "t"),
        weapon("lancer", "primary", "ct"),
        weapon("pistol", "secondary", "any"),
    ];
    menu.loadout.agents = vec![
        LoadoutAgent { id: "halon_default".into(), name: "a".into(), sides: vec!["ct".into()] },
        LoadoutAgent { id: "cyber_ghost".into(), name: "b".into(), sides: vec!["t".into(), "ct".into()] },
    ];
    // An older flat choice: the T rifle, on both sides.
    menu.loadout.choice = json!({"primary": "raider"});
    assert!(menu.rows(&settings, &keys, false).iter().any(|r| r.label == "LOADOUT"));

    menu.page = Page::Loadout;
    let rows = menu.rows(&settings, &keys, false);
    let value = |label: &str| rows.iter().find(|r| r.label == label).map(|r| r.value.clone());
    assert_eq!(value("T PRIMARY").as_deref(), Some("RAIDER"));
    // CT cannot carry it, so it shows what the server would hand out.
    assert_eq!(value("CT PRIMARY").as_deref(), Some("ASSAULT"));
    assert!(value("T AGENT").is_none(), "one agent on offer is not a choice");
    assert!(value("CT AGENT").is_some());

    let (key, raw) = menu.step_loadout(Action::LoadoutPick(1, 0), 1).unwrap();
    assert_eq!(key, "hassault.loadout");
    let doc: Value = serde_json::from_str(&raw).unwrap();
    assert_eq!(doc["t"]["primary"], "raider");
    assert_eq!(doc["ct"]["primary"], "lancer");

    let (key, raw) = menu.step_loadout(Action::AgentPick(1), 1).unwrap();
    assert_eq!(key, "hassault.agents");
    assert_eq!(serde_json::from_str::<Value>(&raw).unwrap()["ct"], "cyber_ghost");
}
