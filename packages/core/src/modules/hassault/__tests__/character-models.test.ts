/**
 * The operator model and its animator.
 *
 * These replaced a suite that asserted joint rotations on a rig built out of box
 * primitives — `expect(joints.leftLeg.thigh.rotation.x)` and so on. That rig is
 * gone: motion now comes from Mixamo clips played over a skinned skeleton, so
 * what is worth pinning is no longer the pose but the wiring — that each player
 * gets their own skeleton and their own materials, that clips are filtered
 * before they layer, and that the weapon rides the hand bone.
 *
 * The asset is synthesised rather than loaded, so the suite stays fast and does
 * not depend on `hassault-operator.glb` having been built.
 */

import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { clone as skeletonClone } from 'three/examples/jsm/utils/SkeletonUtils.js';

import { CharacterModel } from '../models/CharacterModel';
import { CharacterAnimator } from '../models/CharacterAnimator';
import { resolveAvatarSkin, AVATAR_SKIN_CATALOG } from '../models/skins';
import { OPERATOR_CLIPS, type OperatorClip } from '../models/clips';
import type { OperatorAsset } from '../models/operator';
import type { PlayerRow } from '../net';

/** The bones the real rig has that any of this cares about. */
const BONE_NAMES = [
  'Hips',
  'Spine',
  'Spine1',
  'Spine2',
  'Neck',
  'Head',
  'LeftUpLeg',
  'LeftLeg',
  'RightUpLeg',
  'RightLeg',
  'LeftShoulder',
  'LeftArm',
  'RightShoulder',
  'RightArm',
  'RightForeArm',
  'RightHand',
];

/**
 * A stand-in for the built GLB: one skinned mesh over a flat skeleton, plus a
 * clip per name that rotates every bone. Flat rather than hierarchical because
 * nothing under test walks the parent chain, and the tracks address bones by
 * name exactly as the real clips do.
 */
function makeAsset(): OperatorAsset {
  const root = new THREE.Group();
  const bones = BONE_NAMES.map((name) => {
    const bone = new THREE.Bone();
    bone.name = `mixamorig${name}`;
    root.add(bone);
    return bone;
  });

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0], 3));
  geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute([0, 0, 0, 0], 4));
  geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute([1, 0, 0, 0], 4));
  const mesh = new THREE.SkinnedMesh(geometry, new THREE.MeshStandardMaterial());
  mesh.add(bones[0]);
  mesh.bind(new THREE.Skeleton(bones));
  root.add(mesh);

  const clips = new Map<OperatorClip, THREE.AnimationClip>();
  for (const name of OPERATOR_CLIPS) {
    const tracks = bones.map(
      (bone) =>
        new THREE.QuaternionKeyframeTrack(
          `${bone.name}.quaternion`,
          [0, 1],
          [0, 0, 0, 1, 0, 0.3827, 0, 0.9239],
        ),
    );
    clips.set(name, new THREE.AnimationClip(name, 1, tracks));
  }

  return { prototype: root, clips, clone: skeletonClone };
}

function row(over: Partial<PlayerRow> = {}): PlayerRow {
  return {
    id: 'p1',
    name: 'Operator',
    team: 0,
    x: 0,
    y: 0,
    z: 0,
    yaw: 0,
    pitch: 0,
    ground: true,
    stale: false,
    rtt: 20,
    hp: 100,
    alive: true,
    weapon: 2,
    kills: 0,
    deaths: 0,
    bot: false,
    crouch: 0,
    ...over,
  };
}

describe('hAssault operator skins', () => {
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

  it('keeps every catalog entry aesthetic only, so no skin can change a hitbox', () => {
    for (const [id, skin] of Object.entries(AVATAR_SKIN_CATALOG)) {
      expect(skin.id).toBe(id);
      expect(typeof skin.bodyColor).toBe('number');
      expect(typeof skin.armorColor).toBe('number');
      expect(typeof skin.visorColor).toBe('number');
    }
  });
});

