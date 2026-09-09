use hassault_native::api::MapInfo;
use hassault_native::physics_rapier::RapierPhysicsWorld;
use hassault_native::world3d::create_procedural_facility_3d;

#[test]
fn test_facility_3d_generation() {
    let info = MapInfo {
        name: "hd_facility".into(),
        title: "Deadzone Facility".into(),
        ssize: 64,
        ..Default::default()
    };

    let world = create_procedural_facility_3d(info);

    assert!(world.triangles > 0, "should produce triangles");
    assert_eq!(world.render_positions.len(), world.triangles * 9);
    assert_eq!(world.render_normals.len(), world.triangles * 9);
    assert_eq!(world.render_colors.len(), world.triangles * 9);

    assert!(!world.spawns.is_empty(), "must have spawns");
    assert!(world.spawns.iter().any(|s| s.team == 0), "must have CLA spawns");
    assert!(world.spawns.iter().any(|s| s.team == 1), "must have RVSF spawns");

    assert!(!world.items.is_empty(), "must have pickups");
    assert_eq!(world.waterlevel, -3.5);

    // Verify Rapier world can load the facility's collision geometry
    let physics = RapierPhysicsWorld::new(&world.col_vertices, &world.col_indices);
    let (hit, dist, _) = physics.cast_ray([32.0, 32.0, 10.0], [0.0, 0.0, -1.0], 20.0);
    assert!(hit, "ray downwards from center catwalk should hit collision mesh");
    assert!(dist < 20.0);
}

#[test]
fn test_junk_flea_3d_generation() {
    let info = MapInfo {
        name: "hd_junkflea".into(),
        title: "Junk Flea (Combat Arms)".into(),
        ssize: 64,
        ..Default::default()
    };

    let world = hassault_native::world3d::create_world_3d(info);

    assert!(world.triangles > 0, "should produce triangles");
    assert_eq!(world.render_positions.len(), world.triangles * 9);
    assert_eq!(world.render_normals.len(), world.triangles * 9);
    assert_eq!(world.render_colors.len(), world.triangles * 9);

    assert_eq!(world.spawns.len(), 8, "must have 8 spawns");
    assert_eq!(world.spawns.iter().filter(|s| s.team == 0).count(), 4, "must have 4 CLA spawns");
    assert_eq!(world.spawns.iter().filter(|s| s.team == 1).count(), 4, "must have 4 RVSF spawns");

    assert_eq!(world.items.len(), 10, "must have 10 pickups");
    assert_eq!(world.waterlevel, -5.0);

    // Verify Rapier collision
    let physics = RapierPhysicsWorld::new(&world.col_vertices, &world.col_indices);
    // Ray down to catwalk
    let (hit, dist, _) = physics.cast_ray([32.0, 32.0, 10.0], [0.0, 0.0, -1.0], 20.0);
    assert!(hit, "ray downwards from catwalk should hit collision mesh");
    assert!(dist < 10.0);

    // Ray down into subterranean trench at x=19, y=32
    let (hit_trench, dist_trench, _) = physics.cast_ray([19.0, 32.0, 5.0], [0.0, 0.0, -1.0], 20.0);
    assert!(hit_trench, "ray into trench should hit trench floor at z=-2");
    // ray started at z=5, hits at z=-2 -> distance is ~7.0
    assert!((dist_trench - 7.0).abs() < 0.1, "distance to trench floor should be ~7.0, was {}", dist_trench);
}

