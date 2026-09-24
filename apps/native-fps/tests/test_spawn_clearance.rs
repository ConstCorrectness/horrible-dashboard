//! Every spawn in every GLB map has room for a standing body.
//!
//! hd_office was generated in metres — a 4-unit storey for a 5.2-unit player —
//! so every spawn resolved onto the roof, and five of its eight spawns sat inside
//! desks, a pillar or a cubicle partition besides. Nothing failed: a body that
//! does not fit is simply pushed somewhere it does. This probes the collision
//! mesh the way the character controller will meet it, with rays, so a map
//! that is too short or a spawn placed in furniture is a test failure rather
//! than a player standing on a ceiling. When it was written, bank, assault,
//! inferno, mirage and nuke failed it too (spawns off the floor, on a mezzanine
//! with 4.6 of headroom, under overhangs); they were moved in the same change.

use hassault_native::api::MapInfo;
use hassault_native::physics::STANDING_HEIGHT;
use hassault_native::physics_rapier::RapierPhysicsWorld;
use hassault_native::world::PLAYER_RADIUS;
use hassault_native::world3d::create_world_3d;

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

/// Where a standing body at (x, y) collides, or `None` if it fits.
fn obstruction(physics: &RapierPhysicsWorld, x: f32, y: f32, z: f32) -> Option<String> {
    // The floor it stands on is the first surface below the spawn's eye.
    let (hit, down, _) = physics.cast_ray([x, y, z + 3.0], [0.0, 0.0, -1.0], 20.0);
    if !hit {
        return Some("no floor below".into());
    }
    let floor = z + 3.0 - down;
    // Headroom over the body's whole footprint, from just above a step.
    let ring = [(0.0, 0.0), (1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)];
    for (dx, dy) in ring {
        let (px, py) = (x + dx * PLAYER_RADIUS, y + dy * PLAYER_RADIUS);
        let start = floor + 0.6;
        let (hit, up, _) = physics.cast_ray([px, py, start], [0.0, 0.0, 1.0], STANDING_HEIGHT);
        if hit && start + up < floor + STANDING_HEIGHT {
            return Some(format!(
                "only {:.2} of headroom at ({px:.1}, {py:.1}), a body needs {STANDING_HEIGHT}",
                start + up - floor
            ));
        }
    }
    // Nothing within the body's radius at chest height.
    for k in 0..8 {
        let a = k as f32 * std::f32::consts::FRAC_PI_4;
        let dir = [a.cos(), a.sin(), 0.0];
        let (hit, d, _) = physics.cast_ray([x, y, floor + 2.5], dir, PLAYER_RADIUS);
        if hit && d < PLAYER_RADIUS {
            return Some(format!("a wall {d:.2} away at bearing {}°", k * 45));
        }
    }
    None
}

fn spawn_failures(name: &str) -> Vec<String> {
    let world = create_world_3d(MapInfo {
        name: name.into(),
        ssize: 64,
        ..Default::default()
    });
    let physics = RapierPhysicsWorld::new(&world.col_vertices, &world.col_indices);
    assert!(!world.spawns.is_empty(), "{name} has no spawns");
    world
        .spawns
        .iter()
        .enumerate()
        .filter_map(|(i, s)| {
            obstruction(&physics, s.x, s.y, s.z)
                .map(|why| format!("{name} spawn {i} ({}, {}): {why}", s.x, s.y))
        })
        .collect()
}

#[test]
fn every_glb_spawn_has_room_for_a_standing_body() {
    let failures: Vec<String> = GLB_MAPS.iter().flat_map(|name| spawn_failures(name)).collect();
    assert!(failures.is_empty(), "spawns without room:\n  {}", failures.join("\n  "));
}

/// Office only: its items were moved out of the furniture with this check.
/// Elsewhere items sit in crouch-height spaces this standing-body probe would
/// reject, and that wants a crouch-aware check rather than a looser one.
#[test]
fn office_items_are_reachable_on_foot() {
    let world = create_world_3d(MapInfo {
        name: "hd_office".into(),
        ssize: 64,
        ..Default::default()
    });
    let physics = RapierPhysicsWorld::new(&world.col_vertices, &world.col_indices);
    let failures: Vec<String> = world
        .items
        .iter()
        .filter_map(|item| {
            obstruction(&physics, item.x, item.y, item.z)
                .map(|why| format!("item {} {} ({}, {}): {why}", item.id, item.kind, item.x, item.y))
        })
        .collect();
    assert!(failures.is_empty(), "office items without room:\n  {}", failures.join("\n  "));
}
