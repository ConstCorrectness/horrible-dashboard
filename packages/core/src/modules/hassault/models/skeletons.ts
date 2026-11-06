/**
 * One `Skeleton` per set of bones, not one per skinned mesh.
 *
 * GLTFLoader binds every skinned primitive to a skeleton of its own, even when
 * they all come from the same glTF skin, and `SkeletonUtils.clone` keeps it that
 * way: each clone gets a fresh skeleton per mesh. three updates each skeleton
 * once a frame (a matrix multiply per bone) and re-uploads its bone texture, so
 * the first-person arms paid for ten skeletons over two sets of bones, and each
 * operator for two over one. Measured on Bank: 22 bone-texture uploads a frame
 * where 8 do the same job.
 *
 * Two skeletons are the same skeleton when they list the same bone objects in
 * the same order with equal inverse bind matrices; only then is one swapped for
 * the other, so how anything skins is unchanged.
 */
import type * as THREE from 'three';

function same(a: THREE.Skeleton, b: THREE.Skeleton): boolean {
  if (a === b) return true;
  if (a.bones.length !== b.bones.length) return false;
  for (let i = 0; i < a.bones.length; i++) {
    if (a.bones[i] !== b.bones[i]) return false;
    if (!a.boneInverses[i].equals(b.boneInverses[i])) return false;
  }
  return true;
}

/**
 * Rebind every skinned mesh under `root` to the first skeleton with its bones.
 * Returns `root`, so it wraps a clone: `shareSkeletons(SkeletonUtils.clone(x))`.
 */
export function shareSkeletons<T extends THREE.Object3D>(root: T): T {
  const kept: THREE.Skeleton[] = [];
  root.traverse((o) => {
    const mesh = o as THREE.SkinnedMesh;
    if (!mesh.isSkinnedMesh || !mesh.skeleton) return;
    const match = kept.find((s) => same(s, mesh.skeleton));
    if (!match) {
      kept.push(mesh.skeleton);
    } else if (match !== mesh.skeleton) {
      // Nothing has drawn yet, so this has no bone texture to free; dispose
      // anyway in case a caller shares after a frame.
      mesh.skeleton.dispose();
      mesh.skeleton = match;
    }
  });
  return root;
}
