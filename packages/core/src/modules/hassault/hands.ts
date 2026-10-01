/**
 * The skinned hands: posing `hassault-hands.glb` onto a weapon.
 *
 * `arms.ts` used to draw each arm as two cylinders and a box, and an inspect —
 * which turns the weapon, and the hands with it, towards the camera — made that
 * impossible to miss. The mesh is now a real arm with a gloved, five-fingered
 * hand, built by `tools/blender/generate_arms.py`; this file is the half that
 * decides where its bones go.
 *
 * ## The fist goes on the grip, and the arm is worked back from it
 *
 * A weapon's grip anchor (`models/grips.json`) is now **the centre of the closed
 * fist** — the rig's non-deforming `grip` bone — together with the direction the
 * hand points (`aim`, wrist to knuckles) and the direction of the hole through
 * the fist (`up`, the thumb side). From those the hand's rotation is fixed, the
 * wrist falls out as "the grip, minus where the grip sits in the hand", and the
 * two-bone solve reaches the wrist from the shoulder. A hand placed any other way
 * closes around the air beside the handle.
 *
 * ## Relative to the rest pose, never to hardcoded axes
 *
 * Every bone is rotated by *the rotation that takes its rest direction to the
 * posed one*, applied on top of its rest orientation. So nothing here depends on
 * how Blender rolled a bone or how the exporter converted its axes — only on the
 * rest pose itself, which the generator documents and which is read out of the
 * file. The two exceptions are the flex axes below, which say which way a finger
 * folds, and which the generator carries as the same two vectors.
 *
 * ## One right arm, mirrored
 *
 * The left arm is the same rig under a parent scaled by `x = -1`. It is solved as
 * a right arm in that mirrored space — the caller mirrors the targets on the way
 * in — so both hands share one solver and one set of finger poses.
 *
 * Takes `three` as a parameter, like the rest of the module, so the lazy load
 * stays in one place and the maths is testable headless.
 */
import type * as THREE from 'three';

import { getCachedAssetUrl } from './models/assetCache';

export type Vec3 = [number, number, number];

/** How far each finger is closed, 0 (straight) to 1 (a fist): thumb first. */
export type Curl = [number, number, number, number, number];

export const HANDS_URL = '/hassault-hands.glb';

export const FINGERS = ['thumb', 'index', 'middle', 'ring', 'pinky'] as const;

/**
 * What a curl rotates a finger about, in the rig's rest space.
 *
 * The fingers fold towards the palm about the thumb axis (+Y at rest); the thumb
 * folds across the palm about an axis of its own. The generator's `FINGER_FLEX`
 * and `THUMB_FLEX`, converted from Blender's axes to glTF's.
 */
export const FINGER_FLEX_AXIS: Vec3 = [0, 1, 0];
export const THUMB_FLEX_AXIS: Vec3 = normalize([-0.7, 0, 0.57]);

/**
 * How a curl of 1 is shared along a finger's three joints, in radians.
 *
 * A fist is not three equal bends: the middle joint folds furthest and the tip
 * least. The thumb's are small because most of its travel is across the palm,
 * which is the axis rather than the angle.
 */
export const JOINT_FLEX = {
  finger: [1.35, 1.6, 1.0] as const,
  thumb: [0.35, 0.6, 0.8] as const,
};

export const ARM_BONES = ['upper', 'fore', 'hand'] as const;

/** Everything a hand needs from the weapon this frame, in the arm's own space. */
export interface HandTarget {
  /** Where the centre of the fist goes. */
  grip: Vec3;
  /** Wrist to knuckles. */
  aim: Vec3;
  /** The hole through the fist, thumb side. Made perpendicular to `aim` here. */
  up: Vec3;
  curl: Curl;
}

function normalize(v: Vec3): Vec3 {
  const l = Math.hypot(v[0], v[1], v[2]);
  return l < 1e-9 ? [0, 0, 0] : [v[0] / l, v[1] / l, v[2] / l];
}

