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
import { shareSkeletons } from './skeletons';

/** Where the built GLB is served from. Written to `apps/web/public/`. */
export const OPERATOR_URL = '/hassault-operator.glb';
export const OPERATOR_CT_URL = '/hassault-operator.glb';
export const OPERATOR_T_URL = '/hassault-operator-t.glb';

/**
 * A player's light floor, as an emissive fraction of the uniform's colour.
 *
 * A player is lit the same at every graphics tier, because how visible somebody
 * in a dark corner is decides gunfights — and Low draws none of the map's lamps,
 * so without a floor a player under one is a black shape on Low and lit on High.
 * The native skin shader adds the same fraction (`CHARACTER_FLOOR` in
 * `skin.wgsl`); `browser_parity.rs` pins the two.
 */
export const CHARACTER_FLOOR = 0.12;

export interface OperatorAsset {
  /** The loaded scene, kept as the template. Never added to a live scene. */
  readonly prototype: THREE.Object3D;
  readonly clips: ReadonlyMap<OperatorClip, THREE.AnimationClip>;
  /** `SkeletonUtils.clone`, carried here so callers never reach for the wrong one. */
  readonly clone: (source: THREE.Object3D) => THREE.Object3D;
}

const cache = new Map<string, Promise<OperatorAsset>>();

/**
 * Fetch and parse the operator GLB, once per page.
 *
 * The promise is cached per URL rather than the result, so eight players joining in the
 * same frame share one download instead of racing eight of them. A failed load
 * clears the cache so a later attempt can retry rather than inheriting the
 * rejection forever.
 */
import { getCachedAssetUrl } from './assetCache';

export function loadOperator(url: string = OPERATOR_URL): Promise<OperatorAsset> {
  const existing = cache.get(url);
  if (existing) return existing;
  const pending = (async () => {
    const [{ GLTFLoader }, SkeletonUtils, { mergeGeometries }] = await Promise.all([
      import('three/examples/jsm/loaders/GLTFLoader.js'),
      import('three/examples/jsm/utils/SkeletonUtils.js'),
      import('three/examples/jsm/utils/BufferGeometryUtils.js'),
    ]);
    const targetUrl = await getCachedAssetUrl(url);
    const gltf = await new GLTFLoader().loadAsync(targetUrl);
    // Once, on the template: every clone inherits the merged meshes.
    mergeSkinnedPrimitives(gltf.scene, mergeGeometries);
    presetSkinnedBounds(gltf.scene);
    const clips = new Map<OperatorClip, THREE.AnimationClip>();
    for (const clip of gltf.animations) clips.set(clip.name as OperatorClip, clip);
    // Each clone's two skinned meshes then share one skeleton (`skeletons.ts`).
    const clone = (source: THREE.Object3D) => shareSkeletons(SkeletonUtils.clone(source));
    return { prototype: gltf.scene, clips, clone };
  })();
  pending.catch(() => {
    cache.delete(url);
  });
  cache.set(url, pending);
  return pending;
}

/**
 * Merge the skinned primitives that would draw identically into one mesh each.
 *
 * glTF splits a mesh into one primitive per contiguous run of a material, and
 * GLTFLoader makes each primitive its own `SkinnedMesh`: the T operator is one
 * mesh, one skin and **two** materials, and loads as 71 draws. Six of them on the
 * range were ~430 of Bank's 690 draws; a full match would be over 700 before the
 * map. Primitives are merged only when nothing about drawing them differs —
 * same parent, material, skeleton, bind matrix and attribute layout, and an
 * identity local transform — so the merged mesh skins exactly as its parts did.
 *
 * Returns how many meshes were folded away.
 */
