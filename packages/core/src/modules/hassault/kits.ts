/**
 * 3D rendering for dropped defuse kits.
 *
 * Placed on the floor when a defender carrying a defusal kit is eliminated.
 * Features a military tactical pouch with wire-cutter handles and a cyan glow ring.
 */
import type * as THREE from 'three';
import type { ModeKit } from './net';

const HOVER = 0.35;
const BOB = 0.08;
const BOB_SPEED = 2.2;
const SPIN_SPEED = 1.4;

interface LiveKit {
  id: string;
  group: THREE.Group;
  pouch: THREE.Mesh;
  shears: THREE.Mesh;
  ring: THREE.Mesh;
}

export class KitPool {
  private readonly three: typeof THREE;
  private readonly scene: THREE.Scene;
  private readonly pouchGeom: THREE.BufferGeometry;
  private readonly shearsGeom: THREE.BufferGeometry;
  private readonly ringGeom: THREE.BufferGeometry;
  private readonly pouchMat: THREE.Material;
  private readonly shearsMat: THREE.Material;
  private readonly ringMat: THREE.Material;

  private kits = new Map<string, LiveKit>();
  private phase = 0;

  constructor(three: typeof THREE, scene: THREE.Scene) {
    this.three = three;
    this.scene = scene;

    // Tactical kit pouch: dark navy / tactical blue box
    this.pouchGeom = new three.BoxGeometry(0.55, 0.22, 0.4);
    this.pouchMat = new three.MeshLambertMaterial({
      color: 0x1e3a8a, // deep tactical blue
    });

    // Wire-cutter shears strapped to top: high-contrast chrome / metallic yellow-green handles
    this.shearsGeom = new three.BoxGeometry(0.35, 0.08, 0.18);
    this.shearsMat = new three.MeshLambertMaterial({
      color: 0x38bdf8, // cyan accent
    });

    // Floor beacon ring
    this.ringGeom = new three.RingGeometry(0.4, 0.55, 24);
    this.ringMat = new three.MeshBasicMaterial({
      color: 0x38bdf8,
      transparent: true,
      opacity: 0.65,
      side: three.DoubleSide,
    });
  }

  sync(serverKits: ModeKit[] | undefined): void {
    if (!serverKits || serverKits.length === 0) {
      for (const kit of this.kits.values()) {
        this.scene.remove(kit.group);
      }
      this.kits.clear();
      return;
    }

    const seen = new Set<string>();
    for (const sk of serverKits) {
      seen.add(sk.id);
      let live = this.kits.get(sk.id);
      if (!live) {
        const group = new this.three.Group();

        const pouch = new this.three.Mesh(this.pouchGeom, this.pouchMat);
        pouch.castShadow = true;
        group.add(pouch);

        const shears = new this.three.Mesh(this.shearsGeom, this.shearsMat);
        shears.position.y = 0.14;
        group.add(shears);

        const ring = new this.three.Mesh(this.ringGeom, this.ringMat);
        ring.rotation.x = -Math.PI / 2;
        ring.position.y = -HOVER + 0.04;
        group.add(ring);

        group.position.set(sk.x, sk.z + HOVER, sk.y);
        this.scene.add(group);

        live = { id: sk.id, group, pouch, shears, ring };
        this.kits.set(sk.id, live);
      } else {
        live.group.position.x = sk.x;
        live.group.position.z = sk.y;
      }
    }

    for (const [id, live] of this.kits.entries()) {
      if (!seen.has(id)) {
        this.scene.remove(live.group);
        this.kits.delete(id);
      }
    }
  }

  update(dt: number): void {
    this.phase += dt * BOB_SPEED;
    const dy = Math.sin(this.phase) * BOB;
    const rot = dt * SPIN_SPEED;

    for (const kit of this.kits.values()) {
      kit.pouch.position.y = dy;
      kit.shears.position.y = dy + 0.14;
      kit.pouch.rotation.y += rot;
      kit.shears.rotation.y += rot;
    }
  }

  dispose(): void {
    for (const kit of this.kits.values()) {
      this.scene.remove(kit.group);
    }
    this.kits.clear();
    this.pouchGeom.dispose();
    this.shearsGeom.dispose();
    this.ringGeom.dispose();
    this.pouchMat.dispose();
    this.shearsMat.dispose();
    this.ringMat.dispose();
  }
}
