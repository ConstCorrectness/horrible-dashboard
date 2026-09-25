/**
 * Rapier3D Character Controller & Physics Simulation.
 *
 * Implements an industry-standard modern FPS character controller using
 * Rapier3D WebAssembly (@dimforge/rapier3d-compat).
 *
 * Provides:
 * - Capsule-against-trimesh continuous collision detection.
 * - Smooth slope climbing (up to 45°) and automatic sliding on steep inclines.
 * - Automatic stair stepping (up to STEP_HEIGHT = 0.4).
 * - Smooth crouch transitions (shrinking collision capsule).
 * - High-speed momentum preservation (strafe jumping, slide hopping).
 * - Fast raycasting for bullet penetration and line-of-sight checks.
 */
import { UNITS_PER_METRE } from './glb-colliders';
import RAPIER from '@dimforge/rapier3d-compat';
import type { BreakableWindow, CollisionGeometry } from './world3d';
import type { MoveInput, PlayerState } from './player';

let rapierInitialized = false;

export async function ensureRapierInitialized(): Promise<typeof RAPIER> {
  if (!rapierInitialized) {
    await RAPIER.init();
    rapierInitialized = true;
  }
  return RAPIER;
}

export interface RapierMoveInput {
  forward: number; // -1 to 1
  strafe: number;  // -1 to 1
  yaw: number;     // degrees
  jump: boolean;
  crouch: boolean;
  sprint?: boolean;
  dt: number;
}

export interface RapierPlayerState {
  x: number;
  y: number;
  z: number;
  vx: number;
  vy: number;
  vz: number;
  grounded: boolean;
  crouching: boolean;
  stamina?: number;
  sprinting?: boolean;
  sliding?: boolean;
  slideTime?: number;
}

export const MATERIAL_PENETRATION: Record<string, number> = {
  wood: 0.85,
  drywall: 0.90,
  metal: 0.45,
  stone: 0.30,
  glass: 0.95,
};

export function calculateMaterialPenetration(
  weaponId: string,
  material: string = 'wood',
  thickness: number = 0.5,
): { canPenetrate: boolean; damageFactor: number } {
  const matFactor = MATERIAL_PENETRATION[material.toLowerCase()] ?? 0.5;
  let maxThickness = 0.5;
  let baseFalloff = 0.5;

  if (weaponId === 'sniper') {
    maxThickness = 1.8 * matFactor;
    baseFalloff = 0.75;
  } else if (weaponId === 'assault' || weaponId === 'carbine') {
    maxThickness = 1.0 * matFactor;
    baseFalloff = 0.55;
  } else if (weaponId === 'subgun' || weaponId === 'pistol') {
    maxThickness = 0.45 * matFactor;
    baseFalloff = 0.35;
  } else if (weaponId === 'shotgun') {
    maxThickness = 0.30 * matFactor;
    baseFalloff = 0.25;
  } else {
    return { canPenetrate: false, damageFactor: 0 };
  }

  if (thickness > maxThickness) {
    return { canPenetrate: false, damageFactor: 0 };
  }

  const penetrationRatio = 1.0 - (thickness / maxThickness) * 0.4;
  const damageFactor = Math.max(0.1, Math.min(0.9, baseFalloff * matFactor * penetrationRatio));
  return { canPenetrate: true, damageFactor: Math.round(damageFactor * 1000) / 1000 };
}

export interface RayPenetrationHit {
  hit: boolean;
  dist: number;
  point: { x: number; y: number; z: number };
  normal: { x: number; y: number; z: number };
  wallbang: boolean;
  damageFactor: number;
  shatteredWindows: string[];
}

export class RapierPhysicsWorld {
  readonly world: RAPIER.World;
  readonly characterController: RAPIER.KinematicCharacterController;
  private playerBody: RAPIER.RigidBody;
  private standingCollider: RAPIER.Collider;
  private crouchingCollider: RAPIER.Collider;
  private activeCollider: RAPIER.Collider;
  private isCrouched = false;
  private windowColliders = new Map<string, RAPIER.Collider>();
  private colliderToWindowId = new Map<number, string>();

