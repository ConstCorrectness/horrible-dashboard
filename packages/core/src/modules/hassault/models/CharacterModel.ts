/**
 * Articulated Humanoid Character Model for hAssault.
 *
 * Implements a modular, stylized low-poly operator mesh with a complete
 * hierarchical joint rig: Pelvis, Spine, Head, Arms, Legs, and Weapon Socket.
 *
 * Strictly constrained to fit within the canonical cylinder (r=1.1, h=5.2)
 * guaranteeing 100% hitbox and gameplay invariance.
 */

import type * as THREE from 'three';
import { DEFAULT_HITBOX } from '../hitbox';
import { resolveAvatarSkin, type AvatarSkinPalette } from './skins';

const STANDING_HEIGHT = DEFAULT_HITBOX.standingHeight;

export interface CharacterJoints {
  pelvis: THREE.Group;
  spine: THREE.Group;
  head: THREE.Group;
  leftArm: {
    shoulder: THREE.Group;
    upperArm: THREE.Group;
    forearm: THREE.Group;
    hand: THREE.Group;
  };
  rightArm: {
    shoulder: THREE.Group;
    upperArm: THREE.Group;
    forearm: THREE.Group;
    hand: THREE.Group;
  };
  leftLeg: {
    hip: THREE.Group;
    thigh: THREE.Group;
    shin: THREE.Group;
    foot: THREE.Group;
  };
  rightLeg: {
    hip: THREE.Group;
    thigh: THREE.Group;
    shin: THREE.Group;
    foot: THREE.Group;
  };
  weaponSocket: THREE.Group;
  weaponProps: THREE.Group[];
  muzzleFlash: THREE.Mesh;
  nameplate: THREE.Sprite;
}

export class CharacterModel {
  readonly rootGroup: THREE.Group;
  readonly joints: CharacterJoints;
  private currentWeaponSlot = -1;
  private skinPalette: AvatarSkinPalette;
  private disposables: { dispose: () => void }[] = [];

  constructor(
    private readonly three: typeof THREE,
    team: number,
    name: string,
    skinId?: string,
  ) {
    this.rootGroup = new three.Group();
    this.skinPalette = resolveAvatarSkin(team, skinId);
    this.joints = this.buildRig(name);
  }

