/**
 * The hands holding the gun.
 *
 * There were none. The view model was a weapon floating in front of the camera —
 * which reads as a weapon floating in front of the camera, and no amount of bob
 * or sway fixes that, because the thing missing is the body it is attached to.
 *
 * ## Procedural, and solved onto the weapon
 *
 * Two arms, each an upper arm, a forearm and a gloved hand built from
 * primitives — the module's licensing rule (nothing bundled, nothing borrowed)
 * and the same construction the weapons themselves use.
 *
 * The **shoulders are fixed in camera space** and each hand is solved onto a grip
 * anchor **on the weapon**, by two-bone analytic IK. That one decision is most of
 * what makes this work: the anchors are points in the weapon's own space, so
 * they inherit the view model's entire transform for free. Every bob, every
 * sway, the recoil kick, the reload dip, the stow, the inspect roll — the arms
 * follow all of it without knowing any of it exists, and there is no second
 * animation to keep in step with the first.
 *
 * ## The solve must clamp, never NaN
 *
 * An unreachable target puts `acos` outside `[-1, 1]`, which yields `NaN`, which
 * yields a `NaN` quaternion, a `NaN` matrix, and a mesh three silently declines
 * to draw. An arm that vanishes with no error is exactly the failure this file
 * has to not have, so both bounds are clamped explicitly rather than assumed
 * unreachable.
 *
 * Takes `three` as a parameter rather than importing it, like `viewmodel.ts` and
 * `effects.ts`, so the lazy-load stays in one place. `solveTwoBone` itself is
 * pure and takes no three at all, so the arithmetic is testable headless.
 */
import type * as THREE from 'three';

import grips from './models/grips.json';

/** A point in the weapon's own model space. */
export type Vec3 = [number, number, number];

/**
 * Where the shoulders sit, in **camera** space.
 *
 * Below and behind the eye, because that is where shoulders are. The numbers are
 * in cube units: the eye is 4.5 cubes up and eyes sit about 1.6m off the ground,
 * so a cube is roughly 36cm — these put the shoulders about 25cm below the eye
 * and 20cm apart either side of it, which is a person.
 */
export const SHOULDER_R: Vec3 = [0.42, -0.62, 0.28];
export const SHOULDER_L: Vec3 = [-0.42, -0.62, 0.28];

/** Segment lengths, in cube units. About 30cm and 27cm — an arm. */
export const UPPER_LEN = 0.84;
export const LOWER_LEN = 0.76;

/** Sleeve, skin and glove. Muted, so the gun stays the thing you look at. */
export const ARM_PALETTE = {
  sleeve: 0x3d4a3a,
  skin: 0xb98a68,
  glove: 0x2a2d33,
};

/** Limb thickness, in cube units. */
const UPPER_RADIUS = 0.15;
const LOWER_RADIUS: number = 0.12;
const HAND_SIZE: Vec3 = [0.2, 0.24, 0.26];

export interface GripAnchors {
  /** The trigger hand. Always present — every weapon is held by something. */
  primary: Vec3;
  /**
   * The off hand, or `null` for a weapon held in one.
   *
   * `null` rather than an off-screen coordinate: an empty hand is a real state,
   * and a hand parked somewhere arbitrary is a hand the player will eventually
   * see.
   */
  support: Vec3 | null;
  primaryRoll: number;
  supportRoll: number;
}

interface GripFile {
  defaults: { primary: Vec3; support: Vec3; primaryRoll: number; supportRoll: number };
  weapons: Record<string, Partial<GripAnchors> | undefined>;
}

const GRIPS = grips as unknown as GripFile;

/**
 * Where this weapon's hands go.
 *
 * Read from `models/grips.json`, which the native client compiles in with
 * `include_str!` and `browser_parity.rs` pins the two against. Deliberately not
 * served: an anchor is a fact about the *model*, not about the weapon's balance,
 * so putting it in `weapons.py` would give the simulation's table a rendering
 * column and hand it the `response_model` failure mode for nothing.
 *
 * A weapon with no entry gets the defaults scaled to its own fitted bounding
 * box, so **a weapon added later has hands** rather than being empty-handed
 * until somebody notices — the `fitWeaponModel` spirit: measure the general
 * case, and list only the exceptions.
 */