  // Constants. Tuned in metres and multiplied by `U` (cubes per metre): the
  // modelled maps are exported at that scale (`tools/blender/maplib.py`), and a
  // controller left in metres was a 1.8-unit capsule in a world built for a
  // 5.2-unit body — small enough to slip through gaps the avatar visibly cannot.
  // Scaling every length (and speed, and acceleration) by the same factor keeps
  // the tuned feel exactly: it is the same physics, measured in cubes.
  static readonly U = UNITS_PER_METRE;
  static readonly CAPSULE_RADIUS = 0.45 * UNITS_PER_METRE;
  static readonly STANDING_HALF_HEIGHT = 0.45 * UNITS_PER_METRE; // standing: 1.8 m
  static readonly CROUCH_HALF_HEIGHT = 0.15 * UNITS_PER_METRE; // crouched: 1.2 m
  static readonly MAX_SLOPE = (45 * Math.PI) / 180;
  static readonly STEP_HEIGHT = 0.4 * UNITS_PER_METRE;
  static readonly RUN_SPEED = 8.5 * UNITS_PER_METRE;
  static readonly SPRINT_SPEED = 11.5 * UNITS_PER_METRE;
  static readonly CROUCH_SPEED = 3.8 * UNITS_PER_METRE;
  static readonly SLIDE_INITIAL_SPEED = 13.0 * UNITS_PER_METRE;
  static readonly MAX_SLIDE_TIME = 0.8;
  static readonly STAMINA_DRAIN = 25.0;
  static readonly STAMINA_RECOVERY = 30.0;
  static readonly MAX_STAMINA = 100.0;
  static readonly JUMP_VELOCITY = 7.2 * UNITS_PER_METRE;
  static readonly GRAVITY = -22.0 * UNITS_PER_METRE;

  constructor(collision: CollisionGeometry, windows?: Map<string, BreakableWindow>) {
    // Gravity pointing down along Z in game coordinates
    const gravity = new RAPIER.Vector3(0.0, 0.0, RapierPhysicsWorld.GRAVITY);
    this.world = new RAPIER.World(gravity);

    // Create Trimesh collider for level geometry
    if (collision.vertices.length > 0 && collision.indices.length > 0) {
      const trimeshDesc = RAPIER.ColliderDesc.trimesh(collision.vertices, collision.indices);
      this.world.createCollider(trimeshDesc);
    }

    if (windows) {
      for (const [id, win] of windows.entries()) {
        if (win.vertices.length > 0 && win.indices.length > 0) {
          const desc = RAPIER.ColliderDesc.trimesh(win.vertices, win.indices);
          const col = this.world.createCollider(desc);
          this.windowColliders.set(id, col);
          this.colliderToWindowId.set(col.handle, id);
        }
      }
    }

    // Configure Kinematic Character Controller (Z-up)
    const offset = 0.04 * RapierPhysicsWorld.U;
    this.characterController = this.world.createCharacterController(offset);
    this.characterController.setUp(new RAPIER.Vector3(0.0, 0.0, 1.0));
    this.characterController.enableAutostep(RapierPhysicsWorld.STEP_HEIGHT, 0.2 * RapierPhysicsWorld.U, true);
    this.characterController.setMaxSlopeClimbAngle(RapierPhysicsWorld.MAX_SLOPE);
    this.characterController.setMinSlopeSlideAngle(RapierPhysicsWorld.MAX_SLOPE);
    this.characterController.enableSnapToGround(0.35 * RapierPhysicsWorld.U);

    // Create Player Kinematic RigidBody
    const bodyDesc = RAPIER.RigidBodyDesc.kinematicPositionBased().setTranslation(10.0, 10.0, 1.0);
    this.playerBody = this.world.createRigidBody(bodyDesc);

    // Standing Capsule Collider (oriented along Z, offset so body origin is at feet)
    const standingDesc = RAPIER.ColliderDesc.capsule(
      RapierPhysicsWorld.STANDING_HALF_HEIGHT,
      RapierPhysicsWorld.CAPSULE_RADIUS,
    );
    standingDesc.setRotation({ x: Math.SQRT1_2, y: 0, z: 0, w: Math.SQRT1_2 });
    standingDesc.setTranslation(0, 0, RapierPhysicsWorld.STANDING_HALF_HEIGHT + RapierPhysicsWorld.CAPSULE_RADIUS);
    this.standingCollider = this.world.createCollider(standingDesc, this.playerBody);

    // Crouching Capsule Collider
    const crouchDesc = RAPIER.ColliderDesc.capsule(
      RapierPhysicsWorld.CROUCH_HALF_HEIGHT,
      RapierPhysicsWorld.CAPSULE_RADIUS,
    );
    crouchDesc.setRotation({ x: Math.SQRT1_2, y: 0, z: 0, w: Math.SQRT1_2 });
    crouchDesc.setTranslation(0, 0, RapierPhysicsWorld.CROUCH_HALF_HEIGHT + RapierPhysicsWorld.CAPSULE_RADIUS);
    this.crouchingCollider = this.world.createCollider(crouchDesc, this.playerBody);
    this.crouchingCollider.setEnabled(false);

    this.activeCollider = this.standingCollider;

    // Step once to build spatial BVH structures for immediate raycasting
    this.world.step();
  }