/** A rest pose, read once from a loaded rig and shared by every clone of it. */
export interface HandsRest {
  /** Each bone's rest orientation and position in the rig root's space. */
  world: Map<string, { quat: THREE.Quaternion; pos: THREE.Vector3 }>;
  /** Each bone's rest local rotation, which a curl is composed onto. */
  local: Map<string, THREE.Quaternion>;
  /** The flex axis of each finger bone, in that bone's own local frame. */
  flexAxis: Map<string, THREE.Vector3>;
  /** Rest wrist-to-knuckles and fist-hole directions, rig space. */
  aim: THREE.Vector3;
  up: THREE.Vector3;
  /** The fist centre relative to the wrist at rest, rig space. */
  gripOffset: THREE.Vector3;
  upperLen: number;
  lowerLen: number;
  /** `upper`'s parent — the armature node — in rig space. */
  armature: { quat: THREE.Quaternion; pos: THREE.Vector3 };
}

/** The bones of one rig instance, by name. */
export type BoneMap = Map<string, THREE.Object3D>;

export function findBones(root: THREE.Object3D): BoneMap {
  const bones: BoneMap = new Map();
  root.traverse((o) => {
    if (
      (o as THREE.Bone).isBone ||
      /^(upper|fore|hand|grip|(thumb|index|middle|ring|pinky)_\d)$/.test(o.name)
    ) {
      bones.set(o.name, o);
    }
  });
  return bones;
}

/**
 * Read the rest pose off a freshly loaded (unposed) rig.
 *
 * Throws on a rig missing a bone this solver moves, rather than posing half a
 * hand: the caller keeps the procedural arms in that case, and says so.
 */
export function readRest(three: typeof THREE, root: THREE.Object3D, bones: BoneMap): HandsRest {
  for (const name of [
    ...ARM_BONES,
    'grip',
    ...FINGERS.flatMap((f) => [1, 2, 3].map((i) => `${f}_${i}`)),
  ]) {
    if (!bones.has(name)) throw new Error(`hands rig has no bone "${name}"`);
  }
  root.updateMatrixWorld(true);
  const rootInv = root.matrixWorld.clone().invert();
  const world = new Map<string, { quat: THREE.Quaternion; pos: THREE.Vector3 }>();
  const local = new Map<string, THREE.Quaternion>();
  for (const [name, bone] of bones) {
    const m = bone.matrixWorld.clone().premultiply(rootInv);
    const pos = new three.Vector3();
    const quat = new three.Quaternion();
    const scale = new three.Vector3();
    m.decompose(pos, quat, scale);
    world.set(name, { quat, pos });
    local.set(name, bone.quaternion.clone());
  }
  const armature = { quat: new three.Quaternion(), pos: new three.Vector3() };
  const upperParent = bones.get('upper')!.parent;
  if (upperParent) {
    upperParent.matrixWorld
      .clone()
      .premultiply(rootInv)
      .decompose(armature.pos, armature.quat, new three.Vector3());
  }
  const flexAxis = new Map<string, THREE.Vector3>();
  const fingerAxis = new three.Vector3(...FINGER_FLEX_AXIS);
  const thumbAxis = new three.Vector3(...THUMB_FLEX_AXIS);
  for (const f of FINGERS) {
    for (const i of [1, 2, 3]) {
      const name = `${f}_${i}`;
      const q = world.get(name)!.quat.clone().invert();
      flexAxis.set(
        name,
        (f === 'thumb' ? thumbAxis : fingerAxis).clone().applyQuaternion(q).normalize(),
      );
    }
  }
  const w = (n: string) => world.get(n)!.pos;
  const aim = w('index_1').clone().add(w('middle_1')).add(w('ring_1')).add(w('pinky_1'));
  aim.multiplyScalar(0.25).sub(w('hand')).normalize();
  // The grip bone points along the fist's hole: its local +Y, as every bone's.
  const up = new three.Vector3(0, 1, 0).applyQuaternion(world.get('grip')!.quat);
  up.sub(aim.clone().multiplyScalar(up.dot(aim))).normalize();
  return {
    world,
    local,
    flexAxis,
    aim,
    up,
    gripOffset: w('grip').clone().sub(w('hand')),
    upperLen: w('fore').distanceTo(w('upper')),
    lowerLen: w('hand').distanceTo(w('fore')),
    armature,
  };
}