export function gripsFor(
  weaponId: string,
  extent?: { length: number; height: number },
): GripAnchors {
  const listed = GRIPS.weapons[weaponId];
  const d = GRIPS.defaults;
  // Scaled to the weapon's own size where one is known: the defaults are
  // fractions in the spirit of `fitWeaponModel`, so a sniper's support hand
  // reaches further down its barrel than a pistol's does.
  const length = extent?.length ?? 1;
  const height = extent?.height ?? 1;
  const fallback: GripAnchors = {
    primary: [d.primary[0], d.primary[1] * height, d.primary[2] * length],
    support: [d.support[0], d.support[1] * height, d.support[2] * length],
    primaryRoll: d.primaryRoll,
    supportRoll: d.supportRoll,
  };
  if (!listed) return fallback;
  return {
    primary: listed.primary ?? fallback.primary,
    // `undefined` means "not listed, use the default"; an explicit `null` means
    // "this weapon has no off hand". Collapsing the two would give the knife a
    // second hand gripping thin air.
    support: listed.support === undefined ? fallback.support : listed.support,
    primaryRoll: listed.primaryRoll ?? fallback.primaryRoll,
    supportRoll: listed.supportRoll ?? fallback.supportRoll,
  };
}

export interface BoneSolve {
  /** Where the elbow ended up. */
  elbow: Vec3;
  /** Whether the target was out of reach, so the arm is straight rather than bent. */
  stretched: boolean;
}

function sub(a: Vec3, b: Vec3): Vec3 {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}
function add(a: Vec3, b: Vec3): Vec3 {
  return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
}
function scale(a: Vec3, k: number): Vec3 {
  return [a[0] * k, a[1] * k, a[2] * k];
}
function length(a: Vec3): number {
  return Math.hypot(a[0], a[1], a[2]);
}
function normalize(a: Vec3): Vec3 {
  const l = length(a);
  return l < 1e-6 ? [1, 0, 0] : [a[0] / l, a[1] / l, a[2] / l];
}
function dot(a: Vec3, b: Vec3): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
function cross(a: Vec3, b: Vec3): Vec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

/**
 * Put the elbow somewhere plausible between a shoulder and a hand.
 *
 * The law of cosines: with both segment lengths known and the shoulder-to-hand
 * distance measured, the elbow's distance along that line and its offset from it
 * both fall straight out. `pole` picks *which* of the circle of valid elbows —
 * a real arm bends outward and down, not through the chest.
 *
 * **Both bounds are clamped.** Out of reach (`d > upper + lower`) the arm goes
 * straight; folded past its own reach (`d < |upper - lower|`) it stops folding.
 * Unclamped, `acos` of a value outside `[-1, 1]` is `NaN`, and a `NaN` matrix is
 * a mesh three silently declines to draw — an arm that vanishes with no error
 * anywhere.
 */