  public setPosition(x: number, y: number, z: number): void {
    this.playerBody.setTranslation(new RAPIER.Vector3(x, y, z), true);
    this.world.step();
  }

  public getPosition(): { x: number; y: number; z: number } {
    const t = this.playerBody.translation();
    return { x: t.x, y: t.y, z: t.z };
  }

  public step(state: RapierPlayerState, input: RapierMoveInput): RapierPlayerState {
    const dt = Math.min(input.dt, 0.05);

    // Stamina calculation
    const stamina = state.stamina ?? RapierPhysicsWorld.MAX_STAMINA;
    const isSprinting = Boolean(
      input.sprint && input.forward > 0.1 && state.grounded && !this.isCrouched && stamina > 0,
    );
    state.sprinting = isSprinting;
    state.stamina = isSprinting
      ? Math.max(0, stamina - RapierPhysicsWorld.STAMINA_DRAIN * dt)
      : Math.min(RapierPhysicsWorld.MAX_STAMINA, stamina + RapierPhysicsWorld.STAMINA_RECOVERY * dt);

    const hSpeed = Math.hypot(state.vx, state.vy);

    // Crouch Power-Slide Trigger & Slide-Canceling
    if (input.crouch && state.grounded && !state.sliding && (isSprinting || hSpeed > 6.0 * RapierPhysicsWorld.U)) {
      state.sliding = true;
      state.slideTime = 0;
      const boost = Math.max(1.15, RapierPhysicsWorld.SLIDE_INITIAL_SPEED / Math.max(0.1, hSpeed));
      const targetSpeed = Math.min(14.0 * RapierPhysicsWorld.U, hSpeed * boost);
      const scale = targetSpeed / Math.max(0.1, hSpeed);
      state.vx *= scale;
      state.vy *= scale;
    }

    if (state.sliding) {
      state.slideTime = (state.slideTime ?? 0) + dt;
      // Slide Cancel Jump
      if (input.jump) {
        state.sliding = false;
        state.vz = RapierPhysicsWorld.JUMP_VELOCITY;
        state.grounded = false;
      } else {
        input.crouch = true; // force crouch in slide
        // Downhill Slope Acceleration
        const groundHit = this.getGroundHit();
        if (groundHit && groundHit.normal) {
          const norm = groundHit.normal;
          const curSpeed = Math.hypot(state.vx, state.vy);
          if (curSpeed > 0.1) {
            const dirX = state.vx / curSpeed;
            const dirY = state.vy / curSpeed;
            const downhillDot = dirX * (-norm.x) + dirY * (-norm.y);
            const slopeSin = Math.sqrt(norm.x * norm.x + norm.y * norm.y);
            if (downhillDot > 0.05 && slopeSin > 0.05) {
              const downhillAccel = Math.abs(RapierPhysicsWorld.GRAVITY) * slopeSin * downhillDot * 1.5;
              state.vx += dirX * downhillAccel * dt;
              state.vy += dirY * downhillAccel * dt;
            }
          }
        }
        if (
          state.slideTime >= RapierPhysicsWorld.MAX_SLIDE_TIME ||
          Math.hypot(state.vx, state.vy) < 3.5 * RapierPhysicsWorld.U ||
          !input.crouch
        ) {
          state.sliding = false;
        }
      }
    }

    // Handle Crouch Transition
    if (input.crouch !== this.isCrouched) {
      if (input.crouch) {
        // Can always crouch down
        this.isCrouched = true;
        this.standingCollider.setEnabled(false);
        this.crouchingCollider.setEnabled(true);
        this.activeCollider = this.crouchingCollider;
      } else {
        // Headroom check before standing up
        const canStand = this.checkHeadroom();
        if (canStand) {
          this.isCrouched = false;
          this.crouchingCollider.setEnabled(false);
          this.standingCollider.setEnabled(true);
          this.activeCollider = this.standingCollider;
        }
      }
    }

    // Direction from yaw and inputs
    const rad = (input.yaw * Math.PI) / 180;
    const forwardX = -Math.sin(rad);
    const forwardY = Math.cos(rad);
    const rightX = Math.cos(rad);
    const rightY = Math.sin(rad);

    let wishX = forwardX * input.forward + rightX * input.strafe;
    let wishY = forwardY * input.forward + rightY * input.strafe;
    const len = Math.hypot(wishX, wishY);
    if (len > 0.001) {
      wishX /= len;
      wishY /= len;
    }

    let speed = RapierPhysicsWorld.RUN_SPEED;
    if (this.isCrouched) {
      speed = RapierPhysicsWorld.CROUCH_SPEED;
    } else if (isSprinting) {
      speed = RapierPhysicsWorld.SPRINT_SPEED;
    }

    // Horizontal acceleration / friction / air strafe
    if (!state.grounded) {
      // Air strafe momentum: lateral input adds tangential impulse up to 15 m/s
      if (Math.abs(input.strafe) > 0.1) {
        const airStrafeAccel = 18.0 * RapierPhysicsWorld.U;
        state.vx += rightX * input.strafe * airStrafeAccel * dt;
        state.vy += rightY * input.strafe * airStrafeAccel * dt;
        const totalH = Math.hypot(state.vx, state.vy);
        if (totalH > 15.0 * RapierPhysicsWorld.U) {
          state.vx = (state.vx / totalH) * 15.0 * RapierPhysicsWorld.U;
          state.vy = (state.vy / totalH) * 15.0 * RapierPhysicsWorld.U;
        }
      } else {
        state.vx += (wishX * speed - state.vx) * Math.min(1.0, 2.5 * dt);
        state.vy += (wishY * speed - state.vy) * Math.min(1.0, 2.5 * dt);
      }
    } else if (!state.sliding) {
      state.vx += (wishX * speed - state.vx) * Math.min(1.0, 12.0 * dt);
      state.vy += (wishY * speed - state.vy) * Math.min(1.0, 12.0 * dt);
    } else {
      // Gentle slide deceleration
      const slideFriction = 3.5;
      state.vx -= state.vx * Math.min(1.0, slideFriction * dt);
      state.vy -= state.vy * Math.min(1.0, slideFriction * dt);
    }

    // Jump
    if (input.jump && state.grounded) {
      state.vz = RapierPhysicsWorld.JUMP_VELOCITY;
      state.grounded = false;
    } else if (!state.grounded) {
      // Gravity
      state.vz += RapierPhysicsWorld.GRAVITY * dt;
    }

    let desiredZ = state.vz * dt;
    if (state.grounded && desiredZ <= 0) {
      desiredZ = -0.05 * RapierPhysicsWorld.U;
    }

    // Desired displacement this tick
    const desiredTranslation = new RAPIER.Vector3(
      state.vx * dt,
      state.vy * dt,
      desiredZ,
    );

    // Compute kinematic movement with collision resolution
    this.characterController.computeColliderMovement(this.activeCollider, desiredTranslation);

    const movement = this.characterController.computedMovement();
    const curPos = this.playerBody.translation();
    const newPos = new RAPIER.Vector3(
      curPos.x + movement.x,
      curPos.y + movement.y,
      curPos.z + movement.z,
    );

    this.playerBody.setTranslation(newPos, true);
    this.world.step();

    const wasGrounded = state.grounded;
    state.grounded = this.characterController.computedGrounded();

    if (state.grounded && !wasGrounded) {
      state.vz = 0;
    }

    state.x = newPos.x;
    state.y = newPos.y;
    state.z = newPos.z;
    state.crouching = this.isCrouched;

    return state;
  }