/** The rotation taking frame (a0, u0) onto frame (a1, u1). Both pairs orthonormal. */
function frameRotation(
  three: typeof THREE,
  a0: THREE.Vector3,
  u0: THREE.Vector3,
  a1: THREE.Vector3,
  u1: THREE.Vector3,
): THREE.Quaternion {
  const basis = (a: THREE.Vector3, u: THREE.Vector3) =>
    new three.Matrix4().makeBasis(a, u, a.clone().cross(u));
  const m = basis(a1, u1).multiply(basis(a0, u0).transpose());
  return new three.Quaternion().setFromRotationMatrix(m);
}

/** `v` with its component along `axis` removed, or a fallback if nothing is left. */
function perpendicular(three: typeof THREE, v: THREE.Vector3, axis: THREE.Vector3): THREE.Vector3 {
  const out = v.clone().sub(axis.clone().multiplyScalar(v.dot(axis)));
  if (out.lengthSq() < 1e-8) {
    const any = Math.abs(axis.y) < 0.9 ? new three.Vector3(0, 1, 0) : new three.Vector3(1, 0, 0);
    return any.sub(axis.clone().multiplyScalar(any.dot(axis))).normalize();
  }
  return out.normalize();
}

/**
 * Where the shoulder has to be for the arm to reach `wrist`.
 *
 * A view-model weapon is held further forward than an arm reaches — a rifle's
 * handguard is two cubes from the off shoulder. Rather than leave the hand short
 * of the grip, the shoulder comes forward to meet it: **straight forward**, so it
 * stays low and to the side, below the frame. Slid along the line to the hand it
 * rose into view, and the sleeve filled the bottom of the screen. Only when the
 * hand is too far sideways for any forward slide does it fall back to the line.
 */
export function slideShoulder(shoulder: Vec3, wrist: Vec3, reach: number): Vec3 {
  const d: Vec3 = [wrist[0] - shoulder[0], wrist[1] - shoulder[1], wrist[2] - shoulder[2]];
  const length = Math.hypot(d[0], d[1], d[2]);
  if (length <= reach) return shoulder;
  const lateral = reach * reach - d[0] * d[0] - d[1] * d[1];
  if (lateral > 0) {
    // |(dx, dy, dz + k)| = reach, taking the root that keeps the hand ahead.
    const k = -d[2] - Math.sqrt(lateral);
    if (k >= 0) return [shoulder[0], shoulder[1], shoulder[2] - k];
  }
  const f = (length - reach) / length;
  return [shoulder[0] + d[0] * f, shoulder[1] + d[1] * f, shoulder[2] + d[2] * f];
}

export interface ArmSolve {
  wrist: THREE.Vector3;
  elbow: THREE.Vector3;
  stretched: boolean;
}

/**
 * Pose one arm: the fist onto `target`, the shoulder at `shoulder`.
 *
 * Everything is in the rig root's space (camera space for the right arm, the
 * mirrored camera space for the left). Writes each moved bone's local transform
 * and returns where the joints ended up, for the tests.
 */
