/**
 * Remote-player avatars: a body, a facing wedge, and a nameplate.
 *
 * Separate from the panel because it is the only part of the render loop that
 * touches three's object graph directly, and separate from `geometry.ts` because
 * that file is deliberately three-free so it can be unit-tested. This one cannot
 * be — it *is* the three code — so it takes the module as a parameter rather than
 * importing it, keeping the lazy-load in one place.
 */
import type * as THREE from 'three';

import { currentHitbox } from './hitbox';
import type { PlayerRow } from './net';
import { CROUCH_HEIGHT, STANDING_HEIGHT } from './player';
import { PLAYER_EYE_HEIGHT, PLAYER_RADIUS } from './world';

/** Total body height, matching what the collision code reserves. */
const BODY_HEIGHT = STANDING_HEIGHT;

/**
 * How far a crouched body is squashed.
 *
 * The capsule is *scaled* rather than rebuilt, because a crouch is animated —
 * `row.crouch` arrives as a fraction, and disposing and recreating a geometry per
 * frame of the transition would be absurd. It has to be visible and it has to be
 * right: the server rewinds a shot against exactly this height (see
 * `resolve_shot`'s `heights`), so an avatar that stayed standing while its hitbox
 * shrank would make cover look like a miss.
 */
const CROUCH_SCALE = CROUCH_HEIGHT / STANDING_HEIGHT;

/** CLA sand, RVSF blue — AssaultCube's two teams, at a glance. */
const TEAM_COLORS = [0xd9a441, 0x4c8fd4];

/**
 * The debug hitbox, and the band inside it that takes the head multiplier.
 *
 * Deliberately not team colours: this is a measuring tool, and one that changes
 * colour depending on who you are looking at is one you cannot compare two
 * readings with.
 */
const HITBOX_COLOR = 0x33f2d9;
const HEAD_BAND_COLOR = 0xffc740;

interface Avatar {
  group: THREE.Group;
  dispose: () => void;
}

interface Hitbox {
  group: THREE.Group;
  body: THREE.LineSegments;
  head: THREE.LineSegments;
  dispose: () => void;
}

function makeLabelTexture(three: typeof THREE, name: string): THREE.CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 64;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    ctx.font = 'bold 34px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    // Stroke behind fill: a name has to stay readable against both a bright sky
    // and a dark interior, and this is cheaper than an outline shader.
    ctx.lineWidth = 6;
    ctx.strokeStyle = 'rgba(0,0,0,0.85)';
    ctx.strokeText(name, 128, 32);
    ctx.fillStyle = '#fff';
    ctx.fillText(name, 128, 32);
  }
  const texture = new three.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

/**
 * Manages the set of avatar objects in a scene, keyed by player id.
 *
 * `sync` is called every frame with whatever the interpolator produced, and
 * diffs against what is already in the scene — creating, moving and disposing as
 * membership changes.
 */
export class AvatarPool {
  private avatars = new Map<string, Avatar>();
  /**
   * The debug hitboxes, if they are on.
   *
   * A pool of its own rather than a child of each avatar group, and that is
   * load-bearing: the avatar group is **scaled on Y** to animate a crouch, so a
   * head band parented to it would be squashed by the same factor and would stop
   * being the band the server actually uses. These are positioned and sized in
   * world units per frame instead.
   */
  private hitboxes = new Map<string, Hitbox>();
  private showHitboxes = false;

  constructor(
    private readonly three: typeof THREE,
    private readonly scene: THREE.Scene,
  ) {}