  /**
   * Step the full player state directly, interoperable with HorribleAssault's PlayerState.
   */
  public stepPlayer(player: PlayerState, input: MoveInput, dt: number): void {
    dt = Math.min(dt, 0.05);
    if (dt <= 0) return;

    if (input.noclip) {
      const sin = Math.sin(player.yaw);
      const cos = Math.cos(player.yaw);
      const dx = cos * input.forward - sin * input.strafe;
      const dy = sin * input.forward + cos * input.strafe;
      player.x += dx * RapierPhysicsWorld.RUN_SPEED * dt;
      player.y += dy * RapierPhysicsWorld.RUN_SPEED * dt;
      player.z += input.jump ? RapierPhysicsWorld.RUN_SPEED * dt : 0;
      player.velX = 0;
      player.velY = 0;
      player.velZ = 0;
      player.onGround = false;
      player.fallSpeed = 0;
      player.t += dt;
      this.setPosition(player.x, player.y, player.z);
      return;
    }

    player.t += dt;
    player.fallSpeed = 0;

    // Stamina pool update
    const stamina = player.stamina ?? RapierPhysicsWorld.MAX_STAMINA;
    const isSprinting = Boolean(
      input.sprint && input.forward > 0.1 && player.onGround && !this.isCrouched && stamina > 0,
    );
    player.isSprinting = isSprinting;
    player.stamina = isSprinting
      ? Math.max(0, stamina - RapierPhysicsWorld.STAMINA_DRAIN * dt)
      : Math.min(RapierPhysicsWorld.MAX_STAMINA, stamina + RapierPhysicsWorld.STAMINA_RECOVERY * dt);

    const hSpeed = Math.hypot(player.velX, player.velY);

    // Crouch Power-Slide & Slide Cancel
    if (input.crouch && player.onGround && !player.isSliding && (isSprinting || hSpeed > 6.0 * RapierPhysicsWorld.U)) {
      player.isSliding = true;
      player.slideTime = 0;
      const boost = Math.max(1.15, RapierPhysicsWorld.SLIDE_INITIAL_SPEED / Math.max(0.1, hSpeed));
      const targetSpeed = Math.min(14.0 * RapierPhysicsWorld.U, hSpeed * boost);
      const scale = targetSpeed / Math.max(0.1, hSpeed);
      player.velX *= scale;
      player.velY *= scale;
    }

    if (player.isSliding) {
      player.slideTime = (player.slideTime ?? 0) + dt;
      if (input.jump) {
        // Slide Cancel into full-speed jump!
        player.isSliding = false;
        player.velZ = RapierPhysicsWorld.JUMP_VELOCITY;
        player.onGround = false;
      } else {
        input.crouch = true; // force crouch in slide
        const groundHit = this.getGroundHit();
        if (groundHit && groundHit.normal) {
          const norm = groundHit.normal;
          const curSpeed = Math.hypot(player.velX, player.velY);
          if (curSpeed > 0.1) {
            const dirX = player.velX / curSpeed;
            const dirY = player.velY / curSpeed;
            const downhillDot = dirX * (-norm.x) + dirY * (-norm.y);
            const slopeSin = Math.sqrt(norm.x * norm.x + norm.y * norm.y);
            if (downhillDot > 0.05 && slopeSin > 0.05) {
              const downhillAccel = Math.abs(RapierPhysicsWorld.GRAVITY) * slopeSin * downhillDot * 1.5;
              player.velX += dirX * downhillAccel * dt;
              player.velY += dirY * downhillAccel * dt;
            }
          }
        }
        if (
          player.slideTime >= RapierPhysicsWorld.MAX_SLIDE_TIME ||
          Math.hypot(player.velX, player.velY) < 3.5 * RapierPhysicsWorld.U ||
          !input.crouch
        ) {
          player.isSliding = false;
        }
      }
    }

    // Crouch transition
    const wantCrouch = input.crouch;
    if (wantCrouch !== this.isCrouched) {
      if (wantCrouch) {
        this.isCrouched = true;
        this.standingCollider.setEnabled(false);
        this.crouchingCollider.setEnabled(true);
        this.activeCollider = this.crouchingCollider;
      } else {
        if (this.checkHeadroom()) {
          this.isCrouched = false;
          this.crouchingCollider.setEnabled(false);
          this.standingCollider.setEnabled(true);
          this.activeCollider = this.standingCollider;
        }
      }
    }
    player.crouch = this.isCrouched ? 1 : 0;
    player.crouchHeld = wantCrouch;

    // Direction from yaw (yaw is in radians: cos is X, sin is Y)
    const sin = Math.sin(player.yaw);
    const cos = Math.cos(player.yaw);
    let wishX = cos * input.forward - sin * input.strafe;
    let wishY = sin * input.forward + cos * input.strafe;
    const len = Math.hypot(wishX, wishY);
    if (len > 0.001) {
      wishX /= len;
      wishY /= len;
    }

    let speed = RapierPhysicsWorld.RUN_SPEED;
    if (this.isCrouched) {
      speed = RapierPhysicsWorld.CROUCH_SPEED;
    } else if (isSprinting) {
      speed = RapierPhysicsWorld.SPRINT_SPEED;
    }

    // Air-strafing or Ground Movement
    if (!player.onGround) {
      if (Math.abs(input.strafe) > 0.1) {
        const airStrafeAccel = 18.0 * RapierPhysicsWorld.U;
        // Right vector in world: (-sin(yaw), cos(yaw))
        player.velX += -sin * input.strafe * airStrafeAccel * dt;
        player.velY += cos * input.strafe * airStrafeAccel * dt;
        const curAir = Math.hypot(player.velX, player.velY);
        if (curAir > 15.0 * RapierPhysicsWorld.U) {
          player.velX = (player.velX / curAir) * 15.0 * RapierPhysicsWorld.U;
          player.velY = (player.velY / curAir) * 15.0 * RapierPhysicsWorld.U;
        }
      } else {
        player.velX += (wishX * speed - player.velX) * Math.min(1.0, 2.5 * dt);
        player.velY += (wishY * speed - player.velY) * Math.min(1.0, 2.5 * dt);
      }
    } else if (!player.isSliding) {
      player.velX += (wishX * speed - player.velX) * Math.min(1.0, 12.0 * dt);
      player.velY += (wishY * speed - player.velY) * Math.min(1.0, 12.0 * dt);
    } else {
      const slideFriction = 3.5;
      player.velX -= player.velX * Math.min(1.0, slideFriction * dt);
      player.velY -= player.velY * Math.min(1.0, slideFriction * dt);
    }

    // Bunny-hop landing grace period (80ms timing window)
    const bhopTiming = player.onGround && input.jump && (player.t - player.landedAt <= 0.08);

    if ((input.jump && player.onGround) || bhopTiming) {
      player.velZ = RapierPhysicsWorld.JUMP_VELOCITY;
      player.onGround = false;
    } else if (!player.onGround) {
      player.velZ += RapierPhysicsWorld.GRAVITY * dt;
    }

    let desiredZ = player.velZ * dt;
    if (player.onGround && desiredZ <= 0) {
      desiredZ = -0.05 * RapierPhysicsWorld.U;
    }

    const desired = new RAPIER.Vector3(
      player.velX * dt,
      player.velY * dt,
      desiredZ,
    );

    this.characterController.computeColliderMovement(this.activeCollider, desired);
    const movement = this.characterController.computedMovement();
    const curPos = this.playerBody.translation();
    const newPos = new RAPIER.Vector3(
      curPos.x + movement.x,
      curPos.y + movement.y,
      curPos.z + movement.z,
    );
    this.playerBody.setTranslation(newPos, true);
    this.world.step();

    player.x = newPos.x;
    player.y = newPos.y;
    player.z = newPos.z;

    const wasOnGround = player.onGround;
    player.onGround = this.characterController.computedGrounded();

    if (player.onGround && !wasOnGround) {
      player.fallSpeed = Math.abs(player.velZ);
      player.velZ = 0;
      player.landedAt = player.t;
    }
  }

