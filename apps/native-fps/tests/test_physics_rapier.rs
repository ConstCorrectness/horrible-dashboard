use hassault_native::physics_rapier::{
    RapierMoveInput, RapierPhysicsWorld, RapierPlayerState,
};
use rapier3d::prelude::*;

#[test]
fn test_rapier_character_controller_movement() {
    // Floor quad from 0..20, 0..20 at z = 0
    let vertices = vec![
        point![0.0, 0.0, 0.0],
        point![20.0, 0.0, 0.0],
        point![20.0, 20.0, 0.0],
        point![0.0, 20.0, 0.0],
    ];
    let indices = vec![[0, 1, 2], [0, 2, 3]];

    let mut physics = RapierPhysicsWorld::new(&vertices, &indices);
    physics.set_position(2.0, 2.0, 1.0);

    let mut state = RapierPlayerState {
        x: 2.0,
        y: 2.0,
        z: 1.0,
        vx: 0.0,
        vy: 0.0,
        vz: 0.0,
        grounded: false,
        crouching: false,
        ..Default::default()
    };

    let input = RapierMoveInput {
        forward: 1.0,
        strafe: 0.0,
        yaw: 0.0, // facing north (+y)
        jump: false,
        crouch: false,
        sprint: false,
        knife: false,
        dt: 0.016,
    };

    for _ in 0..10 {
        physics.step(&mut state, input);
    }

    assert!(state.y > 2.0, "player should move forward along y");
    assert!(state.z <= 1.0, "player should settle on ground");
}

#[test]
fn test_rapier_raycast_against_mesh() {
    let vertices = vec![
        point![0.0, 0.0, 0.0],
        point![20.0, 0.0, 0.0],
        point![20.0, 20.0, 0.0],
        point![0.0, 20.0, 0.0],
    ];
    let indices = vec![[0, 1, 2], [0, 2, 3]];

    let physics = RapierPhysicsWorld::new(&vertices, &indices);
    let (hit, dist, point) = physics.cast_ray([2.0, 2.0, 5.0], [0.0, 0.0, -1.0], 10.0);

    assert!(hit, "ray should hit the floor");
    assert!((dist - 5.0).abs() < 0.1, "distance should be ~5.0");
    assert!((point[2] - 0.0).abs() < 0.1, "hit point z should be ~0.0");
}

#[test]
fn test_rapier_step_player() {
    let vertices = vec![
        point![0.0, 0.0, 0.0],
        point![20.0, 0.0, 0.0],
        point![20.0, 20.0, 0.0],
        point![0.0, 20.0, 0.0],
    ];
    let indices = vec![[0, 1, 2], [0, 2, 3]];

    let mut physics = RapierPhysicsWorld::new(&vertices, &indices);
    physics.set_position(2.0, 2.0, 0.0);

    let mut player = hassault_native::physics::PlayerState {
        x: 2.0,
        y: 2.0,
        z: 0.0,
        on_ground: true,
        ..Default::default()
    };

    let input = hassault_native::physics::MoveInput {
        forward: 1.0,
        strafe: 0.0,
        jump: false,
        crouch: false,
        sprint: false,
        knife: false,
    };

    for _ in 0..15 {
        physics.step_player(&mut player, input, 0.016);
    }

    assert!(player.x > 2.0, "player should move forward along x (yaw 0)");
    assert!(player.on_ground, "player should remain grounded");
}

#[test]
fn test_rapier_sprint_and_stamina() {
    let vertices = vec![
        point![0.0, 0.0, 0.0],
        point![20.0, 0.0, 0.0],
        point![20.0, 20.0, 0.0],
        point![0.0, 20.0, 0.0],
    ];
    let indices = vec![[0, 1, 2], [0, 2, 3]];

    let mut physics = RapierPhysicsWorld::new(&vertices, &indices);
    physics.set_position(2.0, 2.0, 0.0);

    let mut state = RapierPlayerState {
        x: 2.0,
        y: 2.0,
        z: 0.0,
        grounded: true,
        stamina: 100.0,
        ..Default::default()
    };

    let sprint_input = RapierMoveInput {
        forward: 1.0,
        sprint: true,
        dt: 0.016,
        ..Default::default()
    };

    for _ in 0..20 {
        physics.step(&mut state, sprint_input);
    }

    assert!(state.sprinting, "should be sprinting");
    assert!(state.stamina < 100.0, "stamina should drain");
    let speed = (state.vx * state.vx + state.vy * state.vy).sqrt();
    assert!(speed > hassault_native::physics_rapier::RUN_SPEED, "sprint speed should exceed run speed");
}

#[test]
fn test_rapier_power_slide_and_cancel() {
    let vertices = vec![
        point![0.0, 0.0, 0.0],
        point![20.0, 0.0, 0.0],
        point![20.0, 20.0, 0.0],
        point![0.0, 20.0, 0.0],
    ];
    let indices = vec![[0, 1, 2], [0, 2, 3]];

    let mut physics = RapierPhysicsWorld::new(&vertices, &indices);
    physics.set_position(2.0, 2.0, 0.0);

    let mut state = RapierPlayerState {
        x: 2.0,
        y: 2.0,
        z: 0.0,
        vy: 8.5,
        grounded: true,
        stamina: 100.0,
        ..Default::default()
    };

    let slide_input = RapierMoveInput {
        forward: 1.0,
        crouch: true,
        dt: 0.016,
        ..Default::default()
    };

    physics.step(&mut state, slide_input);
    assert!(state.sliding, "player should enter slide");
    assert!(state.vy >= 8.5, "velocity should be boosted/preserved");

    // Jump to cancel slide
    let cancel_input = RapierMoveInput {
        forward: 1.0,
        jump: true,
        dt: 0.016,
        ..Default::default()
    };

    physics.step(&mut state, cancel_input);
    assert!(!state.sliding, "slide should be canceled");
    assert!(state.vz > 0.0, "jump should launch upward");
    assert!(state.vy > 8.0, "horizontal velocity should be preserved");
}