  /**
   * Draw (or stop drawing) the exact volume a shot is resolved against.
   *
   * This exists because "is the body drawn where it can be hit?" was a question
   * with no way to ask it. The failure it catches is invisible in play: you
   * simply miss slightly more often in some situations than others, and blame
   * the netcode.
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
    // A unit box scaled per frame, so a crouch is a scale change rather than a
    // geometry rebuild sixty times a second.
    const unit = new three.BoxGeometry(1, 1, 1);
    const edges = new three.EdgesGeometry(unit);
    // `depthTest` stays on, like the nameplates: a hitbox visible through a wall
    // is a wall hack, whatever it is labelled. The honest picture is a box that
    // the wall hides, which is also the picture that tells you about cover.
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
   * Put each hitbox where the server would rewind a shot to.
   *
   * Every number here comes from `currentHitbox()` — the spec the server pushed
   * in at join time — and none of them is recomputed locally. A debug overlay
   * drawn from a second copy of the numbers would agree with itself and with
   * nothing else, which is the exact failure it is meant to detect.
   */
  private syncHitboxes(rows: PlayerRow[], seen: Set<string>): void {
    const spec = currentHitbox();
    // A shape this renderer does not know how to draw. Drawing a box for it
    // would be a confident picture of the wrong body.
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
      // Cube (x, y, height) → three (x, height, z), the same mapping the avatar
      // group uses. The box is centred, so it sits half a height above the feet.
      box.group.position.set(row.x, row.z, row.y);
      box.body.scale.set(width, height, width);
      box.body.position.y = height / 2;
      // Measured **down from the top**, so it follows a crouch instead of
      // floating above it — which is why the server defines it that way, and
      // the one thing about a hitbox worth being able to see.
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
    const three = this.three;
    const group = new three.Group();
    const color = TEAM_COLORS[row.team] ?? TEAM_COLORS[0];

    // A capsule of exactly the collision dimensions, so what you see is what you
    // can shoot at later. `CapsuleGeometry`'s length excludes the two caps.
    const bodyGeo = new three.CapsuleGeometry(
      PLAYER_RADIUS,
      BODY_HEIGHT - PLAYER_RADIUS * 2,
      6,
      12,
    );
    const bodyMat = new three.MeshLambertMaterial({ color });
    const body = new three.Mesh(bodyGeo, bodyMat);
    body.position.y = BODY_HEIGHT / 2;
    group.add(body);

    // A wedge at eye height: without it a capsule gives no clue which way
    // someone is facing, which is most of what you want to know about them.
    const noseGeo = new three.ConeGeometry(0.45, 1.2, 8);
    const noseMat = new three.MeshLambertMaterial({ color: 0xffffff });
    const nose = new three.Mesh(noseGeo, noseMat);
    nose.rotation.x = -Math.PI / 2;
    nose.position.set(0, PLAYER_EYE_HEIGHT, -PLAYER_RADIUS - 0.4);
    group.add(nose);

    const labelTexture = makeLabelTexture(three, row.name);
    // `depthTest` stays on (the default): a nameplate drawn through walls is a
    // wallhack, and this is the same scene the human plays in.
    const labelMat = new three.SpriteMaterial({ map: labelTexture, transparent: true });
    const label = new three.Sprite(labelMat);
    label.scale.set(6, 1.5, 1);
    label.position.y = BODY_HEIGHT + 1.2;
    group.add(label);

    this.scene.add(group);
    return {
      group,
      dispose: () => {
        this.scene.remove(group);
        bodyGeo.dispose();
        bodyMat.dispose();
        noseGeo.dispose();
        noseMat.dispose();
        labelTexture.dispose();
        labelMat.dispose();
      },
    };
  }

  sync(rows: PlayerRow[]): void {
    const seen = new Set<string>();
    for (const row of rows) {
      seen.add(row.id);
      let avatar = this.avatars.get(row.id);
      if (!avatar) {
        avatar = this.create(row);
        this.avatars.set(row.id, avatar);
      }
      // The dead are hidden rather than removed: they respawn in three seconds,
      // and disposing the whole group to rebuild it is churn for nothing. There
      // are no corpses because there is no death animation to leave one in.
      avatar.group.visible = row.alive !== false;
      if (!avatar.group.visible) continue;
      // Cube (x, y, height) → three (x, height, z).
      avatar.group.position.set(row.x, row.z, row.y);
      // Same derivation as the camera: three's default forward is -Z, so a yaw
      // about +x becomes this rotation about Y.
      avatar.group.rotation.y = -row.yaw - Math.PI / 2;
      // Squashed by however far into a crouch they are. Scaled on Y only, from the
      // feet, because that is what crouching does — and it keeps the nameplate,
      // which lives at the top of the group, coming down with the body.
      const crouch = row.crouch ?? 0;
      avatar.group.scale.y = 1 + (CROUCH_SCALE - 1) * crouch;
      // A player whose input has stopped arriving is shown translucent rather
      // than removed — they may just be lagging, and vanishing bodies are worse.
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
