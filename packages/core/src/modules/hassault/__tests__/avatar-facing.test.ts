/**
 * A remote player's body faces where they are looking.
 *
 * It shipped facing exactly backwards. `avatars.ts` turned the operator with
 * the camera's own `-yaw - π/2`, which is right for something whose forward is
 * -Z — the box rig the operator replaced — and wrong by 180° for a Mixamo
 * character, whose forward is +Z. Head-on in a screenshot nothing looks off; it
 * takes a second player watching you turn to see it.
 *
 * So this pins both halves against the shipped files rather than a synthetic
 * rig: that each operator GLB really faces +Z (toes ahead of the feet, left
 * hand on +X), and that `avatarRotationY` carries model +Z onto the cube
 * heading `(cos yaw, sin yaw)` all the way round the circle.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';
import { describe, expect, it } from 'vitest';

import { avatarRotationY } from '../avatars';

const PUBLIC_DIR = fileURLToPath(new URL('../../../../../../apps/web/public/', import.meta.url));

interface GltfNode {
  name?: string;
  children?: number[];
  matrix?: number[];
  translation?: [number, number, number];
  rotation?: [number, number, number, number];
  scale?: [number, number, number];
}

/** Every named node's bind-pose position in model space. */
function bindPositions(file: string): Map<string, THREE.Vector3> {
  const buf = readFileSync(PUBLIC_DIR + file);
  const length = buf.readUInt32LE(12);
  const gltf = JSON.parse(buf.subarray(20, 20 + length).toString('utf8')) as {
    scene?: number;
    scenes: { nodes: number[] }[];
    nodes: GltfNode[];
  };

  const out = new Map<string, THREE.Vector3>();
  const walk = (index: number, parent: THREE.Matrix4) => {
    const node = gltf.nodes[index];
    const local = node.matrix
      ? new THREE.Matrix4().fromArray(node.matrix)
      : new THREE.Matrix4().compose(
          new THREE.Vector3(...(node.translation ?? [0, 0, 0])),
          new THREE.Quaternion(...(node.rotation ?? [0, 0, 0, 1])),
          new THREE.Vector3(...(node.scale ?? [1, 1, 1])),
        );
    const world = parent.clone().multiply(local);
    if (node.name)
      out.set(
        node.name.replace(/^mixamorig[:_]?/, ''),
        new THREE.Vector3().setFromMatrixPosition(world),
      );
    for (const child of node.children ?? []) walk(child, world);
  };
  for (const root of gltf.scenes[gltf.scene ?? 0].nodes) walk(root, new THREE.Matrix4());
  return out;
}

describe('avatar facing', () => {
  it.each(['hassault-operator.glb', 'hassault-operator-t.glb'])('%s faces model +Z', (file) => {
    const bones = bindPositions(file);
    const get = (name: string) => {
      const bone = bones.get(name);
      if (!bone) throw new Error(`${file}: no ${name} bone`);
      return bone;
    };
    // Toes point the way the character faces; its left is +X when that is +Z.
    expect(get('LeftToeBase').z).toBeGreaterThan(get('LeftFoot').z);
    expect(get('RightToeBase').z).toBeGreaterThan(get('RightFoot').z);
    expect(get('LeftHand').x).toBeGreaterThan(get('RightHand').x);
  });

  it('turns model +Z onto the cube heading at every yaw', () => {
    for (let step = 0; step < 16; step++) {
      const yaw = (step * Math.PI * 2) / 16;
      const facing = new THREE.Vector3(0, 0, 1).applyAxisAngle(
        new THREE.Vector3(0, 1, 0),
        avatarRotationY(yaw),
      );
      // Cube (x, y, height) -> Three (x, height, z).
      expect(facing.x).toBeCloseTo(Math.cos(yaw), 6);
      expect(facing.y).toBeCloseTo(0, 6);
      expect(facing.z).toBeCloseTo(Math.sin(yaw), 6);
    }
  });
});