export function solveTwoBone(
  root: Vec3,
  target: Vec3,
  upper: number,
  lower: number,
  pole: Vec3,
): BoneSolve {
  const toTarget = sub(target, root);
  const d = length(toTarget);
  if (d < 1e-6) {
    // Degenerate: the hand is at the shoulder. Fold the arm rather than dividing
    // by nothing.
    return { elbow: add(root, scale(normalize(pole), upper)), stretched: false };
  }
  const direction = scale(toTarget, 1 / d);
  const reach = upper + lower;
  const stretched = d >= reach;
  if (stretched) {
    return { elbow: add(root, scale(direction, upper)), stretched: true };
  }
  // How far along the shoulder→hand line the elbow sits, and how far off it.
  const along = (d * d + upper * upper - lower * lower) / (2 * d);
  // Clamped at zero: a folded-past-reach arm has an imaginary offset, and
  // `Math.sqrt` of a negative is `NaN`.
  const off = Math.sqrt(Math.max(0, upper * upper - along * along));
  // The pole, made perpendicular to the arm — Gram-Schmidt. A pole parallel to
  // the arm gives no direction at all, so it falls back to any perpendicular.
  let side = sub(pole, scale(direction, dot(pole, direction)));
  if (length(side) < 1e-5) {
    const axis: Vec3 = Math.abs(direction[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0];
    side = cross(direction, axis);
  }
  side = normalize(side);
  return {
    elbow: add(add(root, scale(direction, along)), scale(side, off)),
    stretched: false,
  };
}

/** One arm's three meshes, and the group they hang in. */
interface Limb {
  upper: THREE.Mesh;
  lower: THREE.Mesh;
  hand: THREE.Mesh;
}

/**
 * Two arms, drawn in camera space and solved onto the weapon every frame.
 *
 * The meshes are built once and only ever repositioned — a limb rebuilt per
 * frame is three allocations and three disposals sixty times a second, for
 * shapes that never change size.
 */
export class ArmRig {
  private group: THREE.Group;
  private right: Limb;
  private left: Limb;
  private geometries: THREE.BufferGeometry[] = [];
  private materials: THREE.Material[] = [];
  private tmp: THREE.Vector3;
  private tmpB: THREE.Vector3;

  constructor(
    private readonly three: typeof THREE,
    parent: THREE.Object3D,
  ) {
    this.group = new three.Group();
    // Below the view model's own order, so a hand can never draw over the
    // weapon it is holding.
    this.group.renderOrder = 2;
    parent.add(this.group);
    this.tmp = new three.Vector3();
    this.tmpB = new three.Vector3();
    this.right = this.buildLimb();
    this.left = this.buildLimb();
  }

  private material(color: number, shininess: number): THREE.MeshPhongMaterial {
    const m = new this.three.MeshPhongMaterial({ color, shininess, specular: 0x22262c });
    this.materials.push(m);
    return m;
  }

  private buildLimb(): Limb {
    const three = this.three;
    // Cylinders along +y by default; each is oriented by `aim` below rather than
    // rotated at build time, so there is one place the orientation is decided.
    const upperGeo = new three.CylinderGeometry(UPPER_RADIUS, LOWER_RADIUS, 1, 8);
    const lowerGeo = new three.CylinderGeometry(LOWER_RADIUS, LOWER_RADIUS * 0.85, 1, 8);
    const handGeo = new three.BoxGeometry(HAND_SIZE[0], HAND_SIZE[1], HAND_SIZE[2]);
    this.geometries.push(upperGeo, lowerGeo, handGeo);

    const upper = new three.Mesh(upperGeo, this.material(ARM_PALETTE.sleeve, 8));
    const lower = new three.Mesh(lowerGeo, this.material(ARM_PALETTE.sleeve, 8));
    const hand = new three.Mesh(handGeo, this.material(ARM_PALETTE.glove, 14));
    for (const mesh of [upper, lower, hand]) this.group.add(mesh);
    return { upper, lower, hand };
  }

  /**
   * Stretch a segment mesh between two points.
   *
   * The geometry is a unit cylinder along +y, so this is a scale on y and a
   * rotation taking +y onto the segment — `setFromUnitVectors` rather than
   * `lookAt`, whose axis convention differs between cameras and everything else
   * and is the kind of thing that is wrong by exactly 180°.
   */
  private span(mesh: THREE.Mesh, from: Vec3, to: Vec3): void {
    const a = this.tmp.set(from[0], from[1], from[2]);
    const b = this.tmpB.set(to[0], to[1], to[2]);
    const delta = b.clone().sub(a);
    const len = delta.length();
    mesh.position.copy(a).addScaledVector(delta, 0.5);
    mesh.scale.set(1, Math.max(1e-3, len), 1);
    if (len > 1e-5) {
      mesh.quaternion.setFromUnitVectors(new this.three.Vector3(0, 1, 0), delta.divideScalar(len));
    }
  }

  /**
   * Place both arms for this frame.
   *
   * `toCamera` takes a point from the weapon's model space into camera space —
   * the view model's own pivot chain, so the arms inherit every animation it has
   * without knowing about any of them.
   */
  update(anchors: GripAnchors, toCamera: (p: Vec3) => Vec3, visible: boolean): void {
    this.group.visible = visible;
    if (!visible) return;

    this.place(this.right, SHOULDER_R, toCamera(anchors.primary), anchors.primaryRoll, [1, -1, 0]);
    if (anchors.support === null) {
      // A one-handed weapon. The off arm is hidden rather than parked, because a
      // hand gripping thin air is a hand the player will eventually see.
      this.left.upper.visible = false;
      this.left.lower.visible = false;
      this.left.hand.visible = false;
      return;
    }
    this.left.upper.visible = true;
    this.left.lower.visible = true;
    this.left.hand.visible = true;
    this.place(this.left, SHOULDER_L, toCamera(anchors.support), anchors.supportRoll, [-1, -1, 0]);
  }

  private place(limb: Limb, shoulder: Vec3, hand: Vec3, roll: number, pole: Vec3): void {
    const { elbow } = solveTwoBone(shoulder, hand, UPPER_LEN, LOWER_LEN, pole);
    this.span(limb.upper, shoulder, elbow);
    this.span(limb.lower, elbow, hand);
    limb.hand.position.set(hand[0], hand[1], hand[2]);
    // The hand points along the forearm, then rolls about it — a fist on a wrist
    // rather than a box floating at the end of an arm.
    const forearm = normalize(sub(hand, elbow));
    limb.hand.quaternion.setFromUnitVectors(
      new this.three.Vector3(0, 1, 0),
      new this.three.Vector3(forearm[0], forearm[1], forearm[2]),
    );
    limb.hand.rotateY(roll);
  }

  dispose(): void {
    this.group.parent?.remove(this.group);
    for (const geometry of this.geometries) geometry.dispose();
    for (const material of this.materials) material.dispose();
    this.geometries = [];
    this.materials = [];
  }
}