  /** Cast downward ray to retrieve surface normal of ground below feet. */
  private getGroundHit(): { normal: { x: number; y: number; z: number } } | null {
    const pos = this.playerBody.translation();
    const ray = new RAPIER.Ray(
      new RAPIER.Vector3(pos.x, pos.y, pos.z + 0.2 * RapierPhysicsWorld.U),
      new RAPIER.Vector3(0, 0, -1),
    );
    const hit = this.world.castRayAndGetNormal(ray, 0.6 * RapierPhysicsWorld.U, true);
    if (!hit) return null;
    return { normal: hit.normal };
  }

  /** Check if player has headroom to stand up without colliding. */
  private checkHeadroom(): boolean {
    const pos = this.playerBody.translation();
    const ray = new RAPIER.Ray(
      new RAPIER.Vector3(pos.x, pos.y, pos.z + RapierPhysicsWorld.CROUCH_HALF_HEIGHT),
      new RAPIER.Vector3(0, 0, 1),
    );
    const hit = this.world.castRay(ray, 0.7 * RapierPhysicsWorld.U, true);
    return hit === null;
  }

  /** Raycast for weapons: returns distance and hit point if obstacle is struck. */
  public castRay(
    origin: { x: number; y: number; z: number },
    dir: { x: number; y: number; z: number },
    maxDist: number,
  ): { hit: boolean; dist: number; point: { x: number; y: number; z: number } } {
    const ray = new RAPIER.Ray(
      new RAPIER.Vector3(origin.x, origin.y, origin.z),
      new RAPIER.Vector3(dir.x, dir.y, dir.z),
    );
    const hit = this.world.castRayAndGetNormal(ray, maxDist, true);
    if (!hit) {
      return {
        hit: false,
        dist: maxDist,
        point: {
          x: origin.x + dir.x * maxDist,
          y: origin.y + dir.y * maxDist,
          z: origin.z + dir.z * maxDist,
        },
      };
    }

    return {
      hit: true,
      dist: hit.timeOfImpact,
      point: {
        x: origin.x + dir.x * hit.timeOfImpact,
        y: origin.y + dir.y * hit.timeOfImpact,
        z: origin.z + dir.z * hit.timeOfImpact,
      },
    };
  }

