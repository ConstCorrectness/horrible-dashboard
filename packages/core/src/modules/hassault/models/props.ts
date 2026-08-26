/**
 * The things an operator carries that the character GLB does not contain:
 * weapon props, a muzzle flash, and a floating nameplate.
 *
 * Lifted essentially unchanged from the primitive character model this replaced.
 * The weapons stay procedural on purpose — the FBX weapon downloads in
 * `assets/horribleAssault/` are third-party Sketchfab models whose licences are
 * unverified, and the module's standing rule is that other people's content is
 * supported, never bundled. These boxes ship.
 *
 * Indices are weapon slots, matching `PlayerRow.weapon` and the server's
 * `GET /api/hassault/weapons` ordering.
 */

import type * as THREE from 'three';

export interface PropSet {
  /** One group per weapon slot; all hidden until `setWeapon` picks one. */
  weapons: THREE.Group[];
  muzzleFlash: THREE.Mesh;
  disposables: { dispose: () => void }[];
}

/** Build the five weapon props, in slot order: knife, pistol, carbine, shotgun, sniper. */
export function buildWeaponProps(
  three: typeof THREE,
  gunMetal: THREE.Material,
  gunTrim: THREE.Material,
  disposables: { dispose: () => void }[],
): THREE.Group[] {
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
  disposables.push(bladeGeo, handleGeo);

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
  disposables.push(pSlideGeo, pGripGeo);

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
  disposables.push(cRecGeo, cBarGeo, cMagGeo, cStockGeo);

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
  disposables.push(sRecGeo, sBarGeo, sPumpGeo);

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
  disposables.push(snRecGeo, snBarGeo, snScopeGeo);

  return props;
}

/** The flash billboard that sits at the muzzle. Opacity is driven by the model. */
export function buildMuzzleFlash(
  three: typeof THREE,
  disposables: { dispose: () => void }[],
): THREE.Mesh {
  const flashGeo = new three.SphereGeometry(0.18, 6, 6);
  const flashMat = new three.MeshBasicMaterial({
    color: 0xffe066,
    transparent: true,
    opacity: 0,
  });
  const flash = new three.Mesh(flashGeo, flashMat);
  flash.position.set(0, 0, 1.8);
  disposables.push(flashGeo, flashMat);
  return flash;
}

/**
 * Draw a player's name into a texture for the floating nameplate.
 *
 * Returns a bare `Texture` without a canvas when there is no DOM, so the model
 * stays constructible under vitest — the nameplate is the one part of the rig
 * that needs a browser, and failing to build it must not take the character
 * down with it.
 */
export function createNameplateTexture(
  three: typeof THREE,
  name: string,
  trimColor: number,
): THREE.Texture {
  if (typeof document === 'undefined') {
    return new three.Texture();
  }
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 64;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    ctx.fillStyle = 'rgba(10, 14, 20, 0.65)';
    // `roundRect` is recent enough that the square fallback still earns its
    // keep. A ternary evaluated for its side effects reads as a value that was
    // meant to go somewhere, so this says plainly that it is a branch.
    if (ctx.roundRect) {
      ctx.roundRect(8, 8, 240, 48, 6);
    } else {
      ctx.fillRect(8, 8, 240, 48);
    }
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
  const texture = new three.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}
