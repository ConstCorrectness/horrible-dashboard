/**
 * Procedural Skeletal & Locomotion Animation Engine for hAssault character models.
 *
 * Drives natural 60fps animations:
 * - Breathing idle sway
 * - Multi-directional walk and sprint cycles (2-phase pendulum leg kinematics)
 * - Dynamic crouch compression (preserving exact hitbox bounds)
 * - Airborne jump tuck & landing impact
 * - Aiming pitch look up / down (distributed across spine and head)
 * - Weapon recoil kickback and weapon holding posture
 * - Elimination / death slump
 */

import type { CharacterModel } from './CharacterModel';
import type { PlayerRow } from '../net';
import { DEFAULT_HITBOX } from '../hitbox';

const STANDING_HEIGHT = DEFAULT_HITBOX.standingHeight;
const CROUCH_HEIGHT = DEFAULT_HITBOX.crouchHeight;

export class CharacterAnimator {
  private prevX = 0;
  private prevY = 0;
  private hasPrev = false;

  private walkPhase = 0;
  private breathPhase = Math.random() * Math.PI * 2;
  private currentSpeed = 0;
  private weaponKick = 0;
  private deathProgress = 0;

  constructor(private readonly model: CharacterModel) {}

  update(dt: number, row: PlayerRow): void {
    if (dt <= 0 || Number.isNaN(dt)) dt = 0.016;

    // Decay muzzle flash if active
    this.model.decayMuzzleFlash(dt);

    // Sync active weapon model
    this.model.setWeapon(row.weapon ?? 2);

    const joints = this.model.joints;
    const alive = row.alive !== false;

    // -------------------------------------------------------------------------
    // 1. Death / Elimination Slump Animation
    // -------------------------------------------------------------------------
    if (!alive) {
      this.deathProgress = Math.min(1.0, this.deathProgress + dt * 4.0);
      const d = this.deathProgress;

      joints.pelvis.position.y = 2.3 * (1 - d) + 0.35 * d;
      joints.spine.rotation.set(-1.1 * d, 0, 0.2 * d);
      joints.head.rotation.set(0.6 * d, 0, 0);

      joints.leftArm.shoulder.rotation.set(0.2 * d, 0, 0.6 * d);
      joints.rightArm.shoulder.rotation.set(0.2 * d, 0, -0.6 * d);
      joints.leftLeg.thigh.rotation.set(-0.2 * d, 0, 0.5 * d);
      joints.rightLeg.thigh.rotation.set(-0.2 * d, 0, -0.5 * d);

      joints.nameplate.visible = false;
      return;
    }

    this.deathProgress = 0;
    joints.nameplate.visible = true;

    // -------------------------------------------------------------------------
    // 2. Velocity & Locomotion Speed Derivation
    // -------------------------------------------------------------------------
    let speed = 0;
    if (this.hasPrev) {
      const dx = row.x - this.prevX;
      const dy = row.y - this.prevY;
      const dist = Math.hypot(dx, dy);
      speed = dist / dt;
    } else {
      this.hasPrev = true;
    }
    this.prevX = row.x;
    this.prevY = row.y;

    // Smooth speed transitions
    this.currentSpeed += (Math.min(speed, 25.0) - this.currentSpeed) * Math.min(1.0, dt * 10);

    // Update phase clocks
    this.breathPhase += dt * 2.2;
    if (this.currentSpeed > 0.4) {
      this.walkPhase += dt * this.currentSpeed * 0.45;
    }

    // -------------------------------------------------------------------------
    // 3. Crouch Dynamics (Strictly preserves Hitbox Envelope)
    // -------------------------------------------------------------------------
    const crouch = row.crouch ?? 0;
    const crouchDrop = (STANDING_HEIGHT - CROUCH_HEIGHT) * crouch;
    const baseHipY = 2.3 - crouchDrop * 0.85;

    // -------------------------------------------------------------------------
    // 4. Aim Pitch Look Up / Down
    // -------------------------------------------------------------------------
    const pitch = row.pitch ?? 0;
    // Distribute pitch: 40% to spine, 60% to neck/head
    const spinePitch = -pitch * 0.4;
    const headPitch = -pitch * 0.6;

    // -------------------------------------------------------------------------
    // 5. Breathing & Hip Bobbing
    // -------------------------------------------------------------------------
    const breathOffset = Math.sin(this.breathPhase) * 0.02;
    const walkBob = Math.abs(Math.sin(this.walkPhase * 2)) * 0.08 * Math.min(1.0, this.currentSpeed / 8);

    joints.pelvis.position.y = baseHipY + breathOffset - walkBob;
    joints.pelvis.rotation.set(0, 0, 0);

    // Spine posture
    joints.spine.rotation.set(spinePitch + crouch * 0.15, 0, 0);
    joints.head.rotation.set(headPitch, 0, 0);

    // -------------------------------------------------------------------------
    // 6. Leg Kinematics (Walk / Run / Jump / Crouch)
    // -------------------------------------------------------------------------
    const onGround = row.ground !== false;

    if (!onGround) {
      // Airborne / Jump Pose: tuck legs up, knees bent
      joints.leftLeg.thigh.rotation.set(-0.65, 0, 0.15);
      joints.leftLeg.shin.rotation.set(0.95, 0, 0);
      joints.rightLeg.thigh.rotation.set(-0.4, 0, -0.15);
      joints.rightLeg.shin.rotation.set(0.7, 0, 0);
    } else if (crouch > 0.05) {
      // Crouch Pose: knees spread forward, shins bent back
      const c = crouch;
      joints.leftLeg.thigh.rotation.set(-0.75 * c, 0.15 * c, 0);
      joints.leftLeg.shin.rotation.set(1.1 * c, 0, 0);
      joints.rightLeg.thigh.rotation.set(-0.75 * c, -0.15 * c, 0);
      joints.rightLeg.shin.rotation.set(1.1 * c, 0, 0);
    } else {
      // Ground Locomotion: 2-phase pendulum swing
      const strideMagnitude = Math.min(0.85, (this.currentSpeed / 12) * 0.85);
      const legSwing = Math.sin(this.walkPhase) * strideMagnitude;

      // Left Leg
      joints.leftLeg.thigh.rotation.set(legSwing, 0, 0);
      const leftKneeBend = Math.max(0, -Math.sin(this.walkPhase)) * strideMagnitude * 1.1;
      joints.leftLeg.shin.rotation.set(leftKneeBend, 0, 0);

      // Right Leg
      joints.rightLeg.thigh.rotation.set(-legSwing, 0, 0);
      const rightKneeBend = Math.max(0, Math.sin(this.walkPhase)) * strideMagnitude * 1.1;
      joints.rightLeg.shin.rotation.set(rightKneeBend, 0, 0);
    }

    // -------------------------------------------------------------------------
    // 7. Arm Posture & Tactical Two-Handed Weapon Grip
    // -------------------------------------------------------------------------
    // Right Arm (Trigger grip on handle)
    joints.rightArm.shoulder.rotation.set(-0.75 - spinePitch * 0.2, -0.3, 0.08);
    joints.rightArm.upperArm.rotation.set(-0.15, 0, 0);
    joints.rightArm.forearm.rotation.set(-0.65, 0.15, 0);
    joints.rightArm.hand.rotation.set(0, 0, 0);

    // Left Arm (Support grip reaching forward to rifle handguard / pump)
    joints.leftArm.shoulder.rotation.set(-0.85 - spinePitch * 0.2, 0.45, -0.18);
    joints.leftArm.upperArm.rotation.set(-0.1, 0, 0);
    joints.leftArm.forearm.rotation.set(-0.75, -0.3, 0.1);
    joints.leftArm.hand.rotation.set(0, 0, 0);

    // Recoil Kickback
    if (this.weaponKick > 0) {
      this.weaponKick = Math.max(0, this.weaponKick - dt * 14);
      joints.weaponSocket.position.z = 0.25 - this.weaponKick * 0.22;
      joints.weaponSocket.rotation.x = -this.weaponKick * 0.18;
    } else {
      joints.weaponSocket.position.z = 0.25;
      joints.weaponSocket.rotation.x = 0;
    }

    // -------------------------------------------------------------------------
    // 8. Nameplate Height Adjustment
    // -------------------------------------------------------------------------
    joints.nameplate.position.y = STANDING_HEIGHT + 0.65 - crouchDrop;
  }

  triggerRecoil(): void {
    this.weaponKick = 1.0;
    this.model.triggerMuzzleFlash();
  }
}