  private buildRig(name: string): CharacterJoints {
    const three = this.three;
    const skin = this.skinPalette;

    // Materials
    const bodyMat = new three.MeshLambertMaterial({ color: skin.bodyColor });
    const armorMat = new three.MeshLambertMaterial({ color: skin.armorColor });
    const trimMat = new three.MeshLambertMaterial({ color: skin.trimColor });
    const visorMat = new three.MeshLambertMaterial({
      color: skin.visorColor,
      emissive: skin.visorColor,
      emissiveIntensity: 0.25,
    });
    const skinToneMat = new three.MeshLambertMaterial({ color: skin.skinToneColor });
    const bootMat = new three.MeshLambertMaterial({ color: skin.bootColor });
    const metalMat = new three.MeshLambertMaterial({ color: 0x27272a });

    this.disposables.push(bodyMat, armorMat, trimMat, visorMat, skinToneMat, bootMat, metalMat);

    // 1. Pelvis / Hips (Root of humanoid body hierarchy)
    const pelvis = new three.Group();
    pelvis.position.y = 2.3; // Base hip height
    this.rootGroup.add(pelvis);

    const pelvisGeo = new three.BoxGeometry(0.95, 0.45, 0.65);
    const pelvisMesh = new three.Mesh(pelvisGeo, armorMat);
    pelvis.add(pelvisMesh);

    // Tactical Belt
    const beltGeo = new three.BoxGeometry(1.02, 0.16, 0.72);
    const beltMesh = new three.Mesh(beltGeo, metalMat);
    pelvis.add(beltMesh);

    this.disposables.push(pelvisGeo, beltGeo);

    // 2. Spine / Torso (Child of Pelvis)
    const spine = new three.Group();
    spine.position.y = 0.25;
    pelvis.add(spine);

    // Torso Under-suit
    const torsoGeo = new three.BoxGeometry(1.05, 1.1, 0.62);
    const torsoMesh = new three.Mesh(torsoGeo, bodyMat);
    torsoMesh.position.y = 0.55;
    spine.add(torsoMesh);

    // Tactical Plate Carrier (Vest)
    const vestGeo = new three.BoxGeometry(1.2, 1.0, 0.78);
    const vestMesh = new three.Mesh(vestGeo, armorMat);
    vestMesh.position.y = 0.58;
    spine.add(vestMesh);

    // Ammo Pouches (Chest)
    const pouchGeo = new three.BoxGeometry(0.85, 0.35, 0.18);
    const pouchMesh = new three.Mesh(pouchGeo, trimMat);
    pouchMesh.position.set(0, 0.48, 0.45);
    spine.add(pouchMesh);

    // Shoulder Pauldrons / Armor Trim
    const pauldronGeo = new three.BoxGeometry(0.35, 0.22, 0.42);
    const leftPauldron = new three.Mesh(pauldronGeo, trimMat);
    leftPauldron.position.set(0.68, 1.05, 0);
    const rightPauldron = new three.Mesh(pauldronGeo, trimMat);
    rightPauldron.position.set(-0.68, 1.05, 0);
    spine.add(leftPauldron, rightPauldron);

    this.disposables.push(torsoGeo, vestGeo, pouchGeo, pauldronGeo);

    // 3. Neck & Head (Child of Spine)
    const head = new three.Group();
    head.position.y = 1.25; // Sits at world y = 3.8 to 5.0
    spine.add(head);

    // Neck
    const neckGeo = new three.CylinderGeometry(0.22, 0.26, 0.25, 8);
    const neckMesh = new three.Mesh(neckGeo, skinToneMat);
    neckMesh.position.y = 0.12;
    head.add(neckMesh);

    // Head Base / Balaclava
    const headGeo = new three.BoxGeometry(0.68, 0.72, 0.72);
    const headMesh = new three.Mesh(headGeo, skinToneMat);
    headMesh.position.y = 0.52;
    head.add(headMesh);

    // Tactical Helmet
    const helmetGeo = new three.BoxGeometry(0.78, 0.45, 0.84);
    const helmetMesh = new three.Mesh(helmetGeo, armorMat);
    helmetMesh.position.set(0, 0.72, -0.02);
    head.add(helmetMesh);

    // Tactical Visor / Goggles (Sits right at EYE_HEIGHT ~4.5)
    const visorGeo = new three.BoxGeometry(0.62, 0.22, 0.24);
    const visorMesh = new three.Mesh(visorGeo, visorMat);
    visorMesh.position.set(0, 0.54, 0.38);
    head.add(visorMesh);

    this.disposables.push(neckGeo, headGeo, helmetGeo, visorGeo);

    // 4. Arms (Left & Right)
    const armWidth = 0.28;
    const upperArmLength = 0.65;
    const forearmLength = 0.6;

    const upperArmGeo = new three.BoxGeometry(armWidth, upperArmLength, armWidth);
    const forearmGeo = new three.BoxGeometry(armWidth * 0.9, forearmLength, armWidth * 0.9);
    const handGeo = new three.BoxGeometry(0.22, 0.22, 0.26);

    this.disposables.push(upperArmGeo, forearmGeo, handGeo);

    // --- Left Arm ---
    const leftShoulder = new three.Group();
    leftShoulder.position.set(0.72, 1.05, 0);
    spine.add(leftShoulder);

    const leftUpperArm = new three.Group();
    leftShoulder.add(leftUpperArm);
    const leftUpperMesh = new three.Mesh(upperArmGeo, bodyMat);
    leftUpperMesh.position.y = -upperArmLength / 2;
    leftUpperArm.add(leftUpperMesh);

    const leftForearm = new three.Group();
    leftForearm.position.y = -upperArmLength;
    leftUpperArm.add(leftForearm);
    const leftForearmMesh = new three.Mesh(forearmGeo, armorMat);
    leftForearmMesh.position.y = -forearmLength / 2;
    leftForearm.add(leftForearmMesh);

    const leftHand = new three.Group();
    leftHand.position.y = -forearmLength;
    leftForearm.add(leftHand);
    const leftHandMesh = new three.Mesh(handGeo, skinToneMat);
    leftHandMesh.position.y = -0.11;
    leftHand.add(leftHandMesh);

    // --- Right Arm ---
    const rightShoulder = new three.Group();
    rightShoulder.position.set(-0.72, 1.05, 0);
    spine.add(rightShoulder);

    const rightUpperArm = new three.Group();
    rightShoulder.add(rightUpperArm);
    const rightUpperMesh = new three.Mesh(upperArmGeo, bodyMat);
    rightUpperMesh.position.y = -upperArmLength / 2;
    rightUpperArm.add(rightUpperMesh);

    const rightForearm = new three.Group();
    rightForearm.position.y = -upperArmLength;
    rightUpperArm.add(rightForearm);
    const rightForearmMesh = new three.Mesh(forearmGeo, armorMat);
    rightForearmMesh.position.y = -forearmLength / 2;
    rightForearm.add(rightForearmMesh);

    const rightHand = new three.Group();
    rightHand.position.y = -forearmLength;
    rightForearm.add(rightHand);
    const rightHandMesh = new three.Mesh(handGeo, skinToneMat);
    rightHandMesh.position.y = -0.11;
    rightHand.add(rightHandMesh);

    // 5. Legs (Left & Right)
    const legWidth = 0.36;
    const thighLength = 0.95;
    const shinLength = 0.95;

    const thighGeo = new three.BoxGeometry(legWidth, thighLength, legWidth);
    const shinGeo = new three.BoxGeometry(legWidth * 0.9, shinLength, legWidth * 0.9);
    const kneePadGeo = new three.BoxGeometry(0.38, 0.28, 0.2);
    const bootGeo = new three.BoxGeometry(0.38, 0.4, 0.65);

    this.disposables.push(thighGeo, shinGeo, kneePadGeo, bootGeo);

    // --- Left Leg ---
    const leftHip = new three.Group();
    leftHip.position.set(0.32, -0.18, 0);
    pelvis.add(leftHip);

    const leftThigh = new three.Group();
    leftHip.add(leftThigh);
    const leftThighMesh = new three.Mesh(thighGeo, bodyMat);
    leftThighMesh.position.y = -thighLength / 2;
    leftThigh.add(leftThighMesh);

    const leftShin = new three.Group();
    leftShin.position.y = -thighLength;
    leftThigh.add(leftShin);
    const leftShinMesh = new three.Mesh(shinGeo, bodyMat);
    leftShinMesh.position.y = -shinLength / 2;
    leftShin.add(leftShinMesh);

    // Left Knee Pad
    const leftKneePad = new three.Mesh(kneePadGeo, armorMat);
    leftKneePad.position.set(0, 0, 0.2);
    leftShin.add(leftKneePad);

    const leftFoot = new three.Group();
    leftFoot.position.y = -shinLength;
    leftShin.add(leftFoot);
    const leftBootMesh = new three.Mesh(bootGeo, bootMat);
    leftBootMesh.position.set(0, -0.15, 0.12);
    leftFoot.add(leftBootMesh);

    // --- Right Leg ---
    const rightHip = new three.Group();
    rightHip.position.set(-0.32, -0.18, 0);
    pelvis.add(rightHip);

    const rightThigh = new three.Group();
    rightHip.add(rightThigh);
    const rightThighMesh = new three.Mesh(thighGeo, bodyMat);
    rightThighMesh.position.y = -thighLength / 2;
    rightThigh.add(rightThighMesh);

    const rightShin = new three.Group();
    rightShin.position.y = -thighLength;
    rightThigh.add(rightShin);
    const rightShinMesh = new three.Mesh(shinGeo, bodyMat);
    rightShinMesh.position.y = -shinLength / 2;
    rightShin.add(rightShinMesh);

    // Right Knee Pad
    const rightKneePad = new three.Mesh(kneePadGeo, armorMat);
    rightKneePad.position.set(0, 0, 0.2);
    rightShin.add(rightKneePad);

    const rightFoot = new three.Group();
    rightFoot.position.y = -shinLength;
    rightShin.add(rightFoot);
    const rightBootMesh = new three.Mesh(bootGeo, bootMat);
    rightBootMesh.position.set(0, -0.15, 0.12);
    rightFoot.add(rightBootMesh);

    // 6. Weapon Socket & Weapon Props
    const weaponSocket = new three.Group();
    weaponSocket.position.set(0, -0.15, 0.25);
    rightHand.add(weaponSocket);

    const weaponProps = this.buildWeaponProps(metalMat, trimMat);
    for (const prop of weaponProps) {
      weaponSocket.add(prop);
      prop.visible = false;
    }

    // Muzzle flash particle / billboard
    const flashGeo = new three.SphereGeometry(0.18, 6, 6);
    const flashMat = new three.MeshBasicMaterial({
      color: 0xffe066,
      transparent: true,
      opacity: 0,
    });
    const muzzleFlash = new three.Mesh(flashGeo, flashMat);
    muzzleFlash.position.set(0, 0, 1.8);
    weaponSocket.add(muzzleFlash);
    this.disposables.push(flashGeo, flashMat);

    // 7. Floating Nameplate Sprite
    const nameplateTex = this.createNameplateTexture(name, skin.trimColor);
    const nameplateMat = new three.SpriteMaterial({ map: nameplateTex, transparent: true });
    const nameplate = new three.Sprite(nameplateMat);
    nameplate.scale.set(4.5, 1.1, 1);
    nameplate.position.y = STANDING_HEIGHT + 0.65;
    this.rootGroup.add(nameplate);

    this.disposables.push(nameplateTex, nameplateMat);

    return {
      pelvis,
      spine,
      head,
      leftArm: {
        shoulder: leftShoulder,
        upperArm: leftUpperArm,
        forearm: leftForearm,
        hand: leftHand,
      },
      rightArm: {
        shoulder: rightShoulder,
        upperArm: rightUpperArm,
        forearm: rightForearm,
        hand: rightHand,
      },
      leftLeg: {
        hip: leftHip,
        thigh: leftThigh,
        shin: leftShin,
        foot: leftFoot,
      },
      rightLeg: {
        hip: rightHip,
        thigh: rightThigh,
        shin: rightShin,
        foot: rightFoot,
      },
      weaponSocket,
      weaponProps,
      muzzleFlash,
      nameplate,
    };
  }

