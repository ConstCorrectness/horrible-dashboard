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
import RAPIER from '@dimforge/rapier3d-compat';
import type { CollisionGeometry } from './world3d';
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

export class RapierPhysicsWorld {
  readonly world: RAPIER.World;
  readonly characterController: RAPIER.KinematicCharacterController;
  private playerBody: RAPIER.RigidBody;
  private standingCollider: RAPIER.Collider;
  private crouchingCollider: RAPIER.Collider;
  private activeCollider: RAPIER.Collider;
  private isCrouched = false;

  // Constants
  static readonly CAPSULE_RADIUS = 0.45;
  static readonly STANDING_HALF_HEIGHT = 0.45; // Total height = 2 * 0.45 + 2 * 0.45 = 1.8
  static readonly CROUCH_HALF_HEIGHT = 0.15;   // Total height = 2 * 0.15 + 2 * 0.45 = 1.2
  static readonly MAX_SLOPE = (45 * Math.PI) / 180;
  static readonly STEP_HEIGHT = 0.4;
  static readonly RUN_SPEED = 8.5;
  static readonly SPRINT_SPEED = 11.5;
  static readonly CROUCH_SPEED = 3.8;
  static readonly SLIDE_INITIAL_SPEED = 13.0;
  static readonly MAX_SLIDE_TIME = 0.8;
  static readonly STAMINA_DRAIN = 25.0;
  static readonly STAMINA_RECOVERY = 30.0;
  static readonly MAX_STAMINA = 100.0;
  static readonly JUMP_VELOCITY = 7.2;
  static readonly GRAVITY = -22.0;

  constructor(collision: CollisionGeometry) {
    // Gravity pointing down along Z in game coordinates
    const gravity = new RAPIER.Vector3(0.0, 0.0, RapierPhysicsWorld.GRAVITY);
    this.world = new RAPIER.World(gravity);

    // Create Trimesh collider for level geometry
    if (collision.vertices.length > 0 && collision.indices.length > 0) {
      const trimeshDesc = RAPIER.ColliderDesc.trimesh(collision.vertices, collision.indices);
      this.world.createCollider(trimeshDesc);
    }

    // Configure Kinematic Character Controller (Z-up)
    const offset = 0.04;
    this.characterController = this.world.createCharacterController(offset);
    this.characterController.setUp(new RAPIER.Vector3(0.0, 0.0, 1.0));
    this.characterController.enableAutostep(RapierPhysicsWorld.STEP_HEIGHT, 0.2, true);
    this.characterController.setMaxSlopeClimbAngle(RapierPhysicsWorld.MAX_SLOPE);
    this.characterController.setMinSlopeSlideAngle(RapierPhysicsWorld.MAX_SLOPE);
    this.characterController.enableSnapToGround(0.35);

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
    if (input.crouch && state.grounded && !state.sliding && (isSprinting || hSpeed > 6.0)) {
      state.sliding = true;
      state.slideTime = 0;
      const boost = Math.max(1.15, RapierPhysicsWorld.SLIDE_INITIAL_SPEED / Math.max(0.1, hSpeed));
      const targetSpeed = Math.min(14.0, hSpeed * boost);
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
          Math.hypot(state.vx, state.vy) < 3.5 ||
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
        const airStrafeAccel = 18.0;
        state.vx += rightX * input.strafe * airStrafeAccel * dt;
        state.vy += rightY * input.strafe * airStrafeAccel * dt;
        const totalH = Math.hypot(state.vx, state.vy);
        if (totalH > 15.0) {
          state.vx = (state.vx / totalH) * 15.0;
          state.vy = (state.vy / totalH) * 15.0;
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
      desiredZ = -0.05;
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
    if (input.crouch && player.onGround && !player.isSliding && (isSprinting || hSpeed > 6.0)) {
      player.isSliding = true;
      player.slideTime = 0;
      const boost = Math.max(1.15, RapierPhysicsWorld.SLIDE_INITIAL_SPEED / Math.max(0.1, hSpeed));
      const targetSpeed = Math.min(14.0, hSpeed * boost);
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
          Math.hypot(player.velX, player.velY) < 3.5 ||
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
        const airStrafeAccel = 18.0;
        // Right vector in world: (-sin(yaw), cos(yaw))
        player.velX += -sin * input.strafe * airStrafeAccel * dt;
        player.velY += cos * input.strafe * airStrafeAccel * dt;
        const curAir = Math.hypot(player.velX, player.velY);
        if (curAir > 15.0) {
          player.velX = (player.velX / curAir) * 15.0;
          player.velY = (player.velY / curAir) * 15.0;
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
      desiredZ = -0.05;
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
      new RAPIER.Vector3(pos.x, pos.y, pos.z + 0.2),
      new RAPIER.Vector3(0, 0, -1),
    );
    const hit = this.world.castRayAndGetNormal(ray, 0.6, true);
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
    const hit = this.world.castRay(ray, 0.7, true);
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

  public dispose(): void {
    this.world.free();
  }
}
