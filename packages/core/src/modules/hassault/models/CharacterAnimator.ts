/**
 * Plays the operator's animation clips against what the server says a player is
 * doing.
 *
 * Replaced a hand-written pose solver — the walk cycle used to be
 * `Math.sin(walkPhase)` fed into thigh rotations, because the rig it drove was
 * rigid boxes with no skin to deform. With a real skeleton the motion comes from
 * the 23 Mixamo clips baked into `hassault-operator.glb`, and this class's job
 * shrinks to three things: choose a clip, crossfade to it, and layer the
 * corrections a clip cannot know about.
 *
 * Those corrections are the legs, the aim and upper-body actions. A clip knows
 * neither how fast its body is crossing the ground nor where its player is
 * looking, so a moving player's legs are solved onto planted feet
 * (`locomotion.ts`) and the torso is turned until the weapon and the head point
 * exactly down the row's yaw and pitch. All of it is applied *after* the mixer
 * runs, because `AnimationMixer` overwrites bone transforms wholesale every
 * update — anything set before it is discarded without warning.
 */

import type * as THREE from 'three';

import type { PlayerRow } from '../net';
import type { CharacterModel } from './CharacterModel';
import { visualSlot } from '../weapon-visuals';
import { solveTwoBone } from '../arms';
import { fadeFor, isOneShot, isUpperBody, selectLocomotion, type OperatorClip } from './clips';
import { createGait, stepGait, type GaitOutput } from './locomotion';
import type { OperatorAsset } from './operator';

/** How long a fire action holds the upper body before locomotion takes it back. */
const FIRE_HOLD = 0.28;
/** A reload is a whole animation; let it run. */
const RELOAD_HOLD = 2.2;

/** How far up the knee's bend is biased, against the pelvis' forward. */
const KNEE_POLE_UP = 0.2;

/** The most mixer time a body out of view is owed, in seconds. */
const SKIP_CAP = 2;

export class CharacterAnimator {
  private readonly mixer: THREE.AnimationMixer;
  private readonly actions = new Map<string, THREE.AnimationAction>();

  private current: OperatorClip | null = null;
  /** The action layered over the base, and how long it has left. */
  private overlay: {
    action: THREE.AnimationAction;
    remaining: number;
    /** Whether the weapon is still pointed while it plays. A reload is not. */
    aimed: boolean;
  } | null = null;

  private readonly gait = createGait();
  private readonly rig: Rig;
  /** The hip joints' height over a planted ankle in the pose last drawn. */
  private hipHeight: number;
  /** Scratch, so a frame allocates nothing. */
  private readonly v: THREE.Vector3[];
  private readonly q: THREE.Quaternion[];
  /** Mixer time owed for the frames this body spent out of view. */
  private skipped = 0;

  constructor(
    private readonly three: typeof THREE,
    private readonly model: CharacterModel,
    private readonly asset: OperatorAsset,
  ) {
    this.mixer = new three.AnimationMixer(model.instance.root);
    this.v = Array.from({ length: 8 }, () => new three.Vector3());
    this.q = Array.from({ length: 5 }, () => new three.Quaternion());
    this.rig = readRig(three, model);
    this.hipHeight = this.rig.legs?.hipHeight ?? 0;
  }

  /**
   * Fetch (and cache) an action for a clip.
   *
   * `upper` returns the clip filtered to the arms, chest and head, which is what
   * makes layering work: two actions driving the same bone are averaged by the
   * mixer rather than one winning, so a fire animation blended over a walk would
   * come out as a half-shrug. Disjoint track sets have nothing to average.
   */
  private action(clip: OperatorClip, upper = false): THREE.AnimationAction | null {
    const key = upper ? `${clip}::upper` : clip;
    const cached = this.actions.get(key);
    if (cached) return cached;

    const source = this.asset.clips.get(clip);
    if (!source) return null;

    const used = upper
      ? new this.three.AnimationClip(
          `${clip}__upper`,
          source.duration,
          source.tracks.filter((t) => isUpperBody(t.name)),
        )
      : source;

    const action = this.mixer.clipAction(used);
    if (isOneShot(clip)) {
      action.setLoop(this.three.LoopOnce, 1);
      action.clampWhenFinished = true;
    }
    this.actions.set(key, action);
    return action;
  }

