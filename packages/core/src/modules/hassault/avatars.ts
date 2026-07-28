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

import type { PlayerRow } from './net';
import { PLAYER_ABOVE_EYE, PLAYER_EYE_HEIGHT, PLAYER_RADIUS } from './world';

/** Total body height, matching what the collision code reserves. */
const BODY_HEIGHT = PLAYER_EYE_HEIGHT + PLAYER_ABOVE_EYE;

/** CLA sand, RVSF blue — AssaultCube's two teams, at a glance. */
const TEAM_COLORS = [0xd9a441, 0x4c8fd4];

interface Avatar {
  group: THREE.Group;
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

  constructor(
    private readonly three: typeof THREE,
    private readonly scene: THREE.Scene,
  ) {}

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
  }

  dispose(): void {
    for (const avatar of this.avatars.values()) avatar.dispose();
    this.avatars.clear();
  }

  get size(): number {
    return this.avatars.size;
  }
}
