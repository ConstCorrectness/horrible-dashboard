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
import { CharacterAnimator, CharacterModel } from './models';
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

  constructor(
    private readonly three: typeof THREE,
    private readonly scene: THREE.Scene,
  ) {}

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

  private create(row: PlayerRow): Avatar {
    const model = new CharacterModel(this.three, row.team, row.name);
    const animator = new CharacterAnimator(model);
    const group = model.rootGroup;
    this.scene.add(group);

    return {
      group,
      model,
      animator,
      dispose: () => {
        this.scene.remove(group);
        model.dispose();
      },
    };
  }

  sync(rows: PlayerRow[], dt = 0.016): void {
    const seen = new Set<string>();
    for (const row of rows) {
      seen.add(row.id);
      let avatar = this.avatars.get(row.id);
      if (!avatar) {
        avatar = this.create(row);
        this.avatars.set(row.id, avatar);
      }

      // Hide or show
      avatar.group.visible = row.alive !== false;
      if (!avatar.group.visible) continue;

      // Position in Three.js coordinates: Cube (x, y, height) -> Three (x, height, z)
      avatar.group.position.set(row.x, row.z, row.y);

      // Facing yaw: Three's default forward is -Z
      avatar.group.rotation.y = -row.yaw - Math.PI / 2;

      // Update skeletal animation engine
      avatar.animator.update(dt, row);

      // Lagging / stale player translucency
      avatar.group.traverse((obj) => {
        const mesh = obj as THREE.Mesh;
        const material = mesh.material as THREE.Material | undefined;
        if (material && 'opacity' in material) {
          material.transparent = true;
          material.opacity = row.stale ? 0.35 : 1;
        }
      });
    }

    for (const [id, avatar] of this.avatars) {
      if (seen.has(id)) continue;
      avatar.dispose();
      this.avatars.delete(id);
    }

    if (this.showHitboxes) this.syncHitboxes(rows, seen);
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
