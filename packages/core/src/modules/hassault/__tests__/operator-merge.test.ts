/**
 * `mergeSkinnedPrimitives`: the T operator loads as 71 skinned primitives over
 * two materials, and draws as two meshes after this. What is pinned is that a
 * merge never changes how anything skins: only meshes that would draw
 * identically are folded together, and the result still binds the same skeleton.
 */
import { describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import { clone as skeletonClone } from 'three/examples/jsm/utils/SkeletonUtils.js';

import { POSE_SLACK, mergeSkinnedPrimitives, presetSkinnedBounds } from '../models/operator';
import { shareSkeletons } from '../models/skeletons';

function skinnedBox(material: THREE.Material, skeleton: THREE.Skeleton): THREE.SkinnedMesh {
  const geometry = new THREE.BoxGeometry(1, 1, 1);
  const n = geometry.attributes.position.count;
  geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(new Array(n * 4).fill(0), 4));
  geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute(new Array(n * 4).fill(0).map((_, i) => (i % 4 === 0 ? 1 : 0)), 4));
  const mesh = new THREE.SkinnedMesh(geometry, material);
  mesh.bind(skeleton);
  return mesh;
}

function rig() {
  const bone = new THREE.Bone();
  bone.name = 'Hips';
  const skeleton = new THREE.Skeleton([bone]);
  const cloth = new THREE.MeshStandardMaterial({ name: 'cloth' });
  const gear = new THREE.MeshStandardMaterial({ name: 'gear' });
  const group = new THREE.Group();
  group.add(bone);
  for (let i = 0; i < 4; i++) group.add(skinnedBox(cloth, skeleton));
  for (let i = 0; i < 3; i++) group.add(skinnedBox(gear, skeleton));
  const moved = skinnedBox(gear, skeleton);
  moved.position.set(0, 1, 0);
  group.add(moved);
  const root = new THREE.Group();
  root.add(group);
  return { root, group, skeleton, cloth, gear };
}

const skinned = (o: THREE.Object3D) => {
  const out: THREE.SkinnedMesh[] = [];
  o.traverse((c) => {
    if ((c as THREE.SkinnedMesh).isSkinnedMesh) out.push(c as THREE.SkinnedMesh);
  });
  return out;
};

describe('mergeSkinnedPrimitives', () => {
  it('folds primitives that share a material and skeleton into one mesh each', () => {
    const { root, cloth, gear } = rig();
    const vertsBefore = skinned(root).reduce((s, m) => s + m.geometry.attributes.position.count, 0);
    const removed = mergeSkinnedPrimitives(root, mergeGeometries);
    const after = skinned(root);
    // 4 cloth -> 1, 3 gear -> 1, and the one with its own transform kept apart.
    expect(removed).toBe(5);
    expect(after).toHaveLength(3);
    expect(after.filter((m) => m.material === cloth)).toHaveLength(1);
    expect(after.filter((m) => m.material === gear)).toHaveLength(2);
    const vertsAfter = after.reduce((s, m) => s + m.geometry.attributes.position.count, 0);
    expect(vertsAfter).toBe(vertsBefore);
  });

  it('keeps the skin attributes and the skeleton binding', () => {
    const { root, skeleton } = rig();
    mergeSkinnedPrimitives(root, mergeGeometries);
    for (const mesh of skinned(root)) {
      expect(mesh.skeleton).toBe(skeleton);
      expect(mesh.geometry.attributes.skinIndex).toBeDefined();
      expect(mesh.geometry.attributes.skinWeight).toBeDefined();
    }
  });

  it('clones of a merged template still get their own skeleton', () => {
    // The reason `SkeletonUtils.clone` is used at all: a merge on the template
    // must not leave clones sharing one pose.
    const { root } = rig();
    mergeSkinnedPrimitives(root, mergeGeometries);
    const a = skinned(skeletonClone(root));
    const b = skinned(skeletonClone(root));
    expect(a).toHaveLength(3);
    expect(a[0].skeleton).not.toBe(b[0].skeleton);
  });

  it('is a no-op when nothing can merge', () => {
    const bone = new THREE.Bone();
    const skeleton = new THREE.Skeleton([bone]);
    const root = new THREE.Group();
    root.add(bone, skinnedBox(new THREE.MeshStandardMaterial(), skeleton));
    root.add(skinnedBox(new THREE.MeshStandardMaterial(), skeleton));
    expect(mergeSkinnedPrimitives(root, mergeGeometries)).toBe(0);
    expect(skinned(root)).toHaveLength(2);
  });
});

