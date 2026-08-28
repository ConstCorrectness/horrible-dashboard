/**
 * Weapon props: the fit, and the states that must leave the boxes standing.
 *
 * The interesting property is that the fit is *measured* rather than tuned per
 * weapon — everything the view model expresses relative to the model's space
 * (`HOME`, the bob, the sway, the muzzle) keeps working only if the prop lands
 * in the same space the boxes occupied.
 */

import { describe, expect, it } from 'vitest';
import * as THREE from 'three';

import { WEAPON_MODEL_URLS, fitWeaponModel, loadWeaponModel } from '../models/weapons';

/** A stand-in prop: a long box, sitting where the converter leaves one. */
function prototypeAt(min: THREE.Vector3, max: THREE.Vector3): THREE.Object3D {
  const size = max.clone().sub(min);
  const mesh = new THREE.Mesh(
    new THREE.BoxGeometry(size.x, size.y, size.z),
    new THREE.MeshStandardMaterial(),
  );
  mesh.position.copy(min.clone().add(max).multiplyScalar(0.5));
  const group = new THREE.Group();
  group.add(mesh);
  return group;
}

describe('loadWeaponModel', () => {
  it('answers null for a weapon with no prop rather than rejecting', async () => {
    // "This weapon is boxes" is an ordinary answer. Making callers tell it apart
    // from a network failure by catching guarantees they eventually stop trying.
    await expect(loadWeaponModel('knife')).resolves.toBeNull();
    await expect(loadWeaponModel('nonsense')).resolves.toBeNull();
  });

  it('only claims props for weapons that have one built', () => {
    // There is no knife model, so it is deliberately absent — an entry here
    // pointing at a file that does not exist would turn a decision into a
    // failed fetch on every weapon swap.
    expect(Object.keys(WEAPON_MODEL_URLS).sort()).toEqual([
      'assault',
      'pistol',
      'shotgun',
      'sniper',
    ]);
    for (const url of Object.values(WEAPON_MODEL_URLS)) {
      expect(url.startsWith('/hassault-weapon-')).toBe(true);
    }
  });
});

describe('fitWeaponModel', () => {
  it('lands the prop centred where the box model was', () => {
    // The converter puts a prop's origin at the rear of its bounding box — for a
    // rifle, the buttstock — while the box models are built around roughly where
    // a hand is. Matching origins instead of centres hangs every rifle a foot
    // forward of the screen.
    const prototype = prototypeAt(new THREE.Vector3(-0.1, 0, -2.9), new THREE.Vector3(0.1, 0.7, 0));
    const target = prototypeAt(
      new THREE.Vector3(-0.2, -0.2, -1.2),
      new THREE.Vector3(0.2, 0.2, 0.6),
    );

    const { model } = fitWeaponModel(THREE, prototype, target);
    model.updateMatrixWorld(true);
    const fitted = new THREE.Box3().setFromObject(model);
    const targetBox = new THREE.Box3().setFromObject(target);

    const a = fitted.getCenter(new THREE.Vector3());
    const b = targetBox.getCenter(new THREE.Vector3());
    expect(a.x).toBeCloseTo(b.x, 5);
    expect(a.y).toBeCloseTo(b.y, 5);
    expect(a.z).toBeCloseTo(b.z, 5);
  });

  it('does not resize the prop', () => {
    // Scale is the converter's job (`--length`), decided against a real weapon's
    // real length. A fit that also scaled would silently make every weapon the
    // size of whichever box model it replaced.
    const prototype = prototypeAt(new THREE.Vector3(-0.1, 0, -2.9), new THREE.Vector3(0.1, 0.7, 0));
    const target = prototypeAt(new THREE.Vector3(0, 0, 0), new THREE.Vector3(0.1, 0.1, 0.1));

    const { model } = fitWeaponModel(THREE, prototype, target);
    model.updateMatrixWorld(true);
    const size = new THREE.Box3().setFromObject(model).getSize(new THREE.Vector3());
    expect(size.z).toBeCloseTo(2.9, 5);
    expect(size.y).toBeCloseTo(0.7, 5);
  });

  it('puts the muzzle at the front of the fitted model', () => {
    // The converter points every barrel down -Z, so the front is the minimum z —
    // and it has to be the *fitted* box, since the flash is added into the same
    // space the model was moved into.
    const prototype = prototypeAt(new THREE.Vector3(-0.1, 0, -2.9), new THREE.Vector3(0.1, 0.7, 0));
    const target = prototypeAt(
      new THREE.Vector3(-0.2, -0.2, -1.2),
      new THREE.Vector3(0.2, 0.2, 0.6),
    );

    const { model, muzzle } = fitWeaponModel(THREE, prototype, target);
    model.updateMatrixWorld(true);
    const fitted = new THREE.Box3().setFromObject(model);
    expect(muzzle[2]).toBeCloseTo(fitted.min.z, 5);
    expect(muzzle[0]).toBeCloseTo(0, 5);
  });

  it('clones materials so a tint never reaches the prototype', () => {
    // The prototype is shared by every future copy. Writing a skin's colour onto
    // it would colour weapons nobody has equipped that skin on, and the symptom
    // appears one weapon swap later than the cause.
    const prototype = prototypeAt(new THREE.Vector3(0, 0, -1), new THREE.Vector3(1, 1, 0));
    const target = prototypeAt(new THREE.Vector3(0, 0, -1), new THREE.Vector3(1, 1, 0));

    const { model } = fitWeaponModel(THREE, prototype, target);
    let tinted = 0;
    model.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (!mesh.isMesh) return;
      (mesh.material as THREE.MeshStandardMaterial).color.setHex(0xff0000);
      tinted++;
    });
    expect(tinted).toBeGreaterThan(0);

    prototype.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (!mesh.isMesh) return;
      expect((mesh.material as THREE.MeshStandardMaterial).color.getHex()).toBe(0xffffff);
    });
  });

  it('leaves the prop where it is when the target has no geometry', () => {
    // An empty box has an inverted range whose centre is ±Infinity, and a model
    // translated by infinity vanishes with no error anywhere.
    const prototype = prototypeAt(new THREE.Vector3(0, 0, -1), new THREE.Vector3(1, 1, 0));
    const { model } = fitWeaponModel(THREE, prototype, new THREE.Group());
    expect(Number.isFinite(model.position.x)).toBe(true);
    expect(model.position.lengthSq()).toBe(0);
  });
});
