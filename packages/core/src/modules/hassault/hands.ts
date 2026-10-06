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
  /**
   * How thick the handle is around the fist's centre: no finger closes past
   * touching it. Zero or absent for "closes on nothing". The thumb settles on the
   * handle's surface instead (`thumbCurl`): it lies along it, not around it.
   */
  radius?: number;
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

/** How far past the handle's surface a fingertip's pad may sit. `hands.rs`'s `FINGERTIP_PAD`. */
const FINGERTIP_PAD = 0.015;
/** The coarse step of the contact search, in curl. */
const CONTACT_STEP = 0.05;
/** A curl no finger passes. */
const MAX_CURL = 1.2;

/**
 * A finger's tip at `curl`, in the hand's rest frame, walked down the chain with
 * the same folds `poseArm` applies. `hands.rs`'s `fingertip`.
 */
export function fingertip(
  three: typeof THREE,
  rest: HandsRest,
  finger: string,
  curl: number,
): THREE.Vector3 {
  const ratios = finger === 'thumb' ? JOINT_FLEX.thumb : JOINT_FLEX.finger;
  const w = (n: string) => rest.world.get(n)!;
  let rot = w('hand').quat.clone();
  let pos = w('hand').pos.clone();
  let parent = 'hand';
  for (const j of [1, 2, 3]) {
    const name = `${finger}_${j}`;
    const local = w(name).pos.clone().sub(w(parent).pos).applyQuaternion(w(parent).quat.clone().invert());
    pos.add(local.applyQuaternion(rot));
    const fold = new three.Quaternion().setFromAxisAngle(rest.flexAxis.get(name)!, curl * ratios[j - 1]);
    rot = rot.clone().multiply(rest.local.get(name)!).multiply(fold).normalize();
    parent = name;
  }
  // One more distal segment on: the last bone's own length, along the line the
  // previous bone ran into it.
  const last = w(`${finger}_3`);
  const span = last.pos.clone().sub(w(`${finger}_2`).pos).applyQuaternion(last.quat.clone().invert());
  return pos.add(span.applyQuaternion(rot));
}

/**
 * How far a finger can close before its tip meets a handle `radius` thick around
 * the fist's centre: the fist's hole is the `up` axis through the `grip` bone, so
 * a handle is a cylinder on it. `MAX_CURL` when it never gets there, or when
 * `radius` is zero. `hands.rs`'s `contact_curl`.
 */
export function contactCurl(
  three: typeof THREE,
  rest: HandsRest,
  finger: string,
  radius: number,
): number {
  if (!(radius > 0)) return MAX_CURL;
  const target = radius + FINGERTIP_PAD;
  const centre = rest.world.get('grip')!.pos;
  const distance = (curl: number) => {
    const d = fingertip(three, rest, finger, curl).sub(centre);
    return d.sub(rest.up.clone().multiplyScalar(d.dot(rest.up))).length();
  };
  let last = distance(0);
  if (last <= target) return 0;
  let curl = 0;
  while (curl < MAX_CURL) {
    const next = Math.min(curl + CONTACT_STEP, MAX_CURL);
    const here = distance(next);
    if (here <= target) {
      // Linear between the two samples: the tip's path is smooth.
      const t = (last - target) / Math.max(last - here, 1e-6);
      return curl + (next - curl) * Math.min(1, Math.max(0, t));
    }
    last = here;
    curl = next;
  }
  return MAX_CURL;
}

/**
 * How closed the thumb ends up for a handle `radius` thick, given the curl it was
 * asked for: that curl if its tip is clear of the handle, otherwise the nearest
 * curl that is. A thumb asked to close all the way (`MAX_CURL`) so comes to rest
 * touching the handle, and is never inside it. Unchanged for a zero radius.
 * `hands.rs`'s `thumb_curl`.
 */
