/**
 * 3D rendering for what players put on the floor in a defuse round: guns dropped
 * with `Command.drop`, and the bomb when its carrier set it down.
 *
 * A renderer only, like `KitPool`: it draws exactly what `ModeShared.drops` and
 * `ModeShared.bomb` said and never decides something was picked up — whether a
 * body is standing on an item is the server's call, and a client that removed
 * it early would show a gun vanishing that the server then hands to somebody
 * else.
 */
import type * as THREE from 'three';
import type { ModeBomb, ModeDrop } from './net';

const HOVER = 0.3;
const BOB = 0.06;
const BOB_SPEED = 2.2;
const SPIN_SPEED = 1.2;
/** The bomb's light, in blinks per second — slow, because it is not planted. */
const BLINK_HZ = 1.2;

/** Length per weapon slot, so a sniper on the floor reads as one at a glance. */
const GUN_LENGTH: Record<number, number> = { 2: 0.9, 3: 0.75, 4: 1.25 };
const GUN_COLOUR: Record<number, number> = { 2: 0x4b5563, 3: 0x7c5a3a, 4: 0x3f4f3a };
const FALLBACK_LENGTH = 0.9;
const FALLBACK_COLOUR = 0x4b5563;

interface LiveGun {
  group: THREE.Group;
  body: THREE.Mesh;
}

interface LiveBomb {
  group: THREE.Group;
  body: THREE.Mesh;
  light: THREE.Mesh;
}

export class DropPool {
  private readonly three: typeof THREE;
  private readonly scene: THREE.Scene;
  private readonly gunGeoms = new Map<number, THREE.BufferGeometry>();
  private readonly gunMats = new Map<number, THREE.Material>();
  private readonly bombGeom: THREE.BufferGeometry;
  private readonly bombMat: THREE.Material;
  private readonly lightGeom: THREE.BufferGeometry;
  private readonly lightMat: THREE.Material;

  private guns = new Map<string, LiveGun>();
  private bomb: LiveBomb | null = null;
  private phase = 0;
  private clock = 0;

  constructor(three: typeof THREE, scene: THREE.Scene) {
    this.three = three;
    this.scene = scene;
    this.bombGeom = new three.BoxGeometry(0.5, 0.26, 0.34);
    this.bombMat = new three.MeshLambertMaterial({ color: 0x5b3a29 });
    this.lightGeom = new three.SphereGeometry(0.05, 8, 6);
    this.lightMat = new three.MeshBasicMaterial({ color: 0xff3b30 });
  }

  sync(drops: ModeDrop[] | undefined, bomb: ModeBomb | undefined): void {
    const seen = new Set<string>();
    for (const drop of drops ?? []) {
      seen.add(drop.id);
      let live = this.guns.get(drop.id);
      if (!live) {
        const group = new this.three.Group();
        const body = new this.three.Mesh(this.gunGeom(drop.slot), this.gunMat(drop.slot));
        body.castShadow = true;
        group.add(body);
        this.scene.add(group);
        live = { group, body };
        this.guns.set(drop.id, live);
      }
      live.group.position.set(drop.x, drop.z + HOVER, drop.y);
    }
    for (const [id, live] of this.guns) {
      if (!seen.has(id)) {
        this.scene.remove(live.group);
        this.guns.delete(id);
      }
    }

    // Only while it is lying loose. Carried it is on somebody's back and planted
    // it is the mode HUD's and the planted-bomb renderer's business.
    const loose = bomb?.state === 'dropped' && bomb.x !== undefined && bomb.y !== undefined;
    if (!loose) {
      if (this.bomb) this.scene.remove(this.bomb.group);
      this.bomb = null;
      return;
    }
    if (!this.bomb) {
      const group = new this.three.Group();
      const body = new this.three.Mesh(this.bombGeom, this.bombMat);
      body.castShadow = true;
      const light = new this.three.Mesh(this.lightGeom, this.lightMat);
      light.position.y = 0.16;
      group.add(body, light);
      this.scene.add(group);
      this.bomb = { group, body, light };
    }
    this.bomb.group.position.set(bomb.x ?? 0, (bomb.z ?? 0) + HOVER, bomb.y ?? 0);
  }

  update(dt: number): void {
    this.phase += dt * BOB_SPEED;
    this.clock += dt;
    const dy = Math.sin(this.phase) * BOB;
    const rot = dt * SPIN_SPEED;
    for (const gun of this.guns.values()) {
      gun.body.position.y = dy;
      gun.body.rotation.y += rot;
    }
    if (this.bomb) {
      this.bomb.body.position.y = dy;
      this.bomb.light.position.y = dy + 0.16;
      this.bomb.light.visible = (this.clock * BLINK_HZ) % 1 < 0.5;
    }
  }

  dispose(): void {
    for (const gun of this.guns.values()) this.scene.remove(gun.group);
    this.guns.clear();
    if (this.bomb) this.scene.remove(this.bomb.group);
    this.bomb = null;
    for (const geom of this.gunGeoms.values()) geom.dispose();
    for (const mat of this.gunMats.values()) mat.dispose();
    this.bombGeom.dispose();
    this.bombMat.dispose();
    this.lightGeom.dispose();
    this.lightMat.dispose();
  }

  private gunGeom(slot: number): THREE.BufferGeometry {
    let geom = this.gunGeoms.get(slot);
    if (!geom) {
      geom = new this.three.BoxGeometry(0.12, 0.16, GUN_LENGTH[slot] ?? FALLBACK_LENGTH);
      this.gunGeoms.set(slot, geom);
    }
    return geom;
  }

  private gunMat(slot: number): THREE.Material {
    let mat = this.gunMats.get(slot);
    if (!mat) {
      mat = new this.three.MeshLambertMaterial({ color: GUN_COLOUR[slot] ?? FALLBACK_COLOUR });
      this.gunMats.set(slot, mat);
    }
    return mat;
  }
}
