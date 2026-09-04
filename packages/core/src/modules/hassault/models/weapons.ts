/**
 * Weapon props: one GLB per weapon, loaded once, cloned into the hands.
 *
 * Built by `scripts/build_hassault_weapon.mjs` from the models in
 * `assets/horribleAssault/`, already scaled to cube units and oriented so the
 * barrel runs down `-Z`. See docs/modules/hassault.mdx for what the converter
 * has to get right.
 *
 * **Not every weapon has one, and that is a supported state rather than a gap.**
 * `viewmodel.ts` builds its procedural boxes first and swaps a GLB in behind
 * them if one arrives, so a weapon with no model, a 404, or a load that has not
 * finished yet all render the boxes — which is exactly what the game did before
 * any of this existed. The alternative, making the view model wait, would mean a
 * player who deploys on a slow connection holding nothing at all, and an empty
 * hand reads as a broken client rather than as a pending fetch.
 */

import type * as THREE from 'three';

/**
 * Which weapons have a prop, and where it is served from.
 *
 * Keyed by the backend's weapon ids (`weapons.py`): knife, pistol, assault,
 * shotgun, sniper. A weapon absent from this map has no prop and never asks for
 * one — a missing entry is a decision, an entry pointing at a missing file is a
 * failed fetch, and only the second is worth a console line.
 *
 * `knife` is deliberately absent: there is no knife model at all. The M4A1 used
 * to be too, at 687k triangles — twenty times the whole map — and is here now
 * because `scripts/decimate_weapon.py` takes it to 30k, which is the same order
 * as the other three.
 */
export const WEAPON_MODEL_URLS: Readonly<Record<string, string>> = {
  pistol: '/hassault-weapon-pistol.glb',
  assault: '/hassault-weapon-assault.glb',
  shotgun: '/hassault-weapon-shotgun.glb',
  sniper: '/hassault-weapon-sniper.glb',
};

export interface WeaponModel {
  /** The loaded scene, kept as a template and never added to a live scene. */
  readonly prototype: THREE.Object3D;
  readonly animations?: THREE.AnimationClip[];
}

const pending = new Map<string, Promise<WeaponModel>>();

/**
 * Fetch and parse one weapon's GLB, once per page.
 *
 * The **promise** is cached rather than the result, so swapping back and forth
 * between two weapons mid-match does not re-download either. A failed load drops
 * its entry so a later attempt retries instead of inheriting the rejection
 * forever — the same contract `loadOperator` follows, and for the same reason.
 *
 * Resolves to `null` for a weapon with no prop, rather than rejecting: "this
 * weapon is boxes" is an ordinary answer and making callers tell it apart from a
 * network failure by catching would guarantee they eventually stop trying.
 */
import { getCachedAssetUrl } from './assetCache';

export function loadWeaponModel(id: string): Promise<WeaponModel | null> {
  const url = id === 'fal' ? '/hassault-weapon-fal.glb' : WEAPON_MODEL_URLS[id];
  if (!url) return Promise.resolve(null);
  const cached = pending.get(id);
  if (cached) return cached;
  const task = (async (): Promise<WeaponModel> => {
    const { GLTFLoader } = await import('three/examples/jsm/loaders/GLTFLoader.js');
    const targetUrl = await getCachedAssetUrl(url);
    const gltf = await new GLTFLoader().loadAsync(targetUrl);
    return { prototype: gltf.scene, animations: gltf.animations || [] };
  })();
  pending.set(id, task);
  task.catch(() => pending.delete(id));
  return task;
}

/** Drop every cached prop. Tests only. */
export function resetWeaponModelCache(): void {
  pending.clear();
}

/**
 * One weapon's own copy of a prop, sized and placed to stand in for a box model.
 *
 * The **fit is measured, not tuned per weapon**, and that is what makes this
 * safe to drop in: the clone is translated so its bounding box centre lands on
 * the box model's, so it occupies the same space the boxes did. Everything
 * expressed relative to that space — `HOME`, the bob, the sway, the recoil kick,
 * the muzzle offset — keeps working untouched, and a converter run with a
 * slightly different `--length` moves the model rather than breaking the pose.
 *
 * Aligning centres rather than origins is the point. The converter puts a
 * prop's origin at the rear of its bounding box, which for a rifle is the
 * buttstock; the box models are built around roughly where a hand is. Matching
 * origins would hang every rifle a foot forward of the screen.
 */