describe('shareSkeletons', () => {
  function twoSkins(inverseB?: THREE.Matrix4) {
    const bone = new THREE.Bone();
    const material = new THREE.MeshStandardMaterial();
    const root = new THREE.Group();
    root.add(bone);
    const a = skinnedBox(material, new THREE.Skeleton([bone]));
    const b = skinnedBox(material, new THREE.Skeleton([bone]));
    // After `bind`, which recalculates the inverses from the bones.
    if (inverseB) b.skeleton.boneInverses[0].copy(inverseB);
    root.add(a, b);
    return { root, a, b };
  }

  it('binds meshes over the same bones to one skeleton', () => {
    const { root, a, b } = twoSkins();
    expect(a.skeleton).not.toBe(b.skeleton);
    shareSkeletons(root);
    expect(a.skeleton).toBe(b.skeleton);
  });

  it('keeps skeletons apart when their inverse bind matrices differ', () => {
    // Same bones, different bind: sharing would skin one of them wrong.
    const { root, a, b } = twoSkins(new THREE.Matrix4().makeTranslation(0, 1, 0));
    shareSkeletons(root);
    expect(a.skeleton).not.toBe(b.skeleton);
  });

  it('shares within a clone and never across clones', () => {
    const { root } = twoSkins();
    const one = shareSkeletons(skeletonClone(root));
    const two = shareSkeletons(skeletonClone(root));
    const skeletons = (r: THREE.Object3D) => {
      const out = new Set<THREE.Skeleton>();
      r.traverse((o) => {
        if ((o as THREE.SkinnedMesh).isSkinnedMesh) out.add((o as THREE.SkinnedMesh).skeleton);
      });
      return out;
    };
    const s1 = skeletons(one);
    const s2 = skeletons(two);
    expect(s1.size).toBe(1);
    expect(s2.size).toBe(1);
    expect([...s1][0]).not.toBe([...s2][0]);
  });
});

describe('presetSkinnedBounds', () => {
  it('sets every skinned mesh a widened bind-pose sphere that clones keep', () => {
    const { root } = rig();
    expect(presetSkinnedBounds(root)).toBe(8);
    const meshes: THREE.SkinnedMesh[] = [];
    root.traverse((o) => {
      if ((o as THREE.SkinnedMesh).isSkinnedMesh) meshes.push(o as THREE.SkinnedMesh);
    });
    for (const mesh of meshes) {
      const bind = mesh.geometry.boundingSphere!;
      expect(mesh.boundingSphere!.center.equals(bind.center)).toBe(true);
      expect(mesh.boundingSphere!.radius).toBeCloseTo(bind.radius * POSE_SLACK, 9);
    }
    // A clone copies the sphere, so three never skins its vertices on the CPU
    // to cull it: the computation is the stall this exists to remove.
    const spy = vi.spyOn(THREE.SkinnedMesh.prototype, 'computeBoundingSphere');
    try {
      const clone = skeletonClone(root);
      const frustum = new THREE.Frustum();
      clone.updateMatrixWorld(true);
      clone.traverse((o) => {
        if ((o as THREE.SkinnedMesh).isSkinnedMesh) frustum.intersectsObject(o as THREE.SkinnedMesh);
      });
      expect(spy).not.toHaveBeenCalled();
    } finally {
      spy.mockRestore();
    }
  });
});