  /** Crossfade the base layer to a clip, if it is not already playing. */
  private play(clip: OperatorClip): void {
    if (clip === this.current) return;
    const next = this.action(clip);
    if (!next) return;

    const previous = this.current ? this.actions.get(this.current) : null;
    next.reset().setEffectiveWeight(1).play();
    if (previous && previous !== next) {
      next.crossFadeFrom(previous, fadeFor(clip), true);
    }
    this.current = clip;
  }

  /** Play a one-shot over the upper body, leaving the legs to the base layer. */
  private layer(clip: OperatorClip, hold: number, aimed: boolean): void {
    const action = this.action(clip, true);
    if (!action) return;
    action.reset().setEffectiveWeight(1).play();
    this.overlay = { action, remaining: hold, aimed };
  }

  /**
   * One frame. `inView` false still steps the gait and chooses the clip (so the
   * stride and crossfades stay on the row's clock) but skips the mixer and the
   * solve:
   * the pose of a body nobody can see is the bulk of the cost and is never read.
   * The time is owed rather than dropped, and paid on the first frame back in
   * view, so a clip resumes where it would have been.
   */
  update(dt: number, row: PlayerRow, inView = true): void {
    if (!(dt > 0) || Number.isNaN(dt)) dt = 1 / 60;

    this.model.decayMuzzleFlash(dt);
    // A wire index, drawn as the base model it borrows (`weapon-visuals.ts`).
    this.model.setWeapon(visualSlot(row.weapon ?? 2));
    this.model.setOpacity(row.stale ? 0.35 : 1);

    const alive = row.alive !== false;
    const ground = row.ground !== false;
    const gait = stepGait(
      this.gait,
      {
        x: row.x,
        y: row.y,
        yaw: row.yaw,
        alive,
        grounded: ground,
        hipHeight: this.hipHeight,
        legLength: this.rig.legs?.legLength ?? 0,
        hipWidth: this.rig.legs?.hipWidth ?? 0,
      },
      dt,
    );
    this.play(selectLocomotion({ alive, ground, crouch: row.crouch ?? 0, speed: gait.speed }));

    if (this.overlay) {
      this.overlay.remaining -= dt;
      if (this.overlay.remaining <= 0) {
        this.overlay.action.fadeOut(0.15);
        this.overlay = null;
      }
    }

    if (!inView) {
      // Capped: past a couple of seconds every crossfade has finished and a
      // looping clip's phase is arbitrary anyway.
      this.skipped = Math.min(this.skipped + dt, SKIP_CAP);
      this.model.nameplate.visible = row.alive !== false;
      return;
    }
    this.mixer.update(dt + this.skipped);
    this.skipped = 0;

    // After the mixer, never before — see the class comment.
    if (alive) this.solve(row, gait);
    this.model.nameplate.visible = row.alive !== false;
  }

  /** Give a bone a world rotation, and carry it down to everything under it. */
  private turn(bone: THREE.Object3D, world: THREE.Quaternion): void {
    const parent = this.q[4];
    if (bone.parent) bone.parent.getWorldQuaternion(parent).invert();
    else parent.identity();
    bone.quaternion.copy(parent.multiply(world));
    bone.updateMatrixWorld(true);
  }

  /**
   * Rotate a chain so that `from` lands on `to`, each bone taking an equal part.
   *
   * Every part is the same rotation about the same axis, so they compose to the
   * whole of it exactly: whatever hangs off the last bone ends up pointing along
   * `to` with no iteration and no residual, however the clip left it.
   */
  private swing(chain: readonly THREE.Object3D[], from: THREE.Vector3, to: THREE.Vector3): void {
    if (chain.length === 0 || from.lengthSq() < 1e-10) return;
    const whole = this.q[1].setFromUnitVectors(from.normalize(), to);
    const part = this.q[0].identity().slerp(whole, 1 / chain.length);
    for (const bone of chain) {
      const world = bone.getWorldQuaternion(this.q[2]);
      this.turn(bone, this.q[3].multiplyQuaternions(part, world));
    }
  }