export function mergeSkinnedPrimitives(
  root: THREE.Object3D,
  mergeGeometries: (geometries: THREE.BufferGeometry[], useGroups?: boolean) => THREE.BufferGeometry | null,
): number {
  const parents = new Set<THREE.Object3D>();
  root.traverse((obj) => {
    if ((obj as THREE.SkinnedMesh).isSkinnedMesh && obj.parent) parents.add(obj.parent);
  });
  let removed = 0;
  for (const parent of parents) {
    const groups = new Map<string, THREE.SkinnedMesh[]>();
    for (const child of parent.children) {
      const mesh = child as THREE.SkinnedMesh;
      if (!mesh.isSkinnedMesh || Array.isArray(mesh.material) || mesh.morphTargetInfluences) continue;
      mesh.updateMatrix();
      if (!isIdentity(mesh.matrix.elements)) continue;
      const geometry = mesh.geometry;
      const key = [
        mesh.material.uuid,
        mesh.skeleton.uuid,
        mesh.bindMatrix.elements.join(','),
        Object.keys(geometry.attributes).sort().join(','),
        geometry.index ? 'indexed' : 'flat',
      ].join('|');
      const list = groups.get(key);
      if (list) list.push(mesh);
      else groups.set(key, [mesh]);
    }
    for (const list of groups.values()) {
      if (list.length < 2) continue;
      const merged = mergeGeometries(
        list.map((m) => m.geometry),
        false,
      );
      if (!merged) continue;
      const [keep, ...rest] = list;
      keep.geometry.dispose();
      keep.geometry = merged;
      // The parts' bounds no longer describe the whole: cleared here, and
      // set from the merged geometry by `presetSkinnedBounds`. (The types say
      // non-null; three's own default is null.)
      const bounds = keep as unknown as { boundingBox: unknown; boundingSphere: unknown };
      bounds.boundingBox = null;
      bounds.boundingSphere = null;
      for (const mesh of rest) {
        parent.remove(mesh);
        mesh.geometry.dispose();
        removed += 1;
      }
    }
  }
  return removed;
}

/**
 * How much wider than the bind pose a body's culling sphere is. A death clip
 * lays the body out flat: its head ends up a body-height from its feet, about
 * twice the bind-pose sphere's radius from that sphere's centre.
 */
export const POSE_SLACK = 2.2;

/**
 * Give every skinned mesh under `root` a culling sphere from its bind pose.
 *
 * Left null, three computes a skinned mesh's sphere the first time it is
 * culled by skinning every vertex on the CPU through the skeleton: on Bank's
 * range, about three seconds of one frame the moment the dummies appeared, and
 * the same stall for every player who joins a match. The bind pose, widened by
 * `POSE_SLACK` for the poses the clips reach, is computed once on the template
 * and copied by every clone (`SkinnedMesh.copy`).
 */
export function presetSkinnedBounds(root: THREE.Object3D): number {
  let set = 0;
  root.traverse((obj) => {
    const mesh = obj as THREE.SkinnedMesh;
    if (!mesh.isSkinnedMesh) return;
    const geometry = mesh.geometry;
    if (!geometry.boundingSphere) geometry.computeBoundingSphere();
    if (!geometry.boundingSphere) return;
    const sphere = geometry.boundingSphere.clone();
    sphere.radius *= POSE_SLACK;
    mesh.boundingSphere = sphere;
    set += 1;
  });
  return set;
}

function isIdentity(e: ArrayLike<number>): boolean {
  for (let i = 0; i < 16; i++) {
    if (Math.abs(e[i] - (i % 5 === 0 ? 1 : 0)) > 1e-6) return false;
  }
  return true;
}

export function loadOperatorForTeam(team: number | string): Promise<OperatorAsset> {
  const isT = team === 1 || team === 'T' || team === 'TERRORIST';
  return loadOperator(isT ? OPERATOR_T_URL : OPERATOR_CT_URL);
}

/** Drop the cached asset. Tests only — a match never needs to forget it. */
export function resetOperatorCache(): void {
  cache.clear();
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
      const lit = copy as THREE.MeshStandardMaterial;
      if (lit.emissive) {
        // The readability floor: the uniform's own colour, glowing faintly, so
        // a player is never a black shape in a corner whose lamp a graphics
        // tier does not draw. Same number in the native skin shader.
        lit.emissiveMap = lit.map ?? null;
        lit.emissive.copy(lit.color ?? new three.Color(1, 1, 1)).multiplyScalar(CHARACTER_FLOOR);
      }
      if (tint !== undefined) {
        const standard = copy as THREE.MeshStandardMaterial;
        // A wash, not a repaint: the texture still has to read as a uniform, and
        // a full replace would make both teams the same silhouette in one colour.
        if (standard.color) standard.color.lerp(new three.Color(tint), 0.28);
        if (standard.emissive && standard.color) {
          standard.emissive.copy(standard.color).multiplyScalar(CHARACTER_FLOOR);
        }
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
