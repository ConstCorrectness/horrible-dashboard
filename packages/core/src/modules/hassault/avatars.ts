/**
 * Remote-player avatars: articulated humanoid character models, animations, and nameplates.
 *
 * Uses `CharacterModel` and `CharacterAnimator` to render natural low-poly tactical operators
 * with breathing sway, multi-directional locomotion, dynamic crouching, airborne tucking,
 * aim pitch looking, weapon props, and muzzle flashes.
 *
 * Strictly respects canonical collision dimensions (r=1.1, h=5.2) ensuring 100% hitbox invariance.
 */
import type * as THREE from 'three';

import { currentHitbox } from './hitbox';
import { CharacterAnimator, CharacterModel, loadOperator, type OperatorAsset } from './models';
import type { PlayerRow } from './net';

/**
 * The debug hitbox, and the band inside it that takes the head multiplier.
 */
const HITBOX_COLOR = 0x33f2d9;
const HEAD_BAND_COLOR = 0xffc740;

interface Avatar {
  group: THREE.Group;
  model: CharacterModel;
  animator: CharacterAnimator;
  dispose: () => void;
}

interface Hitbox {
  group: THREE.Group;
  body: THREE.LineSegments;
  head: THREE.LineSegments;
  dispose: () => void;
}

/**
 * Manages the set of avatar objects in a scene, keyed by player id.
 *
 * `sync` is called every frame with whatever the interpolator produced, and
 * diffs against what is already in the scene — creating, moving, animating and disposing as
 * membership changes.
 */
export class AvatarPool {
  private avatars = new Map<string, Avatar>();
  /**
   * The debug hitboxes, if enabled via `draw.hitboxes`.
   */
  private hitboxes = new Map<string, Hitbox>();
  private showHitboxes = false;

  /**
   * The operator GLB, once it has arrived.
   *
   * Avatars cannot be built before it does, so `sync` draws none until it
   * resolves and then picks up on the next frame. A match that starts while the
   * model is still downloading is missing its bodies for a moment rather than
   * throwing — and hitboxes, which need no asset, keep drawing throughout.
   */
  private asset: OperatorAsset | null = null;
  private assetFailed = false;

  constructor(
    private readonly three: typeof THREE,
    private readonly scene: THREE.Scene,
  ) {
    loadOperator()
      .then((asset) => {
        this.asset = asset;
      })
      .catch((err) => {
        this.assetFailed = true;
        console.error('hassault: operator model failed to load; players will not be drawn', err);
      });
  }

  /** Whether the operator model is in hand. Surfaced for the boot overlay. */
  get ready(): boolean {
    return this.asset !== null;
  }

  /** Whether the model gave up loading, so a caller can say so rather than wait. */
  get failed(): boolean {
    return this.assetFailed;
  }

  /**
   * Draw (or stop drawing) the exact volume a shot is resolved against.
   */
  setHitboxes(on: boolean): void {
    if (this.showHitboxes === on) return;
    this.showHitboxes = on;
    if (!on) {
      for (const box of this.hitboxes.values()) box.dispose();
      this.hitboxes.clear();
    }
  }

  private createHitbox(): Hitbox {
    const three = this.three;
    const group = new three.Group();
    const unit = new three.BoxGeometry(1, 1, 1);
    const edges = new three.EdgesGeometry(unit);

    const bodyMat = new three.LineBasicMaterial({ color: HITBOX_COLOR });
    const headMat = new three.LineBasicMaterial({ color: HEAD_BAND_COLOR });
    const body = new three.LineSegments(edges, bodyMat);
    const head = new three.LineSegments(edges, headMat);
    group.add(body);
    group.add(head);
    this.scene.add(group);

    return {
      group,
      body,
      head,
      dispose: () => {
        this.scene.remove(group);
        unit.dispose();
        edges.dispose();
        bodyMat.dispose();
        headMat.dispose();
      },
    };
  }