  /** Everything laid over the clips for a living body. */
  private solve(row: PlayerRow, gait: GaitOutput): void {
    this.model.rootGroup.updateMatrixWorld(true);
    const legs = this.rig.legs;
    if (legs) {
      // Read before the hips are moved, so the gait plans next frame's stride
      // against the pose the clip gave rather than against its own correction.
      const [left, right] = legs.chains;
      const hipY =
        (left.up.getWorldPosition(this.v[0]).y + right.up.getWorldPosition(this.v[1]).y) * 0.5;
      this.hipHeight = hipY - row.z - legs.ankleHeight;
      if (gait.weight > 0) this.solveLegs(row, gait, legs);
    }
    this.solveAim(row);
  }

  /**
   * Put the pelvis and both legs where the gait says.
   *
   * In world space throughout, which is what a planted foot is stated in: the
   * foot's target does not move when the body does, and solving in the body's
   * own frame would mean re-deriving that every frame.
   */
  private solveLegs(row: PlayerRow, gait: GaitOutput, legs: Legs): void {
    const { v, q } = this;
    const weight = gait.weight;
    const heading = row.yaw + gait.hipYaw;
    // Cube (x, y) is Three (x, z).
    const forward = v[0].set(Math.cos(heading), 0, Math.sin(heading));

    // The pelvis: squared onto its heading (the aiming clips stand it 40° off),
    // leant into the travel, and sunk into the stride.
    const hips = legs.hips;
    const world = hips.getWorldQuaternion(q[2]);
    const facing = v[1].copy(legs.hipsForward).applyQuaternion(world).setY(0);
    const square = q[0].identity();
    if (facing.lengthSq() > 1e-8) {
      square.slerp(q[1].setFromUnitVectors(facing.normalize(), forward), weight);
    }
    const tilt = v[2]
      .set(0, 1, 0)
      .addScaledVector(v[3].set(Math.cos(row.yaw), 0, Math.sin(row.yaw)), gait.leanForward)
      .addScaledVector(v[3].set(-Math.sin(row.yaw), 0, Math.cos(row.yaw)), gait.leanSide)
      .normalize();
    const lean = q[1].setFromUnitVectors(v[3].set(0, 1, 0), tilt);
    const placed = hips.getWorldPosition(v[1]);
    placed.y -= gait.drop;
    if (hips.parent) hips.parent.worldToLocal(placed);
    hips.position.copy(placed);
    this.turn(hips, q[3].multiplyQuaternions(lean, square).multiply(world));

    const side = v[1].set(-Math.sin(heading), 0, Math.cos(heading));
    for (let i = 0; i < 2; i += 1) {
      const { up, knee, foot, rest } = legs.chains[i];
      const target = gait.feet[i];
      const hip = up.getWorldPosition(v[2]);
      const kneeAt = knee.getWorldPosition(v[3]);
      const ankle = foot.getWorldPosition(v[4]);
      const upper = hip.distanceTo(kneeAt);
      const lower = kneeAt.distanceTo(ankle);
      const goal = v[5]
        .set(target.x, row.z + legs.ankleHeight + target.lift, target.y)
        .lerpVectors(ankle, v[5], weight);

      const { elbow } = solveTwoBone(
        [hip.x, hip.y, hip.z],
        [goal.x, goal.y, goal.z],
        upper,
        lower,
        [forward.x, KNEE_POLE_UP, forward.z],
      );
      // Thigh onto the solved knee, then shin onto the ankle: two shortest-arc
      // turns, which leave each bone's own twist as the clip had it.
      this.swing(
        [up],
        kneeAt.sub(hip),
        v[6].set(elbow[0], elbow[1], elbow[2]).sub(hip).normalize(),
      );
      knee.getWorldPosition(v[3]);
      this.swing(
        [knee],
        foot.getWorldPosition(v[4]).sub(v[3]),
        v[6].copy(goal).sub(v[3]).normalize(),
      );

      // The foot: flat and pointing where the pelvis does, tipped by the swing.
      const flat = q[0]
        .setFromAxisAngle(v[6].set(0, 1, 0), Math.atan2(forward.x, forward.z))
        .multiply(rest);
      // About the side axis a positive turn lifts the toe; the gait's is down.
      const tipped = q[1].setFromAxisAngle(side, -target.pitch).multiply(flat);
      this.turn(foot, foot.getWorldQuaternion(q[2]).slerp(tipped, weight));
    }
  }

