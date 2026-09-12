import { describe, expect, it, beforeAll } from 'vitest';
import {
  ensureRapierInitialized,
  RapierPhysicsWorld,
  type RapierMoveInput,
  type RapierPlayerState,
} from '../physics-rapier';
import type { CollisionGeometry } from '../world3d';

describe('RapierPhysicsWorld Character Controller', () => {
  // The Rapier module namespace, taken from the initializer rather than `any`:
  // the type is already in scope, and `any` here hides a signature change in the
  // exact place a wasm-backed API is most likely to shift under you.
  let R: Awaited<ReturnType<typeof ensureRapierInitialized>>;

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

  it('generates 3D bank world (hd_bank) with matching spawns and valid Rapier mesh', async () => {
    const THREE = await import('three');
    const { createWorld3D } = await import('../world3d');

    const world = createWorld3D(THREE, {
      name: 'hd_bank',
      title: 'The Bank',
      ssize: 64,
    } as any);

    expect(world.collision.triangles).toBeGreaterThan(0);
    expect(world.spawns.all.length).toBe(8);
    expect(world.spawns.cla.length).toBe(4);
    expect(world.spawns.rvsf.length).toBe(4);
    expect(world.items.length).toBe(10);
    expect(world.waterlevel).toBe(-100);

    const physics = new RapierPhysicsWorld(world.collision);
    expect(physics).toBeDefined();

    // Verify raycast straight down to street (z = 0)
    const hitStreet = physics.castRay({ x: 20, y: 8, z: 5 }, { x: 0, y: 0, z: -1 }, 10);
    expect(hitStreet.hit).toBe(true);
    expect(hitStreet.point.z).toBeCloseTo(0.0, 1);

    // Verify raycast down to SWAT van roof (z = 2.8)
    const hitVan = physics.castRay({ x: 20, y: 14, z: 5 }, { x: 0, y: 0, z: -1 }, 10);
    expect(hitVan.hit).toBe(true);
    expect(hitVan.point.z).toBeCloseTo(2.8, 1);

    // Verify raycast down to entrance canopy (z = 4.8)
    const hitCanopy = physics.castRay({ x: 30, y: 17, z: 7 }, { x: 0, y: 0, z: -1 }, 10);
    expect(hitCanopy.hit).toBe(true);
    expect(hitCanopy.point.z).toBeCloseTo(4.8, 1);

    // Verify raycast inside The Vault (Site A) hitting gold pallets (z = 1.8)
    const hitVault = physics.castRay({ x: 45, y: 54, z: 5 }, { x: 0, y: 0, z: -1 }, 10);
    expect(hitVault.hit).toBe(true);
    expect(hitVault.point.z).toBeCloseTo(1.8, 1);

    physics.dispose();
    world.dispose();
  });

  it('verifies that all vertex normals in hd_bank are valid, non-NaN, and floors/ramps face upwards', async () => {
    const THREE = await import('three');
    const { createWorld3D } = await import('../world3d');

    const world = createWorld3D(THREE, {
      name: 'hd_bank',
      title: 'The Bank',
      ssize: 64,
    } as any);

    let totalNormalsChecked = 0;
    let upwardFloorCount = 0;

    world.scene.traverse((child) => {
      const mesh = child as import('three').Mesh;
      if (!mesh.isMesh) return;
      const normalAttr = mesh.geometry.getAttribute('normal');
      expect(normalAttr).toBeDefined();

      for (let i = 0; i < normalAttr.count; i++) {
        const nx = normalAttr.getX(i);
        const ny = normalAttr.getY(i);
        const nz = normalAttr.getZ(i);

        // Crucial: No NaN or Infinite normals allowed anywhere
        expect(Number.isNaN(nx)).toBe(false);
        expect(Number.isNaN(ny)).toBe(false);
        expect(Number.isNaN(nz)).toBe(false);
        expect(Number.isFinite(nx)).toBe(true);
        expect(Number.isFinite(ny)).toBe(true);
        expect(Number.isFinite(nz)).toBe(true);

        totalNormalsChecked++;
        // Three.js Y is UP (elevation in world coords)
        if (ny > 0.8) {
          upwardFloorCount++;
        }
      }
    });

    expect(totalNormalsChecked).toBeGreaterThan(100);
    // There must be many upward facing floor/ramp normals
    expect(upwardFloorCount).toBeGreaterThan(20);

    world.dispose();
  });

  it('generates 3D assault world (hd_assault) with multi-level catwalks, roof, vents, and valid Rapier mesh', async () => {
    const THREE = await import('three');
    const { createWorld3D } = await import('../world3d');

    const world = createWorld3D(THREE, {
      name: 'hd_assault',
      title: 'Assault',
      ssize: 64,
    } as any);

    expect(world.collision.triangles).toBeGreaterThan(0);
    expect(world.spawns.all.length).toBe(8);
    expect(world.spawns.cla.length).toBe(4);
    expect(world.spawns.rvsf.length).toBe(4);
    expect(world.items.length).toBe(10);
    expect(world.waterlevel).toBe(-100);

    const physics = new RapierPhysicsWorld(world.collision);
    expect(physics).toBeDefined();

    // Verify raycast straight down to street (z = 0)
    const hitStreet = physics.castRay({ x: 20, y: 8, z: 5 }, { x: 0, y: 0, z: -1 }, 10);
    expect(hitStreet.hit).toBe(true);
    expect(hitStreet.point.z).toBeCloseTo(0.0, 1);

    // Verify raycast down onto Highway Bridge Deck (z = 9)
    const hitBridge = physics.castRay({ x: 28, y: 10, z: 12 }, { x: 0, y: 0, z: -1 }, 10);
    expect(hitBridge.hit).toBe(true);
    expect(hitBridge.point.z).toBeCloseTo(9.0, 1);

    // Verify raycast down under Highway Bridge onto street (z = 0)
    const hitUnder = physics.castRay({ x: 28, y: 10, z: 5 }, { x: 0, y: 0, z: -1 }, 10);
    expect(hitUnder.hit).toBe(true);
    expect(hitUnder.point.z).toBeCloseTo(0.0, 1);

    // Verify raycast down onto Warehouse Rooftop (z = 8)
    const hitRoof = physics.castRay({ x: 20, y: 34, z: 12 }, { x: 0, y: 0, z: -1 }, 10);
    expect(hitRoof.hit).toBe(true);
    expect(hitRoof.point.z).toBeCloseTo(8.0, 1);

    // Verify raycast down onto Elevated Catwalk (z = 4.2)
    const hitCatwalk = physics.castRay({ x: 13, y: 36, z: 6 }, { x: 0, y: 0, z: -1 }, 10);
    expect(hitCatwalk.hit).toBe(true);
    expect(hitCatwalk.point.z).toBeCloseTo(4.2, 1);

    // Verify raycast down under the Catwalk onto ground floor (z = 0)
    const hitUnderCatwalk = physics.castRay({ x: 13, y: 36, z: 3 }, { x: 0, y: 0, z: -1 }, 10);
    expect(hitUnderCatwalk.hit).toBe(true);
    expect(hitUnderCatwalk.point.z).toBeCloseTo(0.0, 1);

    // Verify raycast down onto Hostage Office 2nd floor (z = 4.2)
    const hitOffice = physics.castRay({ x: 20, y: 50, z: 6 }, { x: 0, y: 0, z: -1 }, 10);
    expect(hitOffice.hit).toBe(true);
    expect(hitOffice.point.z).toBeCloseTo(4.2, 1);

    physics.dispose();
    world.dispose();
  });
});


