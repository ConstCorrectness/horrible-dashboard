import { describe, expect, it, beforeAll } from 'vitest';
import {
  ensureRapierInitialized,
  RapierPhysicsWorld,
  type RapierMoveInput,
  type RapierPlayerState,
} from '../physics-rapier';
import type { CollisionGeometry } from '../world3d';

describe('RapierPhysicsWorld Character Controller', () => {
  let R: any;

  beforeAll(async () => {
    R = await ensureRapierInitialized();
    expect(R).toBeDefined();
  });

  function createTestGeometry(): CollisionGeometry {
    // A simple flat floor from 0..20, 0..20 at z = 0
    // plus a small step at x: 5..10, y: 5..10, z = 0.3
    const vertices = new Float32Array([
      // Floor quad
      0, 0, 0,
      20, 0, 0,
      20, 20, 0,
      0, 20, 0,
      // Step quad (0.3 high)
      5, 5, 0.3,
      10, 5, 0.3,
      10, 10, 0.3,
      5, 10, 0.3,
    ]);

    const indices = new Uint32Array([
      // Floor
      0, 1, 2,
      0, 2, 3,
      // Step
      4, 5, 6,
      4, 6, 7,
    ]);

    return {
      vertices,
      indices,
      triangles: 4,
    };
  }

  it('initializes and steps kinematic character on flat ground', () => {
    const geo = createTestGeometry();
    const physics = new RapierPhysicsWorld(geo);

    physics.setPosition(2, 2, 1);

    const state: RapierPlayerState = {
      x: 2,
      y: 2,
      z: 1,
      vx: 0,
      vy: 0,
      vz: 0,
      grounded: false,
      crouching: false,
    };

    const input: RapierMoveInput = {
      forward: 1,
      strafe: 0,
      yaw: 0, // facing north (+y)
      jump: false,
      crouch: false,
      dt: 0.016,
    };

    // Step 10 frames
    for (let i = 0; i < 10; i++) {
      physics.step(state, input);
    }

    expect(state.y).toBeGreaterThan(2.0); // moved forward
    expect(state.z).toBeLessThanOrEqual(1.0); // fell towards floor

    physics.dispose();
  });

  it('handles jump and gravity arc', () => {
    const geo = createTestGeometry();
    const physics = new RapierPhysicsWorld(geo);

    physics.setPosition(2, 2, 0.9);

    const state: RapierPlayerState = {
      x: 2,
      y: 2,
      z: 0.9,
      vx: 0,
      vy: 0,
      vz: 0,
      grounded: true,
      crouching: false,
    };

    const jumpInput: RapierMoveInput = {
      forward: 0,
      strafe: 0,
      yaw: 0,
      jump: true,
      crouch: false,
      dt: 0.016,
    };

    physics.step(state, jumpInput);
    expect(state.vz).toBeGreaterThan(0); // upward velocity
    expect(state.grounded).toBe(false);

    physics.dispose();
  });

  it('casts rays accurately against collision geometry', () => {
    const geo = createTestGeometry();
    const physics = new RapierPhysicsWorld(geo);

    // Raycast straight down from z = 5 towards floor at z = 0
    const hit = physics.castRay(
      { x: 2, y: 2, z: 5 },
      { x: 0, y: 0, z: -1 },
      10,
    );

    expect(hit.hit).toBe(true);
    expect(hit.dist).toBeCloseTo(5.0, 1);
    expect(hit.point.z).toBeCloseTo(0.0, 1);

    physics.dispose();
  });

  it('steps standard PlayerState seamlessly with crouch and ground detection', () => {
    const geo = createTestGeometry();
    const physics = new RapierPhysicsWorld(geo);

    physics.setPosition(2, 2, 0.0);

    const player = {
      x: 2,
      y: 2,
      z: 0.0,
      velX: 0,
      velY: 0,
      velZ: 0,
      yaw: 0,
      pitch: 0,
      onGround: true,
      crouch: 0,
      crouchHeld: false,
      crouchedInAir: false,
      timeInAir: 0,
      t: 0,
      landedAt: 0,
      fallSpeed: 0,
    };

    const input = {
      forward: 1,
      strafe: 0,
      jump: false,
      crouch: false,
      noclip: false,
    };

    for (let i = 0; i < 15; i++) {
      physics.stepPlayer(player, input, 0.016);
    }

    expect(player.x).toBeGreaterThan(2.0); // moved forward (yaw 0 is +x)
    expect(player.onGround).toBe(true);

    physics.dispose();
  });

  it('accelerates when sprinting and drains stamina', () => {
    const geo = createTestGeometry();
    const physics = new RapierPhysicsWorld(geo);
    physics.setPosition(2, 2, 0.0);

    const state: RapierPlayerState = {
      x: 2,
      y: 2,
      z: 0.0,
      vx: 0,
      vy: 0,
      vz: 0,
      grounded: true,
      crouching: false,
      stamina: 100,
    };

    const sprintInput: RapierMoveInput = {
      forward: 1,
      strafe: 0,
      yaw: 0,
      jump: false,
      crouch: false,
      sprint: true,
      dt: 0.016,
    };

    for (let i = 0; i < 20; i++) {
      physics.step(state, sprintInput);
    }

    expect(state.sprinting).toBe(true);
    expect(state.stamina).toBeLessThan(100);
    expect(Math.hypot(state.vx, state.vy)).toBeGreaterThan(RapierPhysicsWorld.RUN_SPEED);

    physics.dispose();
  });

  it('enters crouch power-slide and executes slide cancel jump', () => {
    const geo = createTestGeometry();
    const physics = new RapierPhysicsWorld(geo);
    physics.setPosition(2, 2, 0.0);

    const state: RapierPlayerState = {
      x: 2,
      y: 2,
      z: 0.0,
      vx: 0,
      vy: 8.5, // moving fast along y
      vz: 0,
      grounded: true,
      crouching: false,
      stamina: 100,
    };

    const slideInput: RapierMoveInput = {
      forward: 1,
      strafe: 0,
      yaw: 0,
      jump: false,
      crouch: true, // trigger slide
      dt: 0.016,
    };

    physics.step(state, slideInput);
    expect(state.sliding).toBe(true);
    expect(state.vy).toBeGreaterThanOrEqual(8.5); // boosted/preserved velocity

    // Now press jump to slide-cancel
    const jumpCancelInput: RapierMoveInput = {
      forward: 1,
      strafe: 0,
      yaw: 0,
      jump: true,
      crouch: false,
      dt: 0.016,
    };

    physics.step(state, jumpCancelInput);
    expect(state.sliding).toBe(false); // slide canceled
    expect(state.vz).toBeGreaterThan(0); // jumped!
    expect(state.vy).toBeGreaterThan(8.0); // forward speed preserved!

    physics.dispose();
  });
});