  /**
   * Point the weapon and the head exactly where the player is looking.
   *
   * No clip knows the pitch — Mixamo's animations all look at the horizon — and
   * the aiming clips do not even hold the rifle along the body's own forward. So
   * the barrel's direction is measured off the hand that holds it and the spine
   * is turned by whatever takes it onto the look direction; then the neck and
   * head are turned the rest of the way. An enemy aiming at you is drawn aiming
   * at you, from a balcony or across a strafe.
   *
   * A reload is the exception: the rifle is meant to come off the line then, so
   * the chest is aimed instead and the arms are left to the clip.
   */
  private solveAim(row: PlayerRow): void {
    const { v, rig } = this;
    const pitch = row.pitch ?? 0;
    const look = v[7].set(
      Math.cos(row.yaw) * Math.cos(pitch),
      Math.sin(pitch),
      Math.sin(row.yaw) * Math.cos(pitch),
    );

    const reloading = this.overlay !== null && !this.overlay.aimed;
    const chest = rig.spine[rig.spine.length - 1];
    if (rig.hand && !reloading) {
      // The prop is modelled down +Z and gripped so that lands on the hand
      // bone's +Y, down the fingers (`GRIP_ROTATION` in `CharacterModel.ts`).
      const barrel = v[0].set(0, 1, 0).applyQuaternion(rig.hand.getWorldQuaternion(this.q[2]));
      this.swing(rig.spine, barrel, look);
    } else if (chest && rig.chestForward) {
      const front = v[0]
        .copy(rig.chestForward)
        .applyQuaternion(chest.getWorldQuaternion(this.q[2]));
      this.swing(rig.spine, front, look);
    }

    if (rig.neck.length > 0 && rig.headForward && rig.headUp) {
      const head = rig.neck[rig.neck.length - 1];
      const face = v[0].copy(rig.headForward).applyQuaternion(head.getWorldQuaternion(this.q[2]));
      this.swing(rig.neck, face, look);
      // That fixes where the face points and nothing about which way up it
      // is: the spine's turn leaves the head cocked to one side. Level it by
      // twisting about the look itself, which cannot move the face off it.
      const level = v[1].set(0, 1, 0).addScaledVector(look, -look.y);
      const crown = v[0].copy(rig.headUp).applyQuaternion(head.getWorldQuaternion(this.q[2]));
      crown.addScaledVector(look, -crown.dot(look));
      // Looking straight up or down there is no level to find.
      if (level.lengthSq() > 1e-4) this.swing(rig.neck, crown, level.normalize());
    }
  }

  /** A shot left this player's weapon: flash, and kick the upper body. */
  triggerRecoil(): void {
    this.model.triggerMuzzleFlash();
    this.layer('firing_rifle', FIRE_HOLD, true);
  }

  /** This player started a reload. */
  triggerReload(): void {
    this.layer('reloading', RELOAD_HOLD, false);
  }

  dispose(): void {
    this.mixer.stopAllAction();
    this.mixer.uncacheRoot(this.model.instance.root);
    this.actions.clear();
  }
}