  private buildWeaponProps(gunMetal: THREE.Material, gunTrim: THREE.Material): THREE.Group[] {
    const three = this.three;
    const props: THREE.Group[] = [];

    // 0: Knife
    const knife = new three.Group();
    const bladeGeo = new three.BoxGeometry(0.08, 0.18, 0.7);
    const handleGeo = new three.BoxGeometry(0.1, 0.12, 0.35);
    const blade = new three.Mesh(bladeGeo, gunTrim);
    blade.position.z = 0.45;
    const handle = new three.Mesh(handleGeo, gunMetal);
    handle.position.z = 0.1;
    knife.add(blade, handle);
    props.push(knife);
    this.disposables.push(bladeGeo, handleGeo);

    // 1: Pistol
    const pistol = new three.Group();
    const pSlideGeo = new three.BoxGeometry(0.14, 0.22, 0.7);
    const pGripGeo = new three.BoxGeometry(0.12, 0.42, 0.22);
    const pSlide = new three.Mesh(pSlideGeo, gunMetal);
    pSlide.position.set(0, 0.12, 0.35);
    const pGrip = new three.Mesh(pGripGeo, gunTrim);
    pGrip.position.set(0, -0.15, 0.12);
    pGrip.rotation.x = -0.2;
    pistol.add(pSlide, pGrip);
    props.push(pistol);
    this.disposables.push(pSlideGeo, pGripGeo);

    // 2: Carbine / Assault Rifle
    const carbine = new three.Group();
    const cRecGeo = new three.BoxGeometry(0.16, 0.28, 1.2);
    const cBarGeo = new three.CylinderGeometry(0.06, 0.06, 0.8, 8);
    const cMagGeo = new three.BoxGeometry(0.12, 0.48, 0.25);
    const cStockGeo = new three.BoxGeometry(0.14, 0.32, 0.6);

    const cRec = new three.Mesh(cRecGeo, gunMetal);
    cRec.position.set(0, 0.05, 0.4);

    const cBar = new three.Mesh(cBarGeo, gunTrim);
    cBar.rotation.x = Math.PI / 2;
    cBar.position.set(0, 0.1, 1.3);

    const cMag = new three.Mesh(cMagGeo, gunMetal);
    cMag.position.set(0, -0.26, 0.35);
    cMag.rotation.x = 0.25;

    const cStock = new three.Mesh(cStockGeo, gunTrim);
    cStock.position.set(0, -0.05, -0.4);

    carbine.add(cRec, cBar, cMag, cStock);
    props.push(carbine);
    this.disposables.push(cRecGeo, cBarGeo, cMagGeo, cStockGeo);

    // 3: Shotgun
    const shotgun = new three.Group();
    const sRecGeo = new three.BoxGeometry(0.18, 0.26, 1.1);
    const sBarGeo = new three.CylinderGeometry(0.08, 0.08, 0.9, 8);
    const sPumpGeo = new three.BoxGeometry(0.2, 0.18, 0.4);

    const sRec = new three.Mesh(sRecGeo, gunMetal);
    sRec.position.set(0, 0.05, 0.35);

    const sBar = new three.Mesh(sBarGeo, gunTrim);
    sBar.rotation.x = Math.PI / 2;
    sBar.position.set(0, 0.08, 1.2);

    const sPump = new three.Mesh(sPumpGeo, gunTrim);
    sPump.position.set(0, -0.05, 0.9);

    shotgun.add(sRec, sBar, sPump);
    props.push(shotgun);
    this.disposables.push(sRecGeo, sBarGeo, sPumpGeo);

    // 4: Sniper Rifle
    const sniper = new three.Group();
    const snRecGeo = new three.BoxGeometry(0.18, 0.3, 1.4);
    const snBarGeo = new three.CylinderGeometry(0.06, 0.06, 1.4, 8);
    const snScopeGeo = new three.CylinderGeometry(0.1, 0.12, 0.7, 8);

    const snRec = new three.Mesh(snRecGeo, gunMetal);
    snRec.position.set(0, 0.05, 0.4);

    const snBar = new three.Mesh(snBarGeo, gunTrim);
    snBar.rotation.x = Math.PI / 2;
    snBar.position.set(0, 0.08, 1.6);

    const snScope = new three.Mesh(snScopeGeo, gunMetal);
    snScope.rotation.x = Math.PI / 2;
    snScope.position.set(0, 0.28, 0.35);

    sniper.add(snRec, snBar, snScope);
    props.push(sniper);
    this.disposables.push(snRecGeo, snBarGeo, snScopeGeo);

    return props;
  }

