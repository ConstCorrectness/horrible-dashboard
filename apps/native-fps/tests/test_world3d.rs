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

#[test]
fn test_bank_3d_generation() {
    let info = MapInfo {
        name: "hd_bank".into(),
        title: "The Bank".into(),
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
    assert_eq!(world.waterlevel, -100.0);

    // Verify Rapier collision
    let physics = RapierPhysicsWorld::new(&world.col_vertices, &world.col_indices);

    // Ray down onto street
    let (hit_street, dist_street, _) = physics.cast_ray([20.0, 8.0, 5.0], [0.0, 0.0, -1.0], 10.0);
    assert!(hit_street, "ray downwards onto street should hit floor at z=0");
    assert!((dist_street - 5.0).abs() < 0.2);

    // Ray down onto SWAT van roof (z = 2.8)
    let (hit_van, dist_van, _) = physics.cast_ray([20.0, 14.0, 5.0], [0.0, 0.0, -1.0], 10.0);
    assert!(hit_van, "ray downwards onto SWAT van roof should hit at z=2.8");
    assert!((dist_van - 2.2).abs() < 0.2);

    // Ray down onto entrance stone canopy (z = 4.8)
    let (hit_canopy, dist_canopy, _) = physics.cast_ray([30.0, 17.0, 7.0], [0.0, 0.0, -1.0], 10.0);
    assert!(hit_canopy, "ray downwards onto entrance canopy should hit at z=4.8");
    assert!((dist_canopy - 2.2).abs() < 0.2);

    // Ray down onto West Balcony mezzanine (z = 5.0)
    let (hit_mezz, dist_mezz, _) = physics.cast_ray([16.0, 40.0, 8.0], [0.0, 0.0, -1.0], 10.0);
    assert!(hit_mezz, "ray downwards onto West Balcony should hit at z=5.0");
    assert!((dist_mezz - 3.0).abs() < 0.2);

    // Ray down inside The Vault onto gold bullion pallets (z = 1.8)
    let (hit_vault, dist_vault, _) = physics.cast_ray([45.0, 54.0, 5.0], [0.0, 0.0, -1.0], 10.0);
    assert!(hit_vault, "ray downwards inside vault should hit gold pallets at z=1.8");
    assert!((dist_vault - 3.2).abs() < 0.2);
}

#[test]
fn test_assault_3d_generation() {
    let info = MapInfo {
        name: "hd_assault".into(),
        title: "Assault".into(),
        ssize: 64,
        ..Default::default()
    };

    let world = hassault_native::world3d::create_world_3d(info);
    println!("Loaded assault triangles: {}", world.triangles);

    assert!(world.triangles > 0, "should produce triangles");
    assert_eq!(world.render_positions.len(), world.triangles * 9);
    assert_eq!(world.render_normals.len(), world.triangles * 9);
    assert_eq!(world.render_colors.len(), world.triangles * 9);

    assert_eq!(world.spawns.len(), 8, "must have 8 spawns");
    assert_eq!(world.spawns.iter().filter(|s| s.team == 0).count(), 4, "must have 4 CT spawns");
    assert_eq!(world.spawns.iter().filter(|s| s.team == 1).count(), 4, "must have 4 T spawns");

    assert_eq!(world.items.len(), 10, "must have 10 pickups");
    assert_eq!(world.waterlevel, -100.0);

    // Verify Rapier 3D collision (1:1 human metric scale on 56m layout)
    let physics = RapierPhysicsWorld::new(&world.col_vertices, &world.col_indices);

    // Ray down onto street (z = 0.0) away from SWAT van
    let (hit_street, dist_street, _) = physics.cast_ray([10.0, 14.0, 5.0], [0.0, 0.0, -1.0], 10.0);
    assert!(hit_street, "ray downwards onto street should hit floor at z=0");
    assert!((dist_street - 5.0).abs() < 0.2, "dist_street was {}", dist_street);

    // Ray down onto Highway Bridge Deck (z = 7.5) away from piers
    let (hit_bridge, dist_bridge, _) = physics.cast_ray([18.0, 6.0, 10.0], [0.0, 0.0, -1.0], 10.0);
    assert!(hit_bridge, "ray downwards onto highway bridge deck should hit at z=7.5");
    assert!((dist_bridge - 2.5).abs() < 0.2, "dist_bridge was {}", dist_bridge);

    // Ray down under the highway bridge onto the sidewalk (z = 0.18)
    let (hit_under_bridge, dist_under, _) = physics.cast_ray([18.0, 6.0, 4.0], [0.0, 0.0, -1.0], 10.0);
    assert!(hit_under_bridge, "ray downwards under bridge should hit sidewalk at z=0.18");
    assert!((dist_under - 3.82).abs() < 0.2, "dist_under was {}", dist_under);

    // Ray down onto Warehouse Rooftop (z = 9.0)
    let (hit_roof, dist_roof, _) = physics.cast_ray([25.0, 35.0, 12.0], [0.0, 0.0, -1.0], 10.0);
    assert!(hit_roof, "ray downwards onto warehouse roof should hit at z=9.0");
    assert!((dist_roof - 3.0).abs() < 0.2, "dist_roof was {}", dist_roof);

    // Ray down onto Elevated Catwalk (z = 4.2)
    let (hit_catwalk, dist_catwalk, _) = physics.cast_ray([10.5, 40.0, 6.0], [0.0, 0.0, -1.0], 10.0);
    assert!(hit_catwalk, "ray downwards onto elevated catwalk should hit at z=4.2");
    assert!((dist_catwalk - 1.8).abs() < 0.2, "dist_catwalk was {}", dist_catwalk);

    // Ray down under the Catwalk onto the ground floor (z = 0.0)
    let (hit_under_catwalk, dist_under_catwalk, _) = physics.cast_ray([10.5, 40.0, 3.0], [0.0, 0.0, -1.0], 10.0);
    assert!(hit_under_catwalk, "ray downwards under catwalk should hit ground at z=0.0");
    assert!((dist_under_catwalk - 3.0).abs() < 0.2, "dist_under_catwalk was {}", dist_under_catwalk);

    // Ray down onto Hostage Office 2nd floor (z = 4.2)
    let (hit_office, dist_office, _) = physics.cast_ray([36.0, 48.0, 6.0], [0.0, 0.0, -1.0], 10.0);
    assert!(hit_office, "ray downwards onto hostage office floor should hit at z=4.2");
    assert!((dist_office - 1.8).abs() < 0.2, "dist_office was {}", dist_office);
}

