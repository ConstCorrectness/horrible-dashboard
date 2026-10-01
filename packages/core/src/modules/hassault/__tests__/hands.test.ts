/**
 * The skinned hands land where they are told to.
 *
 * Built from the **shipped** `hassault-hands.glb`'s node tree rather than a
 * synthetic skeleton: the whole solver is "relative to the rest pose read out of
 * the file", so a test against a rig invented here would only prove the solver
 * agrees with itself. No GLTFLoader — the node transforms are all it needs.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';
import { describe, expect, it } from 'vitest';

import { LOWER_LEN, SHOULDER_R, solveTwoBone, UPPER_LEN } from '../arms';
import { findBones, poseArm, readRest, type Curl, type HandTarget } from '../hands';

const GLB = fileURLToPath(
  new URL('../../../../../../apps/web/public/hassault-hands.glb', import.meta.url),
);

interface Node {
  name?: string;
  children?: number[];
  translation?: number[];
  rotation?: number[];
  scale?: number[];
}

function loadRig(): THREE.Object3D {
  const buf = readFileSync(GLB);
  const length = buf.readUInt32LE(12);
  const gltf = JSON.parse(buf.subarray(20, 20 + length).toString('utf8')) as {
    nodes: Node[];
    skins: { joints: number[] }[];
    scenes: { nodes: number[] }[];
  };
  const joints = new Set(gltf.skins[0].joints);
  const objects = gltf.nodes.map((n, i) => {
    const o = joints.has(i) ? new THREE.Bone() : new THREE.Object3D();
    o.name = n.name ?? '';
    if (n.translation) o.position.fromArray(n.translation);
    if (n.rotation) o.quaternion.fromArray(n.rotation);
    if (n.scale) o.scale.fromArray(n.scale);
    return o;
  });
  gltf.nodes.forEach((n, i) => n.children?.forEach((c) => objects[i].add(objects[c])));
  const root = new THREE.Group();
  for (const i of gltf.scenes[0].nodes) root.add(objects[i]);
  return root;
}

function worldIn(root: THREE.Object3D, o: THREE.Object3D): THREE.Vector3 {
  root.updateMatrixWorld(true);
  return new THREE.Vector3().setFromMatrixPosition(o.matrixWorld);
}

const FIST: Curl = [1, 1, 1, 1, 1];

describe('the hands rig', () => {
  it('matches the two-bone solve the procedural arms use', () => {
    const root = loadRig();
    const rest = readRest(THREE, root, findBones(root));
    expect(rest.upperLen).toBeCloseTo(UPPER_LEN, 4);
    expect(rest.lowerLen).toBeCloseTo(LOWER_LEN, 4);
  });

  it('rests pointing down -Z with the thumb up', () => {
    const root = loadRig();
    const rest = readRest(THREE, root, findBones(root));
    expect(rest.aim.z).toBeLessThan(-0.95);
    expect(rest.up.y).toBeGreaterThan(0.95);
  });

  it.each([
    ['a pistol grip', [0.28, -0.35, -0.55], [0, 0.3, -1], [0, 1, 0.3]],
    ['a handguard', [0.05, -0.3, -1.0], [1, 0.2, 0], [0, 0, -1]],
    ['a knife held up', [0.3, -0.4, -0.6], [0.2, 1, 0.1], [0, 0.3, -1]],
  ] as const)('closes the fist on %s', (_name, grip, aim, up) => {
    const root = loadRig();
    const bones = findBones(root);
    const rest = readRest(THREE, root, bones);
    const target: HandTarget = { grip: [...grip], aim: [...aim], up: [...up], curl: FIST };
    const solve = poseArm(THREE, rest, bones, SHOULDER_R, target, [1, -1, 0], solveTwoBone);
    expect(solve.stretched).toBe(false);

    const g = worldIn(root, bones.get('grip')!);
    expect(g.distanceTo(new THREE.Vector3(...grip))).toBeLessThan(1e-3);

    // The hand points where it was aimed.
    const hand = worldIn(root, bones.get('hand')!);
    const knuckles = worldIn(root, bones.get('middle_1')!);
    const a = new THREE.Vector3(...aim).normalize();
    expect(knuckles.sub(hand).normalize().dot(a)).toBeGreaterThan(0.97);

    // And the shoulder stayed on the shoulder.
    expect(
      worldIn(root, bones.get('upper')!).distanceTo(new THREE.Vector3(...SHOULDER_R)),
    ).toBeLessThan(1e-4);
  });

  it('reaches a grip beyond the arm by bringing the shoulder forward', () => {
    // A view-model rifle's handguard is further from the off shoulder than an
    // arm is long. The fist still has to land on it, and nothing may go NaN.
    const root = loadRig();
    const bones = findBones(root);
    const rest = readRest(THREE, root, bones);
    const far: HandTarget = { grip: [0.6, -0.5, -2.4], aim: [1, 0.2, 0], up: [0, 0, -1], curl: FIST };
    poseArm(THREE, rest, bones, SHOULDER_R, far, [1, -1, 0], solveTwoBone);
    root.updateMatrixWorld(true);
    for (const bone of bones.values()) {
      for (const e of bone.matrixWorld.elements) expect(Number.isFinite(e)).toBe(true);
    }
    const g = worldIn(root, bones.get('grip')!);
    expect(g.distanceTo(new THREE.Vector3(...far.grip))).toBeLessThan(1e-3);
  });

  it('folds the fingers towards the palm', () => {
    const root = loadRig();
    const bones = findBones(root);
    const rest = readRest(THREE, root, bones);
    const tip = (curl: Curl) => {
      poseArm(
        THREE,
        rest,
        bones,
        SHOULDER_R,
        { grip: [0.3, -0.35, -0.6], aim: [0, 0, -1], up: [0, 1, 0], curl },
        [1, -1, 0],
        solveTwoBone,
      );
      return worldIn(root, bones.get('middle_3')!);
    };
    const open = tip([0, 0, 0, 0, 0]);
    const closed = tip(FIST);
    // At rest the palm faces -X: a closing finger moves that way and back.
    expect(closed.x).toBeLessThan(open.x - 0.05);
    expect(closed.z).toBeGreaterThan(open.z + 0.05);
  });
});
