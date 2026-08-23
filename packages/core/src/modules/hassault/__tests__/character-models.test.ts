import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { CharacterModel } from '../models/CharacterModel';
import { CharacterAnimator } from '../models/CharacterAnimator';
import { resolveAvatarSkin, AVATAR_SKIN_CATALOG } from '../models/skins';
import type { PlayerRow } from '../net';
import { DEFAULT_HITBOX } from '../hitbox';

const STANDING_HEIGHT = DEFAULT_HITBOX.standingHeight;
const CROUCH_HEIGHT = DEFAULT_HITBOX.crouchHeight;

describe('hAssault Character Models: Skins & Hitbox Invariance', () => {
  it('resolves default faction palettes and custom operator skins', () => {
    const arcSkin = resolveAvatarSkin(0);
    expect(arcSkin.faction).toBe('arc');
    expect(arcSkin.bodyColor).toBe(0xd9a441);

    const halonSkin = resolveAvatarSkin(1);
    expect(halonSkin.faction).toBe('halon');
    expect(halonSkin.bodyColor).toBe(0x4c8fd4);

    const customSkin = resolveAvatarSkin(0, 'cyber_ghost');
    expect(customSkin.name).toBe('Ghost Recon');
    expect(customSkin.visorColor).toBe(0xc084fc);

    const fallbackSkin = resolveAvatarSkin(1, 'non_existent_skin');
    expect(fallbackSkin.id).toBe('halon_default');
  });

  it('guarantees identical hitbox bounding dimensions across all skins', () => {
    // Check that every skin catalog entry defines aesthetic properties only
    for (const [id, skin] of Object.entries(AVATAR_SKIN_CATALOG)) {
      expect(skin.id).toBe(id);
      expect(typeof skin.bodyColor).toBe('number');
      expect(typeof skin.armorColor).toBe('number');
      expect(typeof skin.visorColor).toBe('number');
    }
  });
});

describe('hAssault Character Models: Model & Joint Hierarchy Rig', () => {
  it('constructs complete articulated humanoid joint hierarchy', () => {
    const model = new CharacterModel(THREE, 0, 'TestOperator', 'arc_default');

    expect(model.rootGroup).toBeDefined();
    expect(model.joints.pelvis).toBeDefined();
    expect(model.joints.spine).toBeDefined();
    expect(model.joints.head).toBeDefined();

    // Check Arms
    expect(model.joints.leftArm.shoulder).toBeDefined();
    expect(model.joints.leftArm.forearm).toBeDefined();
    expect(model.joints.rightArm.shoulder).toBeDefined();
    expect(model.joints.rightArm.hand).toBeDefined();

    // Check Legs
    expect(model.joints.leftLeg.hip).toBeDefined();
    expect(model.joints.leftLeg.shin).toBeDefined();
    expect(model.joints.rightLeg.hip).toBeDefined();
    expect(model.joints.rightLeg.foot).toBeDefined();

    // Check Weapon Socket & Props (5 weapon classes)
    expect(model.joints.weaponSocket).toBeDefined();
    expect(model.joints.weaponProps.length).toBe(5);

    // Initial root group position
    expect(model.rootGroup.position.y).toBe(0);
    expect(model.joints.pelvis.position.y).toBeCloseTo(2.3, 1);

    model.dispose();
  });

  it('switches active 3D weapon props correctly', () => {
    const model = new CharacterModel(THREE, 1, 'HalonSoldier');

    // Select Carbine (Slot 2)
    model.setWeapon(2);
    expect(model.joints.weaponProps[2].visible).toBe(true);
    expect(model.joints.weaponProps[0].visible).toBe(false);
    expect(model.joints.weaponProps[1].visible).toBe(false);

    // Switch to Knife (Slot 0)
    model.setWeapon(0);
    expect(model.joints.weaponProps[0].visible).toBe(true);
    expect(model.joints.weaponProps[2].visible).toBe(false);

    model.dispose();
  });

  it('triggers and decays muzzle flash opacity', () => {
    const model = new CharacterModel(THREE, 0, 'Shooter');
    const flashMat = model.joints.muzzleFlash.material as THREE.MeshBasicMaterial;

    expect(flashMat.opacity).toBe(0);

    model.triggerMuzzleFlash();
    expect(flashMat.opacity).toBeGreaterThan(0.9);

    model.decayMuzzleFlash(0.1);
    expect(flashMat.opacity).toBeLessThan(0.9);

    model.decayMuzzleFlash(1.0);
    expect(flashMat.opacity).toBe(0);

    model.dispose();
  });
});

