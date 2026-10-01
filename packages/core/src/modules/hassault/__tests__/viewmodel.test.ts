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
import { clipFor, sampleInspect } from '../inspects';
import { describe, expect, it } from 'vitest';

import {
  equippedSkins,
  WeaponViewModel,
} from '../viewmodel';

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
    const duration = clipFor('assault').duration;
    for (let t = 0; t < duration - 0.1; t += 0.05) vm.update(0.05, frame);
    expect(vm.inspecting).toBe(true);
    // …and past it, it has put itself away. A pose that needed cancelling would
    // be one you could get stuck in.
    for (let t = 0; t < 0.3; t += 0.05) vm.update(0.05, frame);
    expect(vm.inspecting).toBe(false);
  });

  it('never stops moving partway through', () => {
    // The bug this pose was rebuilt for. It used to be one scalar driving every
    // axis through an envelope that *holds*, so the weapon travelled out, froze
    // for the length of the hold, and retraced its path — which reads as a
    // stutter rather than as a weapon being turned over.
    //
    // Asserted on the pivot rather than on the envelope, because the envelope
    // still holds at 1 and is supposed to: what must not hold still is the gun.
    const { vm, camera } = stand();
    vm.setWeapon('assault');
    vm.update(0.016, frame);
    vm.inspect();

    // The pivot is parented to the camera (see `dispose`), so the pose is
    // readable without a test-only accessor on the view model.
    const pivot = camera.children[0];
    const pose = () => [
      pivot.position.x,
      pivot.position.y,
      pivot.position.z,
      pivot.rotation.x,
      pivot.rotation.y,
      pivot.rotation.z,
    ];
    let previous = pose();
    for (let i = 0; i < Math.floor(clipFor('assault').duration / 0.016) - 2; i += 1) {
      vm.update(0.016, frame);
      const now = pose();
      const moved = now.some((v, j) => Math.abs(v - previous[j]) > 1e-5);
      expect(moved).toBe(true);
      previous = now;
    }
  });

  it('lands back exactly at rest', () => {
    // A clip whose last frame is a few degrees off leaves the weapon off home for
    // the rest of the match.
    const { vm, camera } = stand();
    vm.setWeapon('pistol');
    // Past the draw, which starts the weapon stowed below the frame.
    for (let t = 0; t < 0.6; t += 0.016) vm.update(0.016, frame);
    const pivot = camera.children[0];
    const before = [pivot.position.clone(), pivot.rotation.clone()] as const;
    vm.inspect();
    for (let t = 0; t < clipFor('pistol').duration + 0.2; t += 0.016) vm.update(0.016, frame);
    expect(vm.inspecting).toBe(false);
    expect(pivot.position.distanceTo(before[0])).toBeLessThan(1e-3);
    expect(Math.abs(pivot.rotation.z - before[1].z)).toBeLessThan(1e-3);
  });

  it('runs a different choreography for each weapon', () => {
    // Read off the pivot a third of the way in: the rifle is rolled to its
    // ejection port, the pistol tilted to its slide the other way.
    const rollAt = (weapon: string) => {
      const { vm, camera } = stand();
      vm.setWeapon(weapon);
      vm.update(0.016, frame);
      vm.inspect();
      const target = clipFor(weapon).duration * 0.3;
      for (let t = 0; t < target; t += 0.016) vm.update(0.016, frame);
      return camera.children[0].rotation.z;
    };
    expect(rollAt('assault')).toBeGreaterThan(1);
    expect(rollAt('pistol')).toBeLessThan(-0.3);
    expect(sampleInspect('shotgun', 0.4).support[2]).toBeGreaterThan(0.05);
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

});