/** One leg, hip to ankle. */
interface LegChain {
  up: THREE.Bone;
  knee: THREE.Bone;
  foot: THREE.Bone;
  /** The foot's bind-pose rotation in model space: flat, toes down +Z. */
  rest: THREE.Quaternion;
}

interface Legs {
  hips: THREE.Bone;
  /** The pelvis' forward in its own frame. */
  hipsForward: THREE.Vector3;
  /** Left, then right — the order `locomotion.ts` numbers feet in. */
  chains: [LegChain, LegChain];
  /** How far above the ground an ankle sits with the foot flat. */
  ankleHeight: number;
  legLength: number;
  hipWidth: number;
  /** The hip joints' height over a planted ankle, in the bind pose. */
  hipHeight: number;
}

/** The bones the solve drives, and what the bind pose says about them. */
interface Rig {
  /** Null on a rig without legs to solve, which is then left to its clips. */
  legs: Legs | null;
  /** Spine, bottom to top. */
  spine: THREE.Bone[];
  /** Neck then head. */
  neck: THREE.Bone[];
  hand: THREE.Bone | null;
  chestForward: THREE.Vector3 | null;
  headForward: THREE.Vector3 | null;
  /** Model +Y in the head's own frame. */
  headUp: THREE.Vector3 | null;
}

/**
 * Measure a freshly built operator.
 *
 * Has to run before anything poses it or places it in a scene: the bind pose
 * stands facing model +Z with its feet flat, which is the only moment "forward"
 * and "flat" can be read off the bones rather than assumed about them.
 */
function readRig(three: typeof THREE, model: CharacterModel): Rig {
  model.rootGroup.updateMatrixWorld(true);
  const bone = (name: string) => model.bones.get(name) ?? null;
  const present = (names: string[]) => names.map(bone).filter((b): b is THREE.Bone => b !== null);
  /** A model-space axis, in a bone's own frame. */
  const axisOf = (b: THREE.Bone, x: number, y: number, z: number) =>
    new three.Vector3(x, y, z).applyQuaternion(
      b.getWorldQuaternion(new three.Quaternion()).invert(),
    );
  const forwardOf = (b: THREE.Bone) => axisOf(b, 0, 0, 1);
  const at = (b: THREE.Bone) => b.getWorldPosition(new three.Vector3());

  const spine = present(['Spine', 'Spine1', 'Spine2']);
  const neck = present(['Neck', 'Head']);
  const rig: Rig = {
    legs: null,
    spine,
    neck,
    hand: bone('RightHand'),
    chestForward: spine.length > 0 ? forwardOf(spine[spine.length - 1]) : null,
    headForward: neck.length > 0 ? forwardOf(neck[neck.length - 1]) : null,
    headUp: neck.length > 0 ? axisOf(neck[neck.length - 1], 0, 1, 0) : null,
  };

  const hips = bone('Hips');
  const chain = (side: string): LegChain | null => {
    const up = bone(`${side}UpLeg`);
    const knee = bone(`${side}Leg`);
    const foot = bone(`${side}Foot`);
    if (!up || !knee || !foot) return null;
    return { up, knee, foot, rest: foot.getWorldQuaternion(new three.Quaternion()) };
  };
  const left = chain('Left');
  const right = chain('Right');
  if (!hips || !left || !right) return rig;

  const legLength = at(left.up).distanceTo(at(left.knee)) + at(left.knee).distanceTo(at(left.foot));
  // A rig with nothing between hip and ankle has no stride to solve.
  if (legLength < 1e-6) return rig;
  const ankleHeight = (at(left.foot).y + at(right.foot).y) * 0.5;
  rig.legs = {
    hips,
    hipsForward: forwardOf(hips),
    chains: [left, right],
    ankleHeight,
    legLength,
    hipWidth: at(left.up).distanceTo(at(right.up)),
    hipHeight: (at(left.up).y + at(right.up).y) * 0.5 - ankleHeight,
  };
  return rig;
}