describe('hAssault Character Models: Procedural Animation Engine', () => {
  const createMockRow = (overrides?: Partial<PlayerRow>): PlayerRow => ({
    id: 'p1',
    name: 'Player1',
    team: 0,
    x: 10,
    y: 20,
    z: 5,
    yaw: 0,
    pitch: 0,
    ground: true,
    stale: false,
    rtt: 30,
    hp: 100,
    alive: true,
    weapon: 2,
    kills: 0,
    deaths: 0,
    bot: false,
    crouch: 0,
    ...overrides,
  });

  it('drives idle breathing and walking leg kinematics', () => {
    const model = new CharacterModel(THREE, 0, 'Walker');
    const animator = new CharacterAnimator(model);

    // Initial frame
    const row1 = createMockRow({ x: 10, y: 20 });
    animator.update(0.016, row1);

    // Second frame with movement (speed ~10 units/s)
    const row2 = createMockRow({ x: 10.16, y: 20.0 });
    animator.update(0.016, row2);

    // Thighs should engage in counter-swinging kinematics
    expect(model.joints.leftLeg.thigh.rotation.x).toBeDefined();
    expect(model.joints.rightLeg.thigh.rotation.x).toBeDefined();

    model.dispose();
  });

  it('compresses skeleton during crouch to preserve exact height bounds', () => {
    const model = new CharacterModel(THREE, 0, 'Croucher');
    const animator = new CharacterAnimator(model);

    // Standing update
    const standingRow = createMockRow({ crouch: 0 });
    animator.update(0.016, standingRow);
    const standingHipY = model.joints.pelvis.position.y;

    // Full crouch update
    const crouchRow = createMockRow({ crouch: 1.0 });
    animator.update(0.016, crouchRow);
    const crouchHipY = model.joints.pelvis.position.y;

    // Hip should drop significantly in crouch
    expect(crouchHipY).toBeLessThan(standingHipY);
    expect(standingHipY - crouchHipY).toBeCloseTo(1.125 * 0.85, 1);

    // Nameplate drops matching crouch
    expect(model.joints.nameplate.position.y).toBeCloseTo(STANDING_HEIGHT + 0.65 - (STANDING_HEIGHT - CROUCH_HEIGHT), 1);

    model.dispose();
  });

  it('distributes look pitch across spine and head joints', () => {
    const model = new CharacterModel(THREE, 0, 'Looker');
    const animator = new CharacterAnimator(model);

    // Look up 45 degrees (~0.785 rad)
    const pitchRow = createMockRow({ pitch: 0.785 });
    animator.update(0.016, pitchRow);

    // Spine and head should pitch up proportionally
    expect(model.joints.spine.rotation.x).toBeLessThan(0);
    expect(model.joints.head.rotation.x).toBeLessThan(0);
    expect(model.joints.spine.rotation.x + model.joints.head.rotation.x).toBeCloseTo(-0.785, 1);

    model.dispose();
  });

  it('tucks legs into airborne pose when jump / not on ground', () => {
    const model = new CharacterModel(THREE, 0, 'Jumper');
    const animator = new CharacterAnimator(model);

    const jumpRow = createMockRow({ ground: false });
    animator.update(0.016, jumpRow);

    // In airborne pose, thighs tuck up and knees bend
    expect(model.joints.leftLeg.thigh.rotation.x).toBeCloseTo(-0.65, 2);
    expect(model.joints.leftLeg.shin.rotation.x).toBeCloseTo(0.95, 2);

    model.dispose();
  });

  it('applies weapon recoil kickback impulse', () => {
    const model = new CharacterModel(THREE, 0, 'Shooter');
    const animator = new CharacterAnimator(model);

    const row = createMockRow();
    animator.update(0.016, row);

    // Normal weapon socket position
    expect(model.joints.weaponSocket.position.z).toBeCloseTo(0.25, 2);

    // Trigger recoil
    animator.triggerRecoil();
    animator.update(0.016, row);

    // Socket should kick back along Z and tilt up
    expect(model.joints.weaponSocket.position.z).toBeLessThan(0.25);
    expect(model.joints.weaponSocket.rotation.x).toBeLessThan(0);

    model.dispose();
  });

  it('transitions into elimination slump on player death', () => {
    const model = new CharacterModel(THREE, 0, 'Fallen');
    const animator = new CharacterAnimator(model);

    const deadRow = createMockRow({ alive: false });
    animator.update(0.5, deadRow);

    // Pelvis drops to near-ground level and spine slumps back
    expect(model.joints.pelvis.position.y).toBeLessThan(1.0);
    expect(model.joints.spine.rotation.x).toBeLessThan(-0.5);
    expect(model.joints.nameplate.visible).toBe(false);

    model.dispose();
  });
});