describe('Knife archetypes and PBR skin materials', () => {
  const frame = {
    speed: 0,
    onGround: true,
    reloading: false,
    yaw: 0,
    pitch: 0,
    visible: true,
  };

  it('builds distinct procedural meshes for Karambit, Butterfly, Bayonet, and default knives', () => {
    const defaultKnife = stand();
    defaultKnife.vm.setWeapon('knife');
    const defaultColors = colors(defaultKnife.vm, defaultKnife.camera);

    const karambit = stand();
    karambit.vm.setWeapon('knife', {
      id: 'knife_karambit_fade',
      name: 'Karambit | Fade',
      baseColor: '#38bdf8',
      accentColor: '#f43f5e',
      patternType: 'fade',
      floatValue: 0.01,
    });
    const karambitColors = colors(karambit.vm, karambit.camera);

    const butterfly = stand();
    butterfly.vm.setWeapon('knife', {
      id: 'knife_butterfly_marble',
      name: 'Butterfly Knife | Marble Fade',
      baseColor: '#ef4444',
      accentColor: '#3b82f6',
      patternType: 'fade',
      floatValue: 0.02,
    });
    const butterflyColors = colors(butterfly.vm, butterfly.camera);

    const bayonet = stand();
    bayonet.vm.setWeapon('knife', {
      id: 'knife_bayonet_lore',
      name: 'Tactical Bayonet | Lore',
      baseColor: '#eab308',
      accentColor: '#15803d',
      patternType: 'anodized',
      floatValue: 0.03,
    });
    const bayonetColors = colors(bayonet.vm, bayonet.camera);

    const skeleton = stand();
    skeleton.vm.setWeapon('knife', {
      id: 'knife_skeleton_crimson',
      name: 'Skeleton Knife | Crimson Web',
      baseColor: '#dc2626',
      accentColor: '#18181b',
      patternType: 'custom_art',
      floatValue: 0.02,
    });
    const skeletonColors = colors(skeleton.vm, skeleton.camera);

    const huntsman = stand();
    huntsman.vm.setWeapon('knife', {
      id: 'knife_huntsman_case_hardened',
      name: 'Huntsman Knife | Case Hardened',
      baseColor: '#ca8a04',
      accentColor: '#2563eb',
      patternType: 'patina',
      floatValue: 0.04,
    });
    const huntsmanColors = colors(huntsman.vm, huntsman.camera);

    expect(karambitColors.length).toBeGreaterThan(0);
    expect(butterflyColors.length).toBeGreaterThan(0);
    expect(bayonetColors.length).toBeGreaterThan(0);
    expect(skeletonColors.length).toBeGreaterThan(0);
    expect(huntsmanColors.length).toBeGreaterThan(0);
    expect(defaultColors.length).toBeGreaterThan(0);

    // Each knife archetype has a distinct part count reflecting its specific model
    expect(karambitColors.length).not.toEqual(butterflyColors.length);
    expect(butterflyColors.length).not.toEqual(bayonetColors.length);
    expect(skeletonColors.length).not.toEqual(huntsmanColors.length);
  });

  it('allows continuous knife flourish looping when inspect is pressed repeatedly', () => {
    const { vm } = stand();
    vm.setWeapon('knife');
    vm.inspect();
    expect(vm.inspecting).toBe(true);

    // Re-triggering inspect while inspecting loops and stays inspecting
    vm.inspect();
    expect(vm.inspecting).toBe(true);
  });

  it('cancels inspect when aiming down sights', () => {
    const { vm } = stand();
    vm.setWeapon('assault');
    vm.inspect();
    expect(vm.inspecting).toBe(true);

    // Aiming down sights (ads = 1) cancels inspect
    vm.update(0.05, { ...frame, ads: 1.0 });
    expect(vm.inspecting).toBe(false);
  });

  it('applies dry reload bolt rack displacement during empty reload', () => {
    const { vm } = stand();
    vm.setWeapon('assault');

    // Sample pivot at 75% reload progress during tactical vs empty reload
    vm.update(0.016, {
      ...frame,
      reloading: true,
      reloadingEmpty: false,
      reloadProgress: 0.75,
    });
    const tacticalZ = vm.pivot.position.z;

    vm.update(0.016, {
      ...frame,
      reloading: true,
      reloadingEmpty: true,
      reloadProgress: 0.75,
    });
    const emptyZ = vm.pivot.position.z;

    // Empty reload pulls bolt rearward (more negative Z)
    expect(emptyZ).toBeLessThan(tacticalZ);
  });

  it('builds high-fidelity detailed weapon models with rich part counts (anti-roblox)', () => {
    const pistol = stand();
    pistol.vm.setWeapon('pistol');
    const pistolMeshCount = colors(pistol.vm, pistol.camera).length;
    expect(pistolMeshCount).toBeGreaterThanOrEqual(20);

    const shotgun = stand();
    shotgun.vm.setWeapon('shotgun');
    const shotgunMeshCount = colors(shotgun.vm, shotgun.camera).length;
    expect(shotgunMeshCount).toBeGreaterThanOrEqual(25);

    const sniper = stand();
    sniper.vm.setWeapon('sniper');
    const sniperMeshCount = colors(sniper.vm, sniper.camera).length;
    expect(sniperMeshCount).toBeGreaterThanOrEqual(30);

    const assault = stand();
    assault.vm.setWeapon('assault');
    const assaultMeshCount = colors(assault.vm, assault.camera).length;
    expect(assaultMeshCount).toBeGreaterThanOrEqual(35);
  });

  it('executes category-specific inspect choreography for pistol, sniper, shotgun, and knives', () => {
    // Pistol inspect (one-handed slide tilt)
    const pistol = stand();
    pistol.vm.setWeapon('pistol');
    pistol.vm.inspect();
    pistol.vm.update(0.8, { ...frame });
    expect(pistol.vm.inspecting).toBe(true);
    expect(pistol.vm.pivot.rotation.z).not.toBe(0);

    // Sniper inspect (optic & chamber pan)
    const sniper = stand();
    sniper.vm.setWeapon('sniper');
    sniper.vm.inspect();
    sniper.vm.update(0.8, { ...frame });
    expect(sniper.vm.inspecting).toBe(true);
    expect(sniper.vm.pivot.rotation.y).not.toBe(0);

    // Karambit knife inspect (ring spin flourish)
    const karambit = stand();
    karambit.vm.setWeapon('knife', {
      id: 'knife_karambit_fade',
      name: 'Karambit | Fade',
      baseColor: '#ec4899',
      accentColor: '#8b5cf6',
      patternType: 'marble',
      floatValue: 0.01,
    });
    karambit.vm.inspect();
    karambit.vm.update(0.5, { ...frame });
    expect(karambit.vm.inspecting).toBe(true);
    // Karambit executes high roll rotation during ring twirl
    expect(Math.abs(karambit.vm.pivot.rotation.z)).toBeGreaterThan(0.5);
  });

  it('executes multi-phase reload impulses (mag drop, mag seat, bolt rack)', () => {
    const { vm } = stand();
    vm.setWeapon('assault');

    // Mag drop jolt at ~0.22 progress
    vm.update(0.016, { ...frame, reloading: true, reloadProgress: 0.22 });
    const dropY = vm.pivot.position.y;

    // Mag seat slam at ~0.62 progress
    vm.update(0.016, { ...frame, reloading: true, reloadProgress: 0.62 });
    const seatY = vm.pivot.position.y;

    // Both phases induce dynamic vertical impulses distinct from baseline rest
    expect(dropY).not.toBe(0);
    expect(seatY).not.toBe(0);
  });
});