  /**
   * Put each debug hitbox where the server would rewind a shot to.
   */
  private syncHitboxes(rows: PlayerRow[], seen: Set<string>): void {
    const spec = currentHitbox();
    const drawable = spec.shape === 'cylinder';
    for (const row of rows) {
      if (!drawable || row.alive === false) continue;
      let box = this.hitboxes.get(row.id);
      if (!box) {
        box = this.createHitbox();
        this.hitboxes.set(row.id, box);
      }
      const crouch = row.crouch ?? 0;
      const height = spec.standingHeight + (spec.crouchHeight - spec.standingHeight) * crouch;
      const width = spec.radius * 2;

      // Cube (x, y, height) -> Three (x, height, z)
      box.group.position.set(row.x, row.z, row.y);
      box.body.scale.set(width, height, width);
      box.body.position.y = height / 2;

      const band = Math.min(spec.headBand, height);
      box.head.scale.set(width, band, width);
      box.head.position.y = height - band / 2;
    }
    for (const [id, box] of this.hitboxes) {
      if (seen.has(id)) continue;
      box.dispose();
      this.hitboxes.delete(id);
    }
  }

  private create(row: PlayerRow, asset: OperatorAsset): Avatar {
    // Meshes receive shadow but never cast: the scene's shadow map is rendered
    // once per map and holds static world geometry only (see
    // `HorribleAssaultPanel`), so a casting avatar would leave its shadow
    // standing where it used to be — whereas *receiving* against a static map is
    // exact, and is what stops a player crossing a shaded doorway staying lit
    // like a cutout. `instantiateOperator` sets the flag as it walks the clone.
    const model = new CharacterModel(this.three, asset, row.team, row.name);
    const animator = new CharacterAnimator(this.three, model, asset);
    const group = model.rootGroup;
    this.scene.add(group);

    return {
      group,
      model,
      animator,
      dispose: () => {
        this.scene.remove(group);
        animator.dispose();
        model.dispose();
      },
    };
  }

  sync(rows: PlayerRow[], dt = 0.016): void {
    const seen = new Set<string>();
    const asset = this.asset;
    for (const row of rows) {
      seen.add(row.id);
      if (!asset) continue;

      let avatar = this.avatars.get(row.id);
      if (!avatar) {
        avatar = this.create(row, asset);
        this.avatars.set(row.id, avatar);
      }

      // A dead player stays drawn so the death animation can play out. The rig
      // this replaced had no death clip to run, so hiding the body on the frame
      // it died was all there was; now hiding it would cut the kill short.
      avatar.group.visible = true;

      // Position in Three.js coordinates: Cube (x, y, height) -> Three (x, height, z)
      avatar.group.position.set(row.x, row.z, row.y);

      // Facing yaw: Three's default forward is -Z
      avatar.group.rotation.y = -row.yaw - Math.PI / 2;

      // Clip selection, crossfades, aim pitch, weapon and stale fade.
      avatar.animator.update(dt, row);
    }

    for (const [id, avatar] of this.avatars) {
      if (seen.has(id)) continue;
      avatar.dispose();
      this.avatars.delete(id);
    }

    if (this.showHitboxes) this.syncHitboxes(rows, seen);
  }

  /**
   * A shot left this player's weapon.
   *
   * Driven from the snapshot's `fx`, which is the only place a client learns
   * that somebody else fired — `PlayerRow` carries no trigger state, because a
   * shot is an event and a row is a position. Silently ignores an id with no
   * avatar: our own shots arrive here too and we do not draw ourselves.
   */
  fired(id: string): void {
    this.avatars.get(id)?.animator.triggerRecoil();
  }

  dispose(): void {
    for (const avatar of this.avatars.values()) avatar.dispose();
    this.avatars.clear();
    for (const box of this.hitboxes.values()) box.dispose();
    this.hitboxes.clear();
  }

  get size(): number {
    return this.avatars.size;
  }
}
