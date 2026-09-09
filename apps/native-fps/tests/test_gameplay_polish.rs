use hassault_native::effects::EffectsPool;
use hassault_native::physics::{MoveInput, PlayerState};
use hassault_native::physics_rapier::RapierPhysicsWorld;
use hassault_native::viewmodel::{Frame, WeaponViewModel};
use rapier3d::prelude::*;

#[test]
fn test_knife_speed_and_acceleration_boost() {
    let vertices = vec![
        point![0.0, 0.0, 0.0],
        point![50.0, 0.0, 0.0],
        point![50.0, 50.0, 0.0],
        point![0.0, 50.0, 0.0],
    ];
    let indices = vec![[0, 1, 2], [0, 2, 3]];

    let mut physics_gun = RapierPhysicsWorld::new(&vertices, &indices);
    physics_gun.set_position(2.0, 2.0, 0.0);
    let mut physics_knife = RapierPhysicsWorld::new(&vertices, &indices);
    physics_knife.set_position(2.0, 2.0, 0.0);

    let mut state_gun = PlayerState {
        x: 2.0,
        y: 2.0,
        z: 0.0,
        on_ground: true,
        ..Default::default()
    };
    let mut state_knife = PlayerState {
        x: 2.0,
        y: 2.0,
        z: 0.0,
        on_ground: true,
        ..Default::default()
    };

    let input_gun = MoveInput {
        forward: 1.0,
        strafe: 0.0,
        jump: false,
        crouch: false,
        sprint: false,
        knife: false,
    };
    let input_knife = MoveInput {
        forward: 1.0,
        strafe: 0.0,
        jump: false,
        crouch: false,
        sprint: false,
        knife: true,
    };

    // Step both for 15 ticks
    for _ in 0..15 {
        physics_gun.step_player(&mut state_gun, input_gun, 0.016);
        physics_knife.step_player(&mut state_knife, input_knife, 0.016);
    }

    let dist_gun = state_gun.x - 2.0;
    let dist_knife = state_knife.x - 2.0;

    assert!(
        dist_knife > dist_gun,
        "knife ({dist_knife}) should travel further than gun ({dist_gun})"
    );
    let ratio = dist_knife / dist_gun;
    assert!(
        (ratio - 1.10).abs() < 0.05,
        "expected ratio ~1.10, got {ratio}"
    );
}

#[test]
fn test_grenade_viewmodels_generate_vertices() {
    let mut vm = WeaponViewModel::default();
    let frame = Frame {
        ads: 0.0,
        speed: 0.0,
        on_ground: true,
        reloading: false,
        yaw: 0.0,
        pitch: 0.0,
        visible: true,
        move_speed: 16.0,
        fov: 1.0,
        since_landed: 1.0,
        reload_progress: None,
    };

    let nades = ["nade_he", "nade_flash", "nade_smoke", "nade_molotov"];
    for nade in nades {
        vm.set_weapon(nade, None);
        vm.update(0.016, &frame);

        let mut verts = Vec::new();
        vm.vertices(&mut verts);
        assert!(
            !verts.is_empty(),
            "viewmodel for {nade} must produce vertices (got {})",
            verts.len()
        );
    }
}

#[test]
fn test_effects_shot_ex_draw_beam() {
    let origin = [0.0, 0.0, 1.6];
    let ends = vec![[10.0, 0.0, 1.6]];
    let faces = vec![1];

    let count_no_beam = {
        let mut p = EffectsPool::default();
        p.shot_ex(origin, &ends, &faces, true, false, false);
        p.len()
    };
    let count_with_beam = {
        let mut p = EffectsPool::default();
        p.shot_ex(origin, &ends, &faces, true, false, true);
        p.len()
    };
    assert_eq!(
        count_with_beam,
        count_no_beam + 1,
        "drawing beam must add exactly 1 tracer beam"
    );
}