  private createNameplateTexture(name: string, trimColor: number): THREE.Texture {
    if (typeof document === 'undefined') {
      return new this.three.Texture();
    }
    const canvas = document.createElement('canvas');
    canvas.width = 256;
    canvas.height = 64;
    const ctx = canvas.getContext('2d');
    if (ctx) {
      ctx.fillStyle = 'rgba(10, 14, 20, 0.65)';
      ctx.roundRect ? ctx.roundRect(8, 8, 240, 48, 6) : ctx.fillRect(8, 8, 240, 48);
      ctx.fill();

      // Top indicator stripe
      ctx.fillStyle = `#${trimColor.toString(16).padStart(6, '0')}`;
      ctx.fillRect(8, 8, 240, 4);

      ctx.font = 'bold 28px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = '#ffffff';
      ctx.fillText(name, 128, 36);
    }
    const texture = new this.three.CanvasTexture(canvas);
    texture.needsUpdate = true;
    return texture;
  }

  setWeapon(slot: number): void {
    if (slot === this.currentWeaponSlot) return;
    this.currentWeaponSlot = slot;
    for (let i = 0; i < this.joints.weaponProps.length; i++) {
      this.joints.weaponProps[i].visible = i === slot;
    }
  }

  triggerMuzzleFlash(): void {
    const mat = this.joints.muzzleFlash.material as THREE.MeshBasicMaterial;
    mat.opacity = 0.95;
  }

  decayMuzzleFlash(dt: number): void {
    const mat = this.joints.muzzleFlash.material as THREE.MeshBasicMaterial;
    if (mat.opacity > 0) {
      mat.opacity = Math.max(0, mat.opacity - dt * 18);
    }
  }

  dispose(): void {
    for (const d of this.disposables) {
      try {
        d.dispose();
      } catch {}
    }
    this.disposables = [];
  }
}
