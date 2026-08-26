/**
 * The weapon in your hands, and the skin on it.
 *
 * Headless: `viewmodel.ts` imports three only as a *type* and takes the library
 * as a parameter, so the model can be built and inspected with no canvas and no
 * WebGL context — which is the only reason any of this is testable at all.
 *
 * What is worth pinning is the part that was silently missing: an equipped skin
 * reaching the gun. A weapon that renders in its default colours looks perfectly
 * fine, so "the skin is not applied" has no symptom other than someone noticing
 * that the thing they equipped is not there.
 */
import * as THREE from 'three';
import { describe, expect, it } from 'vitest';

import { equippedSkins, INSPECT_DURATION, inspectEnvelope, WeaponViewModel } from '../viewmodel';

function item(
  weaponId: string,
  overrides: {
    isEquipped?: boolean;
    floatValue?: number;
    baseColor?: string;
    accentColor?: string;
    patternType?: string;
    definition?: undefined;
  } = {},
) {
  const { isEquipped = true, floatValue = 0.03, ...rest } = overrides;
  return {
    isEquipped,
    floatValue,
    definition:
      'definition' in overrides
        ? undefined
        : {
            weaponId,
            baseColor: rest.baseColor ?? '#38bdf8',
            accentColor: rest.accentColor ?? '#f43f5e',
            patternType: rest.patternType ?? 'solid',
          },
  };
}

/** Every material colour the built model actually uses. */
function colors(vm: WeaponViewModel, camera: THREE.Camera): number[] {
  const out: number[] = [];
  camera.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    const material = mesh.material as THREE.MeshLambertMaterial | undefined;
    if (mesh.isMesh && material?.color) out.push(material.color.getHex());
  });
  void vm;
  return out;
}

function stand(): { vm: WeaponViewModel; camera: THREE.Camera } {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera();
  return { vm: new WeaponViewModel(THREE, scene, camera), camera };
}

describe('equippedSkins', () => {
  it('keys the equipped skin by its weapon', () => {
    const map = equippedSkins([
      item('assault'),
      item('sniper', { isEquipped: false }),
      item('pistol', { baseColor: '#112233' }),
    ]);
    expect(Object.keys(map).sort()).toEqual(['assault', 'pistol']);
    expect(map.pistol.baseColor).toBe('#112233');
  });

  it('skips an instance whose definition did not come with it', () => {
    // Without `baseColor` there is no skin to apply. Inventing one would put a
    // colour on the weapon that the armoury never showed the player.
    expect(equippedSkins([item('assault', { definition: undefined })])).toEqual({});
  });

  it('carries the float through, because wear is visible', () => {
    expect(equippedSkins([item('assault', { floatValue: 0.82 })]).assault.floatValue).toBe(0.82);
  });
});

describe('WeaponViewModel skins', () => {
  it('puts an equipped skin on the weapon', () => {
    // The bug this exists for: the armoury could equip a skin and the gun in
    // your hands stayed the colour it had always been.
    const plain = stand();
    plain.vm.setWeapon('assault');
    const before = colors(plain.vm, plain.camera);

    const skinned = stand();
    skinned.vm.setWeapon('assault', {
      baseColor: '#38bdf8',
      accentColor: '#f43f5e',
      patternType: 'solid',
      floatValue: 0.03,
    });
    const after = colors(skinned.vm, skinned.camera);

    expect(after.length).toBe(before.length);
    expect(after).not.toEqual(before);
  });

  it('wears a battle-scarred skin visibly duller than a factory new one', () => {
    // A float value nobody can see is a number the whole economy is built on
    // and no player can check.
    const fresh = stand();
    const worn = stand();
    const skin = {
      baseColor: '#38bdf8',
      accentColor: '#f43f5e',
      patternType: 'solid',
    };
    fresh.vm.setWeapon('assault', { ...skin, floatValue: 0.0 });
    worn.vm.setWeapon('assault', { ...skin, floatValue: 0.95 });

    // Saturation is what wear takes away: grime pulls every channel together.
    const spread = (hexes: number[]) =>
      hexes.reduce((sum, hex) => {
        const [r, g, b] = [(hex >> 16) & 0xff, (hex >> 8) & 0xff, hex & 0xff];
        return sum + (Math.max(r, g, b) - Math.min(r, g, b));
      }, 0);
    expect(spread(colors(worn.vm, worn.camera))).toBeLessThan(
      spread(colors(fresh.vm, fresh.camera)),
    );
  });

  it('rebuilds when the skin changes, not only when the weapon does', () => {
    // The materials are baked into the built model, so a skin swap that did not
    // rebuild would leave the previous skin on the gun with nothing to say so.
    const { vm, camera } = stand();
    vm.setWeapon('assault', {
      baseColor: '#38bdf8',
      accentColor: '#f43f5e',
      patternType: 'solid',
      floatValue: 0.03,
    });
    const first = colors(vm, camera);
    vm.setWeapon('assault', {
      baseColor: '#eab308',
      accentColor: '#dc2626',
      patternType: 'fade',
      floatValue: 0.03,
    });
    expect(colors(vm, camera)).not.toEqual(first);
  });

  it('is still a no-op when neither the weapon nor the skin changed', () => {
    // The render loop calls this every frame with whatever the server last said.
    const { vm, camera } = stand();
    const skin = {
      baseColor: '#38bdf8',
      accentColor: '#f43f5e',
      patternType: 'solid',
      floatValue: 0.03,
    };
    vm.setWeapon('assault', skin);
    const before = colors(vm, camera);
    vm.setWeapon('assault', { ...skin });
    expect(colors(vm, camera)).toEqual(before);
  });

  it('draws a different gun for each pattern type', () => {
    // `patternType` cannot be a texture on a weapon made of boxes, so it decides
    // how the two colours are distributed across the parts instead. `patina` and
    // `custom_art` used to fall through to the `solid` arrangement, which meant
    // a Case Hardened and a Slate were the same object in two colours — with the
    // armoury card promising otherwise.
    const seen = new Map<string, string>();
    for (const patternType of ['solid', 'camo', 'anodized', 'fade', 'patina', 'custom_art']) {
      const { vm, camera } = stand();
      vm.setWeapon('assault', {
        baseColor: '#38bdf8',
        accentColor: '#f43f5e',
        patternType,
        floatValue: 0.03,
      });
      const key = colors(vm, camera).join(',');
      expect(seen.has(key), `${patternType} draws the same as ${seen.get(key)}`).toBe(false);
      seen.set(key, patternType);
    }
  });

  it('falls back to the default palette for an unparseable colour', () => {
    // The catalogue is data, and a client that rendered `undefined` as black
    // would show a weapon nobody designed.
    const { vm, camera } = stand();
    vm.setWeapon('assault', {
      baseColor: 'not-a-colour',
      accentColor: '',
      patternType: 'solid',
      floatValue: 0,
    });
    const hexes = colors(vm, camera);
    expect(hexes.length).toBeGreaterThan(0);
    expect(hexes.every((hex) => Number.isInteger(hex) && hex >= 0)).toBe(true);
  });
});

