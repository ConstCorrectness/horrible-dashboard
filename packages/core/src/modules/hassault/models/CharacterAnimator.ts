/**
 * Plays the operator's animation clips against what the server says a player is
 * doing.
 *
 * Replaced a hand-written pose solver — the walk cycle used to be
 * `Math.sin(walkPhase)` fed into thigh rotations, because the rig it drove was
 * rigid boxes with no skin to deform. With a real skeleton the motion comes from
 * the 23 Mixamo clips baked into `hassault-operator.glb`, and this class's job
 * shrinks to three things: choose a clip, crossfade to it, and layer the two
 * corrections a clip cannot know about.
 *
 * Those corrections are aim pitch and upper-body actions, and both are applied
 * *after* the mixer runs, because `AnimationMixer` overwrites bone transforms
 * wholesale every update — anything set before it is discarded without warning.
 */

import type * as THREE from 'three';

import type { PlayerRow } from '../net';
import type { CharacterModel } from './CharacterModel';
import {
  fadeFor,
  isOneShot,
  isUpperBody,
  selectLocomotion,
  type OperatorClip,
  type OperatorState,
} from './clips';
import type { OperatorAsset } from './operator';

/** How long a fire action holds the upper body before locomotion takes it back. */
const FIRE_HOLD = 0.28;
/** A reload is a whole animation; let it run. */
const RELOAD_HOLD = 2.2;

/** Aim pitch is split down the spine so the whole torso leans into a look. */
const PITCH_SPINE = 0.35;
const PITCH_CHEST = 0.25;
const PITCH_HEAD = 0.4;

export class CharacterAnimator {
  private readonly mixer: THREE.AnimationMixer;
  private readonly actions = new Map<string, THREE.AnimationAction>();

  private current: OperatorClip | null = null;
  /** The action layered over the base, and how long it has left. */
  private overlay: { action: THREE.AnimationAction; remaining: number } | null = null;

  private prevX = 0;
  private prevY = 0;
  private hasPrev = false;
  private smoothedSpeed = 0;

  constructor(
    private readonly three: typeof THREE,
    private readonly model: CharacterModel,
    private readonly asset: OperatorAsset,
  ) {
    this.mixer = new three.AnimationMixer(model.instance.root);
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
  private layer(clip: OperatorClip, hold: number): void {
    const action = this.action(clip, true);
    if (!action) return;
    action.reset().setEffectiveWeight(1).play();
    this.overlay = { action, remaining: hold };
  }

  update(dt: number, row: PlayerRow): void {
    if (!(dt > 0) || Number.isNaN(dt)) dt = 1 / 60;

    this.model.decayMuzzleFlash(dt);
    this.model.setWeapon(row.weapon ?? 2);
    this.model.setOpacity(row.stale ? 0.35 : 1);

    this.play(selectLocomotion(this.deriveState(dt, row)));

    if (this.overlay) {
      this.overlay.remaining -= dt;
      if (this.overlay.remaining <= 0) {
        this.overlay.action.fadeOut(0.15);
        this.overlay = null;
      }
    }

    this.mixer.update(dt);

    // After the mixer, never before — see the class comment.
    this.applyAimPitch(row);
    this.model.nameplate.visible = row.alive !== false;
  }

  /**
   * Turn two positions and a snapshot into the state a clip is chosen from.
   *
   * Speed is smoothed because it is derived from interpolated positions: the
   * raw frame-to-frame delta crosses the idle threshold constantly at a walk,
   * and a clip that reselects every other frame never finishes a crossfade.
   */
  private deriveState(dt: number, row: PlayerRow): OperatorState {
    let vx = 0;
    let vy = 0;
    if (this.hasPrev) {
      vx = (row.x - this.prevX) / dt;
      vy = (row.y - this.prevY) / dt;
    } else {
      this.hasPrev = true;
    }
    this.prevX = row.x;
    this.prevY = row.y;

    const speed = Math.min(Math.hypot(vx, vy), 25);
    this.smoothedSpeed += (speed - this.smoothedSpeed) * Math.min(1, dt * 10);

    // Project velocity onto where the player is looking, so "forward" means
    // forward for them rather than for the world.
    const cos = Math.cos(row.yaw);
    const sin = Math.sin(row.yaw);
    const forward = vx * cos + vy * sin;
    const strafe = -vx * sin + vy * cos;
    const magnitude = Math.max(1e-3, Math.hypot(forward, strafe));

    return {
      alive: row.alive !== false,
      ground: row.ground !== false,
      crouch: row.crouch ?? 0,
      speed: this.smoothedSpeed,
      forward: forward / magnitude,
      strafe: strafe / magnitude,
      hurt: (row.hp ?? 100) < 35,
    };
  }

  /**
   * Lean the spine and head toward where the player is actually aiming.
   *
   * No clip knows the pitch — Mixamo's animations all look at the horizon — so
   * without this an enemy shooting down at you from a balcony appears to be
   * firing straight ahead, which misreads their attention entirely. Spread
   * across three joints because putting it all in the neck snaps the head off
   * the shoulders.
   *
   * Applied as a delta on top of the posed rotation rather than a set, so the
   * clip's own spine motion survives.
   */
  private applyAimPitch(row: PlayerRow): void {
    if (row.alive === false) return;
    const pitch = row.pitch ?? 0;
    if (pitch === 0) return;

    const spine = this.model.bones.get('Spine');
    const chest = this.model.bones.get('Spine2');
    const head = this.model.bones.get('Head');

    if (spine) spine.rotation.x -= pitch * PITCH_SPINE;
    if (chest) chest.rotation.x -= pitch * PITCH_CHEST;
    if (head) head.rotation.x -= pitch * PITCH_HEAD;
  }

  /** A shot left this player's weapon: flash, and kick the upper body. */
  triggerRecoil(): void {
    this.model.triggerMuzzleFlash();
    this.layer('firing_rifle', FIRE_HOLD);
  }

  /** This player started a reload. */
  triggerReload(): void {
    this.layer('reloading', RELOAD_HOLD);
  }

  dispose(): void {
    this.mixer.stopAllAction();
    this.mixer.uncacheRoot(this.model.instance.root);
    this.actions.clear();
  }
}
