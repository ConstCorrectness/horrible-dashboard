/**
 * The operator asset: one GLB, loaded once, instantiated per player.
 *
 * `hassault-operator.glb` is built by `scripts/build_hassault_character.mjs` from
 * a Mixamo-rigged character plus the clip FBXs in `assets/horribleAssault/
 * animations/`. It carries the skinned mesh, one shared skeleton, and all 23
 * animations — so a match costs a single fetch no matter how many players are in
 * it, and the clips arrive already bound to the skeleton that renders them.
 *
 * Both functions here exist because the obvious way to do this is wrong in a way
 * that produces no error — see the notes on each.
 */

import type * as THREE from 'three';

import type { OperatorClip } from './clips';

/** Where the built GLB is served from. Written to `apps/web/public/`. */
export const OPERATOR_URL = '/hassault-operator.glb';

export interface OperatorAsset {
  /** The loaded scene, kept as the template. Never added to a live scene. */
  readonly prototype: THREE.Object3D;
  readonly clips: ReadonlyMap<OperatorClip, THREE.AnimationClip>;
  /** `SkeletonUtils.clone`, carried here so callers never reach for the wrong one. */
  readonly clone: (source: THREE.Object3D) => THREE.Object3D;
}

let pending: Promise<OperatorAsset> | null = null;

/**
 * Fetch and parse the operator GLB, once per page.
 *
 * The promise is cached rather than the result, so eight players joining in the
 * same frame share one download instead of racing eight of them. A failed load
 * clears the cache so a later attempt can retry rather than inheriting the
 * rejection forever.
 */
import { getCachedAssetUrl } from './assetCache';

export function loadOperator(url: string = OPERATOR_URL): Promise<OperatorAsset> {
  if (pending) return pending;
  pending = (async () => {
    const [{ GLTFLoader }, SkeletonUtils] = await Promise.all([
      import('three/examples/jsm/loaders/GLTFLoader.js'),
      import('three/examples/jsm/utils/SkeletonUtils.js'),
    ]);
    const targetUrl = await getCachedAssetUrl(url);
    const gltf = await new GLTFLoader().loadAsync(targetUrl);
    const clips = new Map<OperatorClip, THREE.AnimationClip>();
    for (const clip of gltf.animations) clips.set(clip.name as OperatorClip, clip);
    return { prototype: gltf.scene, clips, clone: SkeletonUtils.clone };
  })();
  pending.catch(() => {
    pending = null;
  });
  return pending;
}

/** Drop the cached asset. Tests only — a match never needs to forget it. */
export function resetOperatorCache(): void {
  pending = null;
}

export interface OperatorInstance {
  readonly root: THREE.Object3D;
  /** Every bone, keyed by its sanitised Mixamo name (`Hips`, `RightHand`, ...). */
  readonly bones: ReadonlyMap<string, THREE.Bone>;
  /** Materials owned by this instance alone, safe to tint and fade. */
  readonly materials: THREE.Material[];
  dispose(): void;
}

/**
 * Build one player's copy of the operator.
 *
 * Two things here are load-bearing and silent when missed:
 *
 * **`SkeletonUtils.clone`, not `Object3D.clone`.** A plain clone copies the mesh
 * and the bones but leaves every copy's `SkinnedMesh` bound to the *original*
 * skeleton, so all eight players in a match share one pose and animate in
 * lockstep. Nothing warns; it just looks like the animator is broken.
 *
 * **Materials are cloned per instance.** The GLB's materials are shared across
 * every clone, so the stale-player fade in `avatars.ts` — which sets `opacity`
 * on whatever material it finds — would fade every player on the server the
 * moment one of them lagged. Cloning costs a handful of objects per player and
 * makes team tinting possible at all.
 */
export function instantiateOperator(
  three: typeof THREE,
  asset: OperatorAsset,
  tint?: number,
): OperatorInstance {
  const root = asset.clone(asset.prototype);

  const bones = new Map<string, THREE.Bone>();
  const materials: THREE.Material[] = [];
  const seen = new Set<THREE.Material>();

  root.traverse((obj) => {
    const bone = obj as THREE.Bone;
    if (bone.isBone) {
      const key = bone.name.replace(/^mixamorig[:_]?/, '').replace(/_\d+$/, '');
      if (!bones.has(key)) bones.set(key, bone);
    }

    const mesh = obj as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.receiveShadow = true;
    const source = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    const cloned = source.map((m) => {
      const copy = m.clone();
      if (seen.has(copy)) return copy;
      seen.add(copy);
      materials.push(copy);
      if (tint !== undefined) {
        const standard = copy as THREE.MeshStandardMaterial;
        // A wash, not a repaint: the texture still has to read as a uniform, and
        // a full replace would make both teams the same silhouette in one colour.
        if (standard.color) standard.color.lerp(new three.Color(tint), 0.28);
      }
      return copy;
    });
    mesh.material = Array.isArray(mesh.material) ? cloned : cloned[0];
  });

  return {
    root,
    bones,
    materials,
    dispose() {
      // Geometry belongs to the prototype and is shared by every clone —
      // disposing it here would blank every other player on the field. Only the
      // materials are this instance's to free.
      for (const m of materials) m.dispose();
    },
  };
}
