/**
 * The shipped operator's skeleton and clips, read straight out of the GLB.
 *
 * `GLTFLoader` wants a browser for the textures, and the animator tests do not
 * want textures at all: what they measure is where bones end up. So this reads
 * the two parts of the file that decide that — the node tree and the animation
 * channels — and builds the same `OperatorAsset` the loader would, without a
 * mesh. The real file rather than a synthetic rig, because every number worth
 * pinning here (how long a leg is, which way a hand holds a rifle) is a
 * property of that file.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';
import { clone as skeletonClone } from 'three/examples/jsm/utils/SkeletonUtils.js';

import { OPERATOR_CLIPS, type OperatorClip } from '../models/clips';
import type { OperatorAsset } from '../models/operator';

const PUBLIC_DIR = fileURLToPath(new URL('../../../../../../apps/web/public/', import.meta.url));

interface Gltf {
  scene?: number;
  scenes: { nodes: number[] }[];
  nodes: {
    name?: string;
    children?: number[];
    translation?: [number, number, number];
    rotation?: [number, number, number, number];
    scale?: [number, number, number];
  }[];
  accessors: {
    bufferView: number;
    byteOffset?: number;
    componentType: number;
    count: number;
    type: string;
  }[];
  bufferViews: { byteOffset?: number; byteStride?: number }[];
  animations: {
    name: string;
    channels: { sampler: number; target: { node: number; path: string } }[];
    samplers: { input: number; output: number }[];
  }[];
}

const WIDTH: Record<string, number> = { SCALAR: 1, VEC3: 3, VEC4: 4 };

export function loadOperatorGlb(file: string): OperatorAsset {
  const buf = readFileSync(PUBLIC_DIR + file);
  const jsonLength = buf.readUInt32LE(12);
  const gltf = JSON.parse(buf.subarray(20, 20 + jsonLength).toString('utf8')) as Gltf;
  const bin = buf.subarray(20 + jsonLength + 8);

  const read = (index: number): number[] => {
    const accessor = gltf.accessors[index];
    const view = gltf.bufferViews[accessor.bufferView];
    const width = WIDTH[accessor.type];
    const start = (view.byteOffset ?? 0) + (accessor.byteOffset ?? 0);
    const out: number[] = [];
    // Every animation accessor in both operators is tightly packed float32;
    // anything else is a re-export this reader has not been taught.
    if (accessor.componentType !== 5126 || view.byteStride) {
      throw new Error(`${file}: accessor ${index} is not packed float32`);
    }
    for (let i = 0; i < accessor.count * width; i += 1) out.push(bin.readFloatLE(start + i * 4));
    return out;
  };

  const objects = gltf.nodes.map((node) => {
    const bone = new THREE.Bone();
    bone.name = THREE.PropertyBinding.sanitizeNodeName(node.name ?? '');
    if (node.translation) bone.position.fromArray(node.translation);
    if (node.rotation) bone.quaternion.fromArray(node.rotation);
    if (node.scale) bone.scale.fromArray(node.scale);
    return bone;
  });
  gltf.nodes.forEach((node, index) => {
    for (const child of node.children ?? []) objects[index].add(objects[child]);
  });
  const root = new THREE.Group();
  for (const index of gltf.scenes[gltf.scene ?? 0].nodes) root.add(objects[index]);

  const clips = new Map<OperatorClip, THREE.AnimationClip>();
  for (const animation of gltf.animations) {
    const tracks: THREE.KeyframeTrack[] = [];
    for (const channel of animation.channels) {
      const sampler = animation.samplers[channel.sampler];
      const name = objects[channel.target.node].name;
      const times = read(sampler.input);
      const values = read(sampler.output);
      if (channel.target.path === 'rotation') {
        tracks.push(new THREE.QuaternionKeyframeTrack(`${name}.quaternion`, times, values));
      } else if (channel.target.path === 'translation') {
        tracks.push(new THREE.VectorKeyframeTrack(`${name}.position`, times, values));
      } else if (channel.target.path === 'scale') {
        tracks.push(new THREE.VectorKeyframeTrack(`${name}.scale`, times, values));
      }
    }
    if ((OPERATOR_CLIPS as readonly string[]).includes(animation.name)) {
      clips.set(
        animation.name as OperatorClip,
        new THREE.AnimationClip(animation.name, -1, tracks),
      );
    }
  }

  return { prototype: root, clips, clone: skeletonClone };
}
