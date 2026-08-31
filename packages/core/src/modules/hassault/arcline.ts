/**
 * Drawing the predicted throw.
 *
 * The arithmetic is `arc.ts`, which is deliberately free of three so it can be
 * tested headless. This is the thin part that puts it on screen, split out for
 * the same reason `effects.ts` and `decals.ts` are: it is the only other place
 * that touches three's object graph, and it takes the module as a parameter so
 * the lazy-load stays in one place.
 *
 * The geometry is allocated **once** at `ARC_SAMPLES + 2` points and rewritten in
 * place every frame — a line rebuilt sixty times a second from a fresh
 * `BufferGeometry` is sixty allocations and sixty disposals a second, for a
 * shape that is only ever the same length.
 */
import type * as THREE from 'three';

import { ARC_SAMPLES, type ThrowArc } from './arc';

/** The line itself. Warm, so it reads against grey geometry and blue water. */
const ARC_COLOR = 0xffcf7a;
/** The landing marker. Brighter than the line, because it is the answer. */
const MARK_COLOR = 0xfff0c8;
/** The marker's radius, in cube units — about a body's width. */
const MARK_RADIUS = 0.55;

export class ArcLine {
  private line: THREE.Line;
  private positions: Float32Array;
  private geometry: THREE.BufferGeometry;
  private mark: THREE.Mesh;
  private markGeo: THREE.BufferGeometry;
  private materials: THREE.Material[] = [];

  constructor(
    // Not kept: everything is built here and then only moved or rewritten in
    // place, so holding the module would be a field nothing reads.
    three: typeof THREE,
    private readonly scene: THREE.Scene,
  ) {
    // `+2` for the origin and the contact point, which are pushed outside the
    // sampling loop.
    this.positions = new Float32Array((ARC_SAMPLES + 2) * 3);
    this.geometry = new three.BufferGeometry();
    this.geometry.setAttribute('position', new three.BufferAttribute(this.positions, 3));
    const lineMat = new three.LineBasicMaterial({
      color: ARC_COLOR,
      transparent: true,
      opacity: 0.75,
      // Never writes depth, so the arc does not z-fight the floor it grazes; it
      // is still depth *tested*, so a throw around a corner is hidden by the
      // corner rather than drawn through it. Being able to see through walls
      // would make this a wall hack rather than an aiming aid.
      depthWrite: false,
    });
    this.materials.push(lineMat);
    this.line = new three.Line(this.geometry, lineMat);
    this.line.visible = false;
    // Below the view model's 2, so the arc can never draw over the grenade in
    // your hand.
    this.line.renderOrder = 1;
    scene.add(this.line);

    // A ring rather than a disc: a filled marker hides the thing you are aiming
    // at, which on a floor is usually the point.
    this.markGeo = new three.RingGeometry(MARK_RADIUS * 0.72, MARK_RADIUS, 24);
    // `RingGeometry` lies in XY; the floor is XZ.
    this.markGeo.rotateX(-Math.PI / 2);
    const markMat = new three.MeshBasicMaterial({
      color: MARK_COLOR,
      transparent: true,
      opacity: 0.55,
      side: three.DoubleSide,
      depthWrite: false,
    });
    this.materials.push(markMat);
    this.mark = new three.Mesh(this.markGeo, markMat);
    this.mark.visible = false;
    this.mark.renderOrder = 1;
    scene.add(this.mark);
  }

  /** Draw one predicted throw, in **cube** coordinates. */
  show(arc: ThrowArc): void {
    const points = arc.points;
    const count = Math.min(points.length, ARC_SAMPLES + 2);
    for (let i = 0; i < count; i++) {
      const p = points[i];
      // Cube (x, y, height) -> three (x, height, z). Converted here rather than
      // at the call site so every user of this class has one fewer chance to get
      // it backwards.
      this.positions[i * 3] = p[0];
      this.positions[i * 3 + 1] = p[2];
      this.positions[i * 3 + 2] = p[1];
    }
    this.geometry.setDrawRange(0, count);
    this.geometry.attributes.position.needsUpdate = true;
    // The bounding sphere is stale the moment the points move, and three culls
    // against it — without this the arc vanishes as soon as it leaves wherever
    // the first frame's throw happened to go.
    this.geometry.computeBoundingSphere();
    this.line.visible = count >= 2;

    // The marker is only drawn for a contact that was actually the **ground**.
    // A grenade that clipped a wall is going to carry on somewhere this preview
    // does not follow, and a ring on the wall would claim otherwise.
    if (arc.contact && arc.landed) {
      // Lifted a little, or it z-fights the floor it is lying on.
      this.mark.position.set(arc.contact[0], arc.contact[2] + 0.02, arc.contact[1]);
      this.mark.visible = true;
    } else {
      this.mark.visible = false;
    }
  }

  /** Stop drawing. Called on every frame with no grenade in hand. */
  hide(): void {
    this.line.visible = false;
    this.mark.visible = false;
  }

  get visible(): boolean {
    return this.line.visible;
  }

  dispose(): void {
    this.scene.remove(this.line);
    this.scene.remove(this.mark);
    this.geometry.dispose();
    this.markGeo.dispose();
    for (const material of this.materials) material.dispose();
    this.materials = [];
  }
}
