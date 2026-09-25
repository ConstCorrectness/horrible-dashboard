//! What is solid on a modelled map, and that both clients agree on it.
//!
//! The node rules live in `glb-colliders.json`; its `cases` are replayed here
//! and by `glb-colliders.test.ts`. The second test is the failure that made the
//! table: a keyword list once matched `Perimeter` against `rim`, so the outer
//! wall of every map was walk-through. From the middle of each map, a body
//! walking in any direction must meet something before it leaves the map.

use hassault_native::api::MapInfo;
use hassault_native::physics_rapier::RapierPhysicsWorld;
use hassault_native::world3d::{create_world_3d, glb_node_collides};

const TABLE: &str = include_str!("../../../packages/core/src/modules/hassault/glb-colliders.json");

const GLB_MAPS: &[&str] = &[
    "hd_facility",
    "hd_junkflea",
    "hd_bank",
    "hd_assault",
    "hd_office",
    "hd_dust2",
    "hd_inferno",
    "hd_mirage",
    "hd_nuke",
];

#[test]
fn shared_cases_agree_with_the_browser() {
    let json: serde_json::Value = serde_json::from_str(TABLE).unwrap();
    for case in json["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let mats: Vec<&str> = case["materials"].as_array().unwrap().iter().map(|m| m.as_str().unwrap()).collect();
        let s: Vec<f32> = case["size"].as_array().unwrap().iter().map(|v| v.as_f64().unwrap() as f32).collect();
        let want = case["collides"].as_bool().unwrap();
        assert_eq!(glb_node_collides(name, &mats, [s[0], s[1], s[2]]), want, "{name}");
    }
}

#[test]
fn no_map_can_be_walked_out_of() {
    let mut failures = Vec::new();
    for name in GLB_MAPS {
        let world = create_world_3d(MapInfo { name: (*name).into(), ssize: 256, ..Default::default() });
        let physics = RapierPhysicsWorld::new(&world.col_vertices, &world.col_indices);
        let [cx, cy, _] = world.bounds.center;
        let reach = world.bounds.extent * 3.0;
        for k in 0..16 {
            let a = k as f32 * std::f32::consts::PI / 8.0;
            let dir = [a.cos(), a.sin(), 0.0];
            // Chest height over the ground floor, where a walking body meets a wall.
            let (hit, _, _) = physics.cast_ray([cx, cy, 2.5], dir, reach);
            if !hit {
                failures.push(format!("{name}: nothing stops a body heading {}°", k * 45 / 2));
            }
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
