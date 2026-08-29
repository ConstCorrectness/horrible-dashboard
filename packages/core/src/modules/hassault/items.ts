/**
 * Drawing the items lying on the map.
 *
 * A **renderer for something the server already decided**, the same contract
 * `nades.ts` and `effects.ts` have: placements arrive once with the welcome,
 * availability arrives in every snapshot, and nothing here decides whether a
 * pickup happened. A client that took an item locally would be predicting the
 * one thing prediction cannot help with — two players reaching the armour in the
 * same tick is exactly the case the server exists to settle.
 *
 * Shapes are **built from primitives**, like every other surface in this game:
 * AssaultCube's item models are its copyright and are never bundled, so a health
 * pack here is two boxes in a cross and an armour plate is a bevelled slab. They
 * are legible for the same reason the maps are — a silhouette and a colour is
 * all an item has to be from across a room.
 *
 * Two behaviours are load-bearing rather than decoration:
 *
 * - **A taken item is drawn as absent, not deleted.** It sinks and fades over a
 *   few frames and its floor ring stays, dimmed. Players learn spawn timings off
 *   that ring, and an item that simply vanished would make the map's rhythm
 *   invisible — which is most of what makes an item worth fighting over.
 * - **The bob is driven by a clock shared by every item**, so a whole map's items
 *   rise and fall together. Per-item phase looks livelier and reads as noise;
 *   in motion, a synchronised field is much easier to pick a *missing* item out
 *   of.
 *
 * Takes the three namespace as a parameter rather than importing it, so this file
 * never pulls three into the bundle — the same contract as `avatars.ts`,
 * `effects.ts`, `backdrop.ts`, `nades.ts` and `surfaces.ts`.
 */
import type * as THREE from 'three';

import type { ItemRow } from './net';

/** Body colour per kind. Warm for what heals, cool for what protects, brass for ammunition. */
const TINT: Record<string, number> = {
  health: 0xe4534a,
  helmet: 0x6f97c4,
  armour: 0x4c7fd4,
  ammo: 0xc9a227,
  clips: 0xb08d2a,
  grenade: 0x5d6b45,
};

/** How high above the floor an item floats, in cubes. */
const HOVER = 0.9;
/** Half-amplitude of the bob. */
const BOB = 0.18;
const BOB_SPEED = 1.6;
const SPIN_SPEED = 0.9;

/** Seconds an item takes to sink away when taken, and to pop back when it returns. */
const FADE = 0.22;

interface LiveItem {
  group: THREE.Group;
  body: THREE.Mesh;
  ring: THREE.Mesh;
  kind: string;
  z: number;
  /** 1 = fully present, 0 = taken. Eased toward `wanted`, never snapped: the
   *  snap is what makes a respawn look like a rendering glitch. */
  presence: number;
  wanted: number;
}

export class ItemPool {
  private items = new Map<number, LiveItem>();
  private readonly ringGeo: THREE.RingGeometry;
  private readonly geos: THREE.BufferGeometry[] = [];
  private readonly mats = new Map<string, THREE.MeshLambertMaterial>();
  private readonly ringMats = new Map<string, THREE.MeshBasicMaterial>();
  private elapsed = 0;

  constructor(
    private readonly three: typeof THREE,
    private readonly scene: THREE.Scene,
  ) {
    this.ringGeo = new three.RingGeometry(0.55, 0.78, 20);
    for (const [kind, color] of Object.entries(TINT)) {
      this.mats.set(kind, new three.MeshLambertMaterial({ color }));
      this.ringMats.set(
        kind,
        new three.MeshBasicMaterial({ color, transparent: true, opacity: 0.35 }),
      );
    }
  }

  /**
   * Place the map's items. Called once, when the welcome arrives.
   *
   * Rebuilds from scratch rather than diffing: this runs on joining a room, and
   * a room's items are fixed for its lifetime.
   */
  place(rows: ItemRow[]): void {
    this.clear();
    for (const row of rows) {
      const mat = this.mats.get(row.kind);
      if (!mat) continue; // an item kind this client is too old to draw
      const group = new this.three.Group();
      const body = new this.three.Mesh(this.shapeFor(row.kind), mat);
      group.add(body);

      // The floor ring, which is what a player actually navigates by: it stays
      // put while the body bobs, and it is still there when the item is gone.
      const ring = new this.three.Mesh(this.ringGeo, this.ringMats.get(row.kind)!);
      ring.rotation.x = -Math.PI / 2;
      ring.position.z = 0.03;
      group.add(ring);

      group.position.set(row.x, row.y, row.z);
      this.scene.add(group);
      this.items.set(row.id, {
        group,
        body,
        ring,
        kind: row.kind,
        z: row.z,
        presence: 1,
        wanted: 1,
      });
    }
  }