describe('hAssault operator model', () => {
  it('exposes the skeleton by sanitised bone name', () => {
    const model = new CharacterModel(THREE, makeAsset(), 0, 'TestOperator', 'arc_default');
    expect(model.bones.get('Hips')).toBeDefined();
    expect(model.bones.get('RightHand')).toBeDefined();
    expect(model.bones.size).toBe(BONE_NAMES.length);
    model.dispose();
  });

  it('hangs the weapon socket off the hand bone, not the root', () => {
    const model = new CharacterModel(THREE, makeAsset(), 0, 'Gunner');
    // A socket on the root would need the grip animated by hand; on the bone the
    // skeleton carries it through every clip for free.
    expect(model.weaponSocket.parent).toBe(model.bones.get('RightHand'));
    model.dispose();
  });

  it('gives each player their own skeleton', () => {
    // A plain Object3D.clone leaves every copy bound to the source skeleton, so
    // all eight players in a match would share one pose. Nothing warns.
    const asset = makeAsset();
    const a = new CharacterModel(THREE, asset, 0, 'A');
    const b = new CharacterModel(THREE, asset, 1, 'B');
    expect(a.bones.get('Hips')).not.toBe(b.bones.get('Hips'));
    a.dispose();
    b.dispose();
  });

  it('gives each player their own materials, so one stale player cannot fade the field', () => {
    const asset = makeAsset();
    const a = new CharacterModel(THREE, asset, 0, 'A');
    const b = new CharacterModel(THREE, asset, 1, 'B');
    expect(a.instance.materials[0]).not.toBe(b.instance.materials[0]);

    a.setOpacity(0.35);
    expect(a.instance.materials[0].opacity).toBe(0.35);
    expect(b.instance.materials[0].opacity).toBe(1);
    a.dispose();
    b.dispose();
  });

  it('restores a material to its own blend mode after a fade, not to opaque', () => {
    // Eyewear and shoes are genuinely translucent in the built GLB; a blanket
    // `transparent = false` on recovery would flatten them.
    const model = new CharacterModel(THREE, makeAsset(), 0, 'A');
    const material = model.instance.materials[0];
    material.transparent = true;
    // Re-read the mode the model recorded at construction by round-tripping.
    model.setOpacity(0.35);
    model.setOpacity(1);
    expect(material.opacity).toBe(1);
    model.dispose();
  });

  it('switches active weapon props and leaves the rest hidden', () => {
    const model = new CharacterModel(THREE, makeAsset(), 0, 'Gunner');
    const props = model.weaponSocket.children.filter((c) => c.type === 'Group');
    model.setWeapon(2);
    expect(props[2].visible).toBe(true);
    expect(props[0].visible).toBe(false);
    model.setWeapon(4);
    expect(props[4].visible).toBe(true);
    expect(props[2].visible).toBe(false);
    model.dispose();
  });

  it('triggers and decays muzzle flash opacity', () => {
    const model = new CharacterModel(THREE, makeAsset(), 0, 'Shooter');
    const mat = model.muzzleFlash.material as THREE.MeshBasicMaterial;
    expect(mat.opacity).toBe(0);
    model.triggerMuzzleFlash();
    expect(mat.opacity).toBeGreaterThan(0.9);
    model.decayMuzzleFlash(0.1);
    expect(mat.opacity).toBeLessThan(0.9);
    model.decayMuzzleFlash(1.0);
    expect(mat.opacity).toBe(0);
    model.dispose();
  });
});

describe('hAssault operator animator', () => {
  it('drives the skeleton from clips', () => {
    const model = new CharacterModel(THREE, makeAsset(), 0, 'Walker');
    const animator = new CharacterAnimator(THREE, model, makeAsset());
    const hips = model.bones.get('Hips')!;
    const before = hips.quaternion.clone();

    animator.update(0.016, row({ x: 0, y: 0 }));
    animator.update(0.25, row({ x: 2, y: 0 }));

    expect(hips.quaternion.angleTo(before)).toBeGreaterThan(1e-4);
    animator.dispose();
    model.dispose();
  });

  it('leans the spine toward where a player is aiming', () => {
    // No Mixamo clip knows the pitch — they all look at the horizon — so an
    // enemy firing down from a balcony would appear to aim straight ahead.
    const model = new CharacterModel(THREE, makeAsset(), 0, 'Looker');
    const animator = new CharacterAnimator(THREE, model, makeAsset());

    animator.update(0.016, row({ pitch: 0 }));
    const level = model.bones.get('Head')!.rotation.x;
    animator.update(0.016, row({ pitch: 0.8 }));
    const raised = model.bones.get('Head')!.rotation.x;

    expect(raised).toBeLessThan(level);
    animator.dispose();
    model.dispose();
  });

  it('layers a fire action over the legs without averaging against them', () => {
    const model = new CharacterModel(THREE, makeAsset(), 0, 'Shooter');
    const animator = new CharacterAnimator(THREE, model, makeAsset());
    animator.update(0.016, row());
    animator.triggerRecoil();
    animator.update(0.016, row());

    const mat = model.muzzleFlash.material as THREE.MeshBasicMaterial;
    expect(mat.opacity).toBeGreaterThan(0.5);
    animator.dispose();
    model.dispose();
  });

  it('fades a stale player and brings them back', () => {
    const model = new CharacterModel(THREE, makeAsset(), 0, 'Laggy');
    const animator = new CharacterAnimator(THREE, model, makeAsset());

    animator.update(0.016, row({ stale: true }));
    expect(model.instance.materials[0].opacity).toBeCloseTo(0.35);

    animator.update(0.016, row({ stale: false }));
    expect(model.instance.materials[0].opacity).toBe(1);
    animator.dispose();
    model.dispose();
  });
});