describe('inspect', () => {
  const frame = {
    speed: 0,
    onGround: true,
    reloading: false,
    yaw: 0,
    pitch: 0,
    visible: true,
  };

  it('runs for its duration and then stops on its own', () => {
    const { vm } = stand();
    vm.setWeapon('assault');
    expect(vm.inspecting).toBe(false);
    vm.inspect();
    expect(vm.inspecting).toBe(true);
    // Just short of the end it is still running…
    for (let t = 0; t < INSPECT_DURATION - 0.1; t += 0.05) vm.update(0.05, frame);
    expect(vm.inspecting).toBe(true);
    // …and past it, it has put itself away. A pose that needed cancelling would
    // be one you could get stuck in.
    for (let t = 0; t < 0.3; t += 0.05) vm.update(0.05, frame);
    expect(vm.inspecting).toBe(false);
  });

  it('is cancelled by firing', () => {
    // The pose swings the barrel away from the crosshair, so a shot drawn
    // mid-animation leaves a weapon pointing at the floor — a picture of a shot
    // that did not happen, since the server resolved it against the real angles.
    const { vm } = stand();
    vm.setWeapon('assault');
    vm.inspect();
    vm.fire();
    expect(vm.inspecting).toBe(false);
  });

  it('is cancelled by a reload, which is the animation the server is doing', () => {
    const { vm } = stand();
    vm.setWeapon('assault');
    vm.inspect();
    vm.update(0.05, { ...frame, reloading: true });
    expect(vm.inspecting).toBe(false);
  });

  it('does not resume after a death', () => {
    const { vm } = stand();
    vm.setWeapon('assault');
    vm.inspect();
    vm.update(0.05, { ...frame, visible: false });
    expect(vm.inspecting).toBe(false);
  });

  it('never starts without a weapon to look at', () => {
    const { vm } = stand();
    vm.inspect();
    expect(vm.inspecting).toBe(false);
  });

  it('has an envelope that starts and ends at rest', () => {
    // Both ends matter: a pose that does not start at zero snaps into place on
    // the first frame, and one that does not end at zero leaves the weapon a few
    // degrees off home for the rest of the match.
    expect(inspectEnvelope(0)).toBeCloseTo(0, 5);
    expect(inspectEnvelope(INSPECT_DURATION)).toBeCloseTo(0, 5);
    expect(inspectEnvelope(INSPECT_DURATION / 2)).toBeCloseTo(1, 5);
  });

  it('never leaves its envelope, so the weapon cannot be flung off screen', () => {
    for (let t = -0.5; t < INSPECT_DURATION + 0.5; t += 0.01) {
      const w = inspectEnvelope(t);
      expect(w).toBeGreaterThanOrEqual(0);
      expect(w).toBeLessThanOrEqual(1);
    }
  });
});