  public shatterWindow(id: string): boolean {
    const col = this.windowColliders.get(id);
    if (col && col.isEnabled()) {
      col.setEnabled(false);
      return true;
    }
    return false;
  }

  /**
   * Penetrating raycast for weapons: shoots through windows (shattering them)
   * and penetrable barriers (wood, drywall, thin metal) with realistic damage attenuation.
   */
  public castRayPenetrating(
    origin: { x: number; y: number; z: number },
    dir: { x: number; y: number; z: number },
    maxDist: number,
    weaponId: string = 'assault',
  ): RayPenetrationHit {
    let curX = origin.x;
    let curY = origin.y;
    let curZ = origin.z;
    let remainingDist = maxDist;
    let totalDist = 0;
    let accumDamageFactor = 1.0;
    let wallbang = false;
    const shatteredWindows: string[] = [];
    const maxPenetrations = 3;
    let penetrations = 0;

    while (remainingDist > 0.05 * RapierPhysicsWorld.U && penetrations < maxPenetrations) {
      const ray = new RAPIER.Ray(
        new RAPIER.Vector3(curX, curY, curZ),
        new RAPIER.Vector3(dir.x, dir.y, dir.z),
      );
      const hit = this.world.castRayAndGetNormal(ray, remainingDist, true);
      if (!hit) {
        return {
          hit: false,
          dist: maxDist,
          point: {
            x: origin.x + dir.x * maxDist,
            y: origin.y + dir.y * maxDist,
            z: origin.z + dir.z * maxDist,
          },
          normal: { x: 0, y: 0, z: 1 },
          wallbang,
          damageFactor: accumDamageFactor,
          shatteredWindows,
        };
      }

      const hitDist = hit.timeOfImpact;
      totalDist += hitDist;
      const hitPoint = {
        x: curX + dir.x * hitDist,
        y: curY + dir.y * hitDist,
        z: curZ + dir.z * hitDist,
      };
      const hitNormal = {
        x: hit.normal.x,
        y: hit.normal.y,
        z: hit.normal.z,
      };

      // Check if struck collider is a window
      const winId = hit.collider ? this.colliderToWindowId.get(hit.collider.handle) : undefined;
      if (winId) {
        this.shatterWindow(winId);
        shatteredWindows.push(winId);
        accumDamageFactor *= 0.95;
        wallbang = true;
        penetrations++;
        curX = hitPoint.x + dir.x * 0.08 * RapierPhysicsWorld.U;
        curY = hitPoint.y + dir.y * 0.08 * RapierPhysicsWorld.U;
        curZ = hitPoint.z + dir.z * 0.08 * RapierPhysicsWorld.U;
        remainingDist = Math.max(0, remainingDist - (hitDist + 0.08 * RapierPhysicsWorld.U));
        continue;
      }

      // Check material penetration through barrier
      const maxProbe = (weaponId === 'sniper' ? 1.5 : 0.8) * RapierPhysicsWorld.U;
      const probeRay = new RAPIER.Ray(
        new RAPIER.Vector3(
          hitPoint.x + dir.x * maxProbe,
          hitPoint.y + dir.y * maxProbe,
          hitPoint.z + dir.z * maxProbe,
        ),
        new RAPIER.Vector3(-dir.x, -dir.y, -dir.z),
      );
      const backHit = this.world.castRay(probeRay, maxProbe, true);
      if (backHit !== null) {
        const thickness = maxProbe - backHit.timeOfImpact;
        // The material model is in metres; the world is in cubes.
        const metres = thickness / RapierPhysicsWorld.U;
        if (metres > 0.02 && thickness <= maxProbe) {
          const mat = metres <= 0.4 ? 'wood' : 'drywall';
          const pen = calculateMaterialPenetration(weaponId, mat, metres);
          if (pen.canPenetrate && pen.damageFactor > 0.1) {
            accumDamageFactor *= pen.damageFactor;
            wallbang = true;
            penetrations++;
            const advance = thickness + 0.05 * RapierPhysicsWorld.U;
            curX = hitPoint.x + dir.x * advance;
            curY = hitPoint.y + dir.y * advance;
            curZ = hitPoint.z + dir.z * advance;
            remainingDist = Math.max(0, remainingDist - (hitDist + advance));
            continue;
          }
        }
      }

      // Solid impassable barrier
      return {
        hit: true,
        dist: totalDist,
        point: hitPoint,
        normal: hitNormal,
        wallbang,
        damageFactor: accumDamageFactor,
        shatteredWindows,
      };
    }

    return {
      hit: true,
      dist: totalDist,
      point: { x: curX, y: curY, z: curZ },
      normal: { x: -dir.x, y: -dir.y, z: -dir.z },
      wallbang,
      damageFactor: accumDamageFactor,
      shatteredWindows,
    };
  }

  public dispose(): void {
    this.world.free();
  }
}