export function poseArm(
  three: typeof THREE,
  rest: HandsRest,
  bones: BoneMap,
  shoulder: Vec3,
  target: HandTarget,
  pole: Vec3,
  solveTwoBone: (
    root: Vec3,
    target: Vec3,
    upper: number,
    lower: number,
    pole: Vec3,
  ) => { elbow: Vec3; stretched: boolean },
): ArmSolve {
  const V = (v: Vec3) => new three.Vector3(v[0], v[1], v[2]);

  // The hand's rotation comes from the weapon, before anything else is known.
  const aim = V(target.aim).normalize();
  if (aim.lengthSq() < 1e-8) aim.copy(rest.aim);
  const up = perpendicular(three, V(target.up), aim);
  const handDelta = frameRotation(three, rest.aim, rest.up, aim, up);

  // So the wrist is wherever puts the fist's centre on the grip.
  const wrist = V(target.grip).sub(rest.gripOffset.clone().applyQuaternion(handDelta));
  const s = V(slideShoulder(shoulder, [wrist.x, wrist.y, wrist.z], (rest.upperLen + rest.lowerLen) * 0.985));
  const solved = solveTwoBone(
    [s.x, s.y, s.z],
    [wrist.x, wrist.y, wrist.z],
    rest.upperLen,
    rest.lowerLen,
    pole,
  );
  const elbow = V(solved.elbow);

  // Each segment aimed along its span and rolled to keep the hand's up — so
  // the forearm turns with the wrist rather than wringing it, and the upper arm,
  // which is almost entirely off-screen, just follows.
  const restDir = (from: string, to: string) =>
    rest.world.get(to)!.pos.clone().sub(rest.world.get(from)!.pos).normalize();
  const segment = (from: string, to: string, a: THREE.Vector3, b: THREE.Vector3) => {
    const dir0 = restDir(from, to);
    const up0 = perpendicular(three, rest.up, dir0);
    const dir1 = b.clone().sub(a).normalize();
    const up1 = perpendicular(three, up, dir1);
    return frameRotation(three, dir0, up0, dir1, up1).multiply(rest.world.get(from)!.quat.clone());
  };
  const upperWorld = segment('upper', 'fore', s, elbow);
  // Where the forearm actually ends: the wrist, or short of it when the target
  // was out of reach and the arm is straight.
  const wristReached = elbow
    .clone()
    .add(wrist.clone().sub(elbow).normalize().multiplyScalar(rest.lowerLen));
  const foreWorld = segment('fore', 'hand', elbow, wristReached);
  const handWorld = handDelta.clone().multiply(rest.world.get('hand')!.quat.clone());

  // World (rig space) to local: each parent's posed world rotation is known,
  // because the chain is walked from the shoulder down.
  // `upper`'s parent is the armature node, which never moves, so its rest
  // transform is its posed one.
  const upper = bones.get('upper')!;
  const parentInv = rest.armature.quat.clone().invert();
  upper.quaternion.copy(parentInv.clone().multiply(upperWorld));
  upper.position.copy(s.clone().sub(rest.armature.pos).applyQuaternion(parentInv));
  bones.get('fore')!.quaternion.copy(upperWorld.clone().invert().multiply(foreWorld));
  bones.get('hand')!.quaternion.copy(foreWorld.clone().invert().multiply(handWorld));

  // The fingers are children of the hand, so a curl is local and needs nothing
  // from above: the rest rotation, then a fold about the finger's own axis.
  FINGERS.forEach((f, i) => {
    const amount = Math.max(0, Math.min(1.2, target.curl[i] ?? 0));
    const ratios = f === 'thumb' ? JOINT_FLEX.thumb : JOINT_FLEX.finger;
    for (const j of [1, 2, 3]) {
      const name = `${f}_${j}`;
      const fold = new three.Quaternion().setFromAxisAngle(
        rest.flexAxis.get(name)!,
        amount * ratios[j - 1],
      );
      bones.get(name)!.quaternion.copy(rest.local.get(name)!).multiply(fold);
    }
  });

  return { wrist: wristReached, elbow, stretched: solved.stretched };
}

export interface HandsModel {
  readonly prototype: THREE.Object3D;
  readonly clone: (source: THREE.Object3D) => THREE.Object3D;
}

let pending: Promise<HandsModel> | null = null;

/**
 * Fetch the hands once per page; the same contract as `loadWeaponModel` — the
 * promise is cached, a failure drops it so a later attempt retries.
 */
export function loadHandsModel(): Promise<HandsModel> {
  if (pending) return pending;
  const task = (async (): Promise<HandsModel> => {
    const [{ GLTFLoader }, SkeletonUtils] = await Promise.all([
      import('three/examples/jsm/loaders/GLTFLoader.js'),
      import('three/examples/jsm/utils/SkeletonUtils.js'),
    ]);
    const url = await getCachedAssetUrl(HANDS_URL);
    const gltf = await new GLTFLoader().loadAsync(url);
    return { prototype: gltf.scene, clone: SkeletonUtils.clone };
  })();
  pending = task;
  task.catch(() => {
    pending = null;
  });
  return task;
}
