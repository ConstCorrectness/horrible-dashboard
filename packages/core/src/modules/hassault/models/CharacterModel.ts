/**
 * One player's operator: the skinned Mixamo character, plus the props the GLB
 * does not carry.
 *
 * Replaced a rig built out of 48 box primitives parented into hand-written
 * joint groups. That rig could only be posed, never deformed — each limb was a
 * rigid box pivoting about a `Group` — so the Mixamo clips had nothing to bind
 * to and the motion had to be written by hand in trigonometry. This is a real
 * `SkinnedMesh` on a real skeleton, so `CharacterAnimator` plays clips instead
 * of computing poses.
 *
 * Constrained to the canonical cylinder (r=1.1, h=5.2) the same way its
 * predecessor was: the GLB is scaled to `standingHeight` at build time by
 * `scripts/build_hassault_character.mjs`, so what is drawn matches what a shot
 * is resolved against.
 */

import type * as THREE from 'three';

import { DEFAULT_HITBOX } from '../hitbox';
import { instantiateOperator, type OperatorAsset, type OperatorInstance } from './operator';
import { buildMuzzleFlash, buildWeaponProps, createNameplateTexture } from './props';
import { resolveAvatarSkin, type AvatarSkinPalette } from './skins';

const STANDING_HEIGHT = DEFAULT_HITBOX.standingHeight;

/**
 * The bone a weapon is held in, and how the prop sits in that hand.
 *
 * Mixamo's hand bone points down the fingers with Y up the back of the hand, so
 * a weapon modelled down +Z needs rotating into it. These were dialled in
 * against `rifle_aiming_idle`, which is the pose a player is in most of the time.
 */
const GRIP_BONE = 'RightHand';
const GRIP_POSITION: readonly [number, number, number] = [0.02, 0.03, 0.06];
const GRIP_ROTATION: readonly [number, number, number] = [-Math.PI / 2, 0, Math.PI / 2];

/** Scale a weapon prop down out of cube units into the hand that holds it. */
const GRIP_SCALE = 0.55;

export class CharacterModel {
  /** What `avatars.ts` positions. Everything else hangs off it. */
  readonly rootGroup: THREE.Group;
  readonly instance: OperatorInstance;
  /** Bones by sanitised Mixamo name — `Hips`, `Spine1`, `RightHand`. */
  readonly bones: ReadonlyMap<string, THREE.Bone>;
  readonly weaponSocket: THREE.Group;
  readonly nameplate: THREE.Sprite;
  readonly muzzleFlash: THREE.Mesh;

  private readonly weaponProps: THREE.Group[];
  private readonly skinPalette: AvatarSkinPalette;
  private currentWeaponSlot = -1;
  private currentOpacity = 1;
  /** Each material's own `transparent` flag, so a fade can be undone exactly. */
  private readonly opaqueModes = new Map<THREE.Material, boolean>();
  private disposables: { dispose: () => void }[] = [];

  constructor(
    three: typeof THREE,
    asset: OperatorAsset,
    team: number,
    name: string,
    skinId?: string,
  ) {
    this.skinPalette = resolveAvatarSkin(team, skinId);
    this.rootGroup = new three.Group();

    this.instance = instantiateOperator(three, asset, this.skinPalette.armorColor);
    this.rootGroup.add(this.instance.root);
    this.bones = this.instance.bones;
    for (const material of this.instance.materials) {
      this.opaqueModes.set(material, material.transparent);
    }

    // Weapon props live on the hand bone, so the skeleton carries them through
    // every clip for free — a socket parented to the root would need the grip
    // animating by hand, which is exactly the work this rewrite removed.
    const metalMat = new three.MeshStandardMaterial({
      color: 0x27272a,
      roughness: 0.55,
      metalness: 0.7,
    });
    const trimMat = new three.MeshStandardMaterial({
      color: this.skinPalette.trimColor,
      roughness: 0.7,
      metalness: 0.3,
    });
    this.disposables.push(metalMat, trimMat);

    this.weaponSocket = new three.Group();
    this.weaponSocket.position.set(...GRIP_POSITION);
    this.weaponSocket.rotation.set(...GRIP_ROTATION);
    this.weaponSocket.scale.setScalar(GRIP_SCALE);

    const hand = this.bones.get(GRIP_BONE);
    // Falling back to the root keeps a weapon visible rather than dropping it
    // into the world origin if the rig ever changes its hand bone's name.
    (hand ?? this.rootGroup).add(this.weaponSocket);

    this.weaponProps = buildWeaponProps(three, metalMat, trimMat, this.disposables);
    for (const prop of this.weaponProps) {
      prop.visible = false;
      this.weaponSocket.add(prop);
    }

    this.muzzleFlash = buildMuzzleFlash(three, this.disposables);
    this.weaponSocket.add(this.muzzleFlash);

    const nameplateTex = createNameplateTexture(three, name, this.skinPalette.trimColor);
    const nameplateMat = new three.SpriteMaterial({ map: nameplateTex, transparent: true });
    this.nameplate = new three.Sprite(nameplateMat);
    this.nameplate.scale.set(4.5, 1.1, 1);
    this.nameplate.position.y = STANDING_HEIGHT + 0.65;
    this.rootGroup.add(this.nameplate);
    this.disposables.push(nameplateTex, nameplateMat);
  }

  /** Show the prop for a weapon slot, hiding the rest. */
  setWeapon(slot: number): void {
    if (slot === this.currentWeaponSlot) return;
    this.currentWeaponSlot = slot;
    for (let i = 0; i < this.weaponProps.length; i += 1) {
      this.weaponProps[i].visible = i === slot;
    }
  }

  triggerMuzzleFlash(): void {
    const mat = this.muzzleFlash.material as THREE.MeshBasicMaterial;
    mat.opacity = 0.95;
  }

  decayMuzzleFlash(dt: number): void {
    const mat = this.muzzleFlash.material as THREE.MeshBasicMaterial;
    if (mat.opacity > 0) {
      mat.opacity = Math.max(0, mat.opacity - dt * 18);
    }
  }

  /**
   * Fade the whole operator, for a player whose updates have gone stale.
   *
   * Only touches this instance's own cloned materials. The previous
   * implementation walked the scene graph setting `opacity` on whatever
   * material it found, which with a shared GLB would have faded every player on
   * the field the moment one of them lagged.
   *
   * Restoring means going back to each material's *own* blend mode rather than
   * to opaque: the build classifies eyewear and shoes as genuinely translucent
   * and the body as an alpha mask, so a blanket `transparent = false` on
   * recovery would flatten tinted lenses and fill in the masked body.
   */
  setOpacity(opacity: number): void {
    if (opacity === this.currentOpacity) return;
    this.currentOpacity = opacity;
    const faded = opacity < 1;
    for (const material of this.instance.materials) {
      const original = this.opaqueModes.get(material) ?? false;
      material.opacity = opacity;
      material.transparent = faded || original;
      material.depthWrite = !faded;
    }
    const plate = this.nameplate.material as THREE.SpriteMaterial;
    plate.opacity = opacity;
  }

  dispose(): void {
    this.instance.dispose();
    for (const d of this.disposables) {
      try {
        d.dispose();
      } catch {
        // Dispose everything else regardless: one geometry that objects to being
        // freed twice must not strand the rest of the model's GPU resources.
      }
    }
    this.disposables = [];
  }
}