export function fitWeaponModel(
  three: typeof THREE,
  prototype: THREE.Object3D,
  target: THREE.Object3D,
): { model: THREE.Object3D; muzzle: [number, number, number] } {
  const model = prototype.clone(true);

  // Materials are cloned too. Without this the skin tint applied below is
  // written onto the shared prototype, so equipping a skin on one weapon would
  // colour every future copy of it — including the ones already in other
  // players' hands if this ever renders more than the local view model.
  model.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.material = Array.isArray(mesh.material)
      ? mesh.material.map((m) => m.clone())
      : mesh.material.clone();
  });

  const targetBox = new three.Box3().setFromObject(target);
  const modelBox = new three.Box3().setFromObject(model);
  // An empty target — a weapon whose boxes have not been added yet — would give
  // an inverted box whose centre is ±Infinity, and a model translated by
  // infinity vanishes with no error anywhere. Fall back to leaving it where the
  // converter put it.
  if (targetBox.isEmpty() || modelBox.isEmpty()) {
    // Said out loud. The fallback is safe, but its symptom — a weapon sitting a
    // little off from where the boxes were — looks like a bad export rather than
    // like a measurement that never happened, and the grip anchors are measured
    // in this same space.
    console.warn(
      'hassault: could not measure the weapon fit; leaving the model where the converter put it',
    );
  } else {
    const targetCentre = targetBox.getCenter(new three.Vector3());
    const modelCentre = modelBox.getCenter(new three.Vector3());
    model.position.copy(targetCentre.sub(modelCentre));
  }

  // The muzzle is the front-centre of the model *where it now sits*: the
  // converter points every barrel down -Z, so the front is the minimum z. The
  // box is the measured one translated by the fit, rather than a fresh
  // `setFromObject`, because the model has not been added to a parent yet and
  // its world matrix is still stale — measuring it again here would return the
  // unfitted box and put the muzzle flash inside the receiver.
  const fitted = modelBox.clone().translate(model.position);
  const centre = fitted.getCenter(new three.Vector3());
  return { model, muzzle: [centre.x, centre.y, fitted.min.z] };
}

/**
 * The environment a metal weapon reflects.
 *
 * **Without this every prop renders nearly black**, and it looks like the
 * textures failed to load rather than like a lighting model doing exactly what
 * it is supposed to. A metallic surface in a physically-based renderer has no
 * diffuse response at all — it only reflects — so a scene lit purely by
 * analytic lights gives it nothing to return. Measured on the SVU with the
 * hassault rig: mean luminance 3.5/255 and 93% of its pixels near-black without
 * an environment, 41.2 and 21% with one.
 *
 * The gradient is the **hemisphere light's own two colours**, not a studio box.
 * `RoomEnvironment` is the usual answer and it is the wrong one here: it would
 * light the weapon in your hands from a room that is not the map you are
 * standing in, and a gun lit differently from its surroundings reads as pasted
 * on — the same failure the shared `lighting.wgsl.inc` exists to prevent
 * between the two clients.
 *
 * Prefiltered through `PMREMGenerator` because a raw texture assigned as an
 * environment ignores roughness: every surface would mirror it equally and a
 * matte grip would come out as shiny as a barrel.
 */
/**
 * How strongly a prop reflects `createPropEnvironment`.
 *
 * The scene's hemisphere light runs at 1.55 and the environment encodes its two
 * colours at 1.0, so this is the factor that puts the two on the same footing
 * rather than a number picked because it looked better. Measured on the SVU:
 * mean luminance 14.9 at 1.0 against 23.5 here, with nothing clipping at either
 * (0% of pixels blown at 1.0, 1.55 or even 2.2 — the headroom is real, the
 * weapons are simply dark).
 */
export const PROP_ENV_INTENSITY = 1.55;

export function createPropEnvironment(
  three: typeof THREE,
  renderer: THREE.WebGLRenderer,
): THREE.Texture {
  // **Twice as wide as it is tall, and not tiny.** Both halves of that were got
  // wrong first time and neither failed loudly. An equirectangular map is 2:1
  // with longitude across and latitude down; a portrait one is not an equirect
  // at all. And a source this small drives `PMREMGenerator` to a degenerate
  // output — a 16x32 source produced a 336x16 CubeUV texture that renders
  // **pure black**, which is indistinguishable from having no environment and
  // is exactly the symptom this function exists to cure. Measured: a metal
  // sphere lit by it had zero non-black pixels.
  //
  // 128x64 is still nothing to generate and prefilters to a sane pyramid. The
  // content is a two-stop gradient, so resolution beyond this buys nothing.
  const W = 128;
  const H = 64;
  const data = new Uint8Array(W * H * 4);
  const sky = { r: 0xbf, g: 0xd4, b: 0xff };
  const ground = { r: 0x33, g: 0x30, b: 0x2c };
  for (let y = 0; y < H; y++) {
    // Row 0 is the top of an equirect map, which is up.
    const t = y / (H - 1);
    const r = Math.round(sky.r + (ground.r - sky.r) * t);
    const g = Math.round(sky.g + (ground.g - sky.g) * t);
    const b = Math.round(sky.b + (ground.b - sky.b) * t);
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      data[i] = r;
      data[i + 1] = g;
      data[i + 2] = b;
      data[i + 3] = 255;
    }
  }
  const source = new three.DataTexture(data, W, H, three.RGBAFormat);
  source.mapping = three.EquirectangularReflectionMapping;
  // Authored as hex, so sRGB — the same rule the operator's team wash follows.
  // Left linear it would reflect a washed-out sky.
  source.colorSpace = three.SRGBColorSpace;
  source.needsUpdate = true;

  const pmrem = new three.PMREMGenerator(renderer);
  const target = pmrem.fromEquirectangular(source);
  // Both are finished with: the render target's texture is what survives, and
  // the generator holds GPU resources until it is told otherwise.
  pmrem.dispose();
  source.dispose();
  return target.texture;
}