  /**
   * Tell the pool which items are currently gone.
   *
   * `undefined` means the server never said — an older host, or a match with no
   * items — and is deliberately **not** read as "none are gone": that would pop
   * every taken item back into existence once a tick.
   */
  sync(takenIds: number[] | undefined): void {
    if (!takenIds) return;
    const gone = new Set(takenIds);
    for (const [id, live] of this.items) live.wanted = gone.has(id) ? 0 : 1;
  }

  update(dt: number): void {
    this.elapsed += dt;
    const bob = Math.sin(this.elapsed * BOB_SPEED) * BOB;
    const spin = this.elapsed * SPIN_SPEED;
    const step = Math.min(1, dt / FADE);

    for (const live of this.items.values()) {
      live.presence += (live.wanted - live.presence) * step;
      const present = live.presence;
      live.body.visible = present > 0.02;
      // Sinks into the floor as it goes rather than shrinking in place: an item
      // being *taken off the floor* is the thing being depicted.
      live.body.position.z = HOVER + bob - (1 - present) * (HOVER + BOB);
      live.body.scale.setScalar(0.35 + present * 0.65);
      live.body.rotation.z = spin;
      const ringMat = live.ring.material as THREE.MeshBasicMaterial;
      // The ring never disappears: it is the timer players read the map from.
      ringMat.opacity = 0.12 + present * 0.23;
    }
  }

  dispose(): void {
    this.clear();
    this.ringGeo.dispose();
    for (const geo of this.geos) geo.dispose();
    this.geos.length = 0;
    for (const mat of this.mats.values()) mat.dispose();
    for (const mat of this.ringMats.values()) mat.dispose();
  }

  private clear(): void {
    for (const live of this.items.values()) this.scene.remove(live.group);
    this.items.clear();
  }

  /**
   * One item's silhouette, from primitives.
   *
   * Geometries are kept for disposal rather than shared per kind: there are a few
   * dozen items on a map, they are built once on join, and a cross needs two
   * boxes merged into one buffer anyway.
   */
  private shapeFor(kind: string): THREE.BufferGeometry {
    const three = this.three;
    let geo: THREE.BufferGeometry;
    switch (kind) {
      case 'health': {
        // A cross: the one item shape that needs no legend.
        const arm = new three.BoxGeometry(0.9, 0.28, 0.28);
        const stem = new three.BoxGeometry(0.28, 0.28, 0.9);
        geo = mergeInto(three, [arm, stem]);
        break;
      }
      case 'helmet':
        geo = new three.SphereGeometry(0.45, 12, 8, 0, Math.PI * 2, 0, Math.PI / 2);
        break;
      case 'armour':
        // A plate, wider than it is thick, so it reads as a vest end-on too.
        geo = new three.BoxGeometry(0.75, 0.3, 0.9);
        break;
      case 'ammo':
        geo = new three.BoxGeometry(0.8, 0.5, 0.5);
        break;
      case 'clips':
        geo = new three.BoxGeometry(0.45, 0.28, 0.6);
        break;
      case 'grenade':
        geo = new three.CapsuleGeometry(0.24, 0.34, 4, 8);
        break;
      default:
        geo = new three.BoxGeometry(0.5, 0.5, 0.5);
    }
    this.geos.push(geo);
    return geo;
  }
}

/**
 * Merge a handful of geometries into one buffer.
 *
 * Hand-rolled rather than pulled from `three/examples`: that entry point is an
 * addon path this bundle does not otherwise import, and the whole requirement
 * here is concatenating position and normal attributes of a couple of boxes.
 */
function mergeInto(three: typeof THREE, parts: THREE.BufferGeometry[]): THREE.BufferGeometry {
  const positions: number[] = [];
  const normals: number[] = [];
  for (const part of parts) {
    const nonIndexed = part.index ? part.toNonIndexed() : part;
    positions.push(...Array.from(nonIndexed.getAttribute('position').array));
    normals.push(...Array.from(nonIndexed.getAttribute('normal').array));
    if (nonIndexed !== part) nonIndexed.dispose();
    part.dispose();
  }
  const merged = new three.BufferGeometry();
  merged.setAttribute('position', new three.Float32BufferAttribute(positions, 3));
  merged.setAttribute('normal', new three.Float32BufferAttribute(normals, 3));
  return merged;
}