export function thumbCurl(
  three: typeof THREE,
  rest: HandsRest,
  radius: number,
  requested: number,
): number {
  if (!(radius > 0)) return requested;
  const target = radius + FINGERTIP_PAD;
  const centre = rest.world.get('grip')!.pos;
  const distance = (curl: number) => {
    const d = fingertip(three, rest, 'thumb', curl).sub(centre);
    return d.sub(rest.up.clone().multiplyScalar(d.dot(rest.up))).length();
  };
  const clear = (curl: number) => distance(curl) >= target;
  if (clear(requested)) return requested;
  for (let step = CONTACT_STEP; step <= MAX_CURL; step += CONTACT_STEP) {
    // Nearest first, and the way it was asked to go first: a thumb asked to close
    // further than it can backs off rather than springing open.
    for (const candidate of [requested - step, requested + step]) {
      if (candidate >= 0 && candidate <= MAX_CURL && clear(candidate)) return candidate;
    }
  }
  // A handle thicker than any pose clears: the furthest the thumb gets.
  let best = requested;
  for (let curl = 0; curl <= MAX_CURL; curl += CONTACT_STEP) {
    if (distance(curl) > distance(best)) best = curl;
  }
  return best;
}

/** How far a wrist bends before the hand has to follow the forearm: ~40 degrees. */
export const MAX_WRIST_BEND = 0.7;
/** Passes of the wrist/forearm fixed point; three settle it to well under a degree. */
const WRIST_PASSES = 3;

/**
 * `aim` turned toward `forearm` until it is no more than `MAX_WRIST_BEND` from
 * it. Unchanged when it already is. `hands.rs`'s `limit_wrist_aim`.
 */
export function limitWristAim(
  three: typeof THREE,
  forearm: THREE.Vector3,
  aim: THREE.Vector3,
): THREE.Vector3 {
  const angle = forearm.angleTo(aim);
  if (angle <= MAX_WRIST_BEND) return aim.clone();
  let axis = forearm.clone().cross(aim);
  if (axis.lengthSq() < 1e-8) axis = perpendicular(three, new three.Vector3(0, 1, 0), forearm);
  else axis.normalize();
  return forearm.clone().applyAxisAngle(axis, MAX_WRIST_BEND).normalize();
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
  let aim = V(target.aim).normalize();
  if (aim.lengthSq() < 1e-8) aim.copy(rest.aim);
  const wantedAim = aim.clone();
  let up = perpendicular(three, V(target.up), aim);
  let handDelta = frameRotation(three, rest.aim, rest.up, aim, up);

  // So the wrist is wherever puts the fist's centre on the grip. That moves the
  // forearm, and a forearm that has to meet the hand at an angle no wrist makes
  // is the kinked look: so the hand turns toward the forearm's line until the
  // bend is one a wrist can make, and the fist stays on the grip. The wrist and
  // the forearm depend on each other, so a few passes settle it.
  let wrist = V(target.grip).sub(rest.gripOffset.clone().applyQuaternion(handDelta));
  let s = V(
    slideShoulder(shoulder, [wrist.x, wrist.y, wrist.z], (rest.upperLen + rest.lowerLen) * 0.985),
  );
  let solved = solveTwoBone(
    [s.x, s.y, s.z],
    [wrist.x, wrist.y, wrist.z],
    rest.upperLen,
    rest.lowerLen,
    pole,
  );
  for (let pass = 0; pass < WRIST_PASSES; pass++) {
    const forearm = wrist.clone().sub(V(solved.elbow)).normalize();
    const limited = limitWristAim(three, forearm, wantedAim);
    if (limited.angleTo(aim) < 1e-4) break;
    aim = limited;
    up = perpendicular(three, V(target.up), aim);
    handDelta = frameRotation(three, rest.aim, rest.up, aim, up);
    wrist = V(target.grip).sub(rest.gripOffset.clone().applyQuaternion(handDelta));
    s = V(
      slideShoulder(shoulder, [wrist.x, wrist.y, wrist.z], (rest.upperLen + rest.lowerLen) * 0.985),
    );
    solved = solveTwoBone(
      [s.x, s.y, s.z],
      [wrist.x, wrist.y, wrist.z],
      rest.upperLen,
      rest.lowerLen,
      pole,
    );
  }
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
    let amount = Math.max(0, Math.min(MAX_CURL, target.curl[i] ?? 0));
    const radius = target.radius ?? 0;
    amount =
      f === 'thumb'
        ? thumbCurl(three, rest, radius, amount)
        : Math.min(amount, contactCurl(three, rest, f, radius));
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
