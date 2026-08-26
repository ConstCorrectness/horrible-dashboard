/**
 * Tracers and impacts.
 *
 * Every shot the server resolves comes back in the snapshot with an origin and
 * one endpoint per pellet, so there is exactly one place that decides where a
 * bullet went and the renderer only draws what it is told. The alternative — a
 * client-side raycast for the local player's own tracer — would be a second
 * implementation of the world ray whose only job is to disagree with the first
 * one occasionally.
 *
 * The visible cost is that your own tracer appears half a round trip after the
 * trigger. On a LAN that is ~10 ms and invisible; what actually sells the shot is
 * the muzzle flash and the crosshair kick, and both of those are local and
 * immediate (`combat.ts`).
 *
 * Separate from the panel for the same reason `avatars.ts` is: this is the only
 * other part of the render loop that touches three's object graph, and it takes
 * the module as a parameter rather than importing it so the lazy-load stays in
 * one place.
 */
import type * as THREE from 'three';

/** Tracer lifetime. Long enough to register, short enough not to draw a web. */
const TRACER_LIFE = 0.075;
const IMPACT_LIFE = 0.3;
/** How long a detonation's light and debris shell last. */
const BLAST_LIFE = 0.5;

/** The colour a detonation throws. Smoke and fire are tinted like what they
 * become, so the pop and the cloud that follows it read as one event. */
const BLAST_TINT: Record<string, number> = {
  he: 0xffa64d,
  flash: 0xffffff,
  smoke: 0xb9c2cc,
  fire: 0xff7a2a,
};
/** Beyond this, older effects are dropped rather than queued. */
const MAX_LIVE = 96;

interface Live {
  object: THREE.Object3D;
  material: THREE.Material & { opacity: number };
  age: number;
  life: number;
  /** Starting opacity, so the fade is `base * (1 - t)` and not a compounding decay. */
  base: number;
  scale: number;
}

export class EffectsPool {
  private live: Live[] = [];
  private tracerGeo: THREE.BufferGeometry;
  private impactGeo: THREE.BufferGeometry;
  /** A unit sphere, grown to a blast's real radius. Low-poly on purpose: it is
   * drawn as a wireframe and a dense one reads as a solid ball. */
  private blastGeo: THREE.BufferGeometry;

  constructor(
    private readonly three: typeof THREE,
    private readonly scene: THREE.Scene,
  ) {
    // One geometry per kind, reused: a tracer is a two-point line scaled and
    // rotated into place, so per-shot geometry would be pure garbage collection.
    this.tracerGeo = new three.BufferGeometry().setFromPoints([
      new three.Vector3(0, 0, 0),
      new three.Vector3(0, 0, 1),
    ]);
    this.impactGeo = new three.SphereGeometry(0.16, 6, 4);
    this.blastGeo = new three.SphereGeometry(1, 12, 8);
  }

  /**
   * A shot, in **cube** coordinates — the axes the netcode speaks.
   *
   * Converted here rather than at the call site so every user of this class has
   * one fewer chance to get `three.z = cube.y` backwards.
   */
  /**
   * A grenade going off: a flash of light and a shell of debris.
   *
   * Purely a renderer, like everything else here — the server has already
   * decided who it hurt and how blind it left anyone. What this owns is the half
   * second where the room tells you it happened, and the two cues are chosen so
   * a detonation reads from *outside* its radius too: a light you see reflected,
   * and an expanding shell you can judge the size of.
   *
   * The shell is a wireframe sphere grown to the blast's real `radius` rather
   * than an artistic one. A player has to be able to learn how far an HE
   * reaches, and they can only learn that from something drawn at the distance
   * the damage actually stops.
   */
  blast(at: [number, number, number], radius: number, kind: string): void {
    const three = this.three;
    // Cube (x, y, height) -> three (x, height, z).
    const position = new three.Vector3(at[0], at[2], at[1]);
    const tint = BLAST_TINT[kind] ?? BLAST_TINT.he;

    const shellMat = new three.MeshBasicMaterial({
      color: tint,
      transparent: true,
      opacity: 0.5,
      wireframe: true,
      depthWrite: false,
    });
    const shell = new three.Mesh(this.blastGeo, shellMat);
    shell.position.copy(position);
    // `add` grows an entry from 1 to `scale` over its life, so the shell is a
    // unit sphere told to end up at the blast's real radius — it must not be
    // pre-scaled here or the first frame of the animation would overwrite it.
    this.add(shell, shellMat, BLAST_LIFE, radius);

    // A smoke or a fire has no flash — the cloud it becomes is the event, and a
    // white pop in front of it would read as a second explosion.
    if (kind === 'he' || kind === 'flash') {
      const coreMat = new three.MeshBasicMaterial({
        color: kind === 'flash' ? 0xffffff : 0xffcf7a,
        transparent: true,
        opacity: 0.95,
        depthWrite: false,
      });
      const core = new three.Mesh(this.blastGeo, coreMat);
      core.position.copy(position);
      this.add(core, coreMat, BLAST_LIFE * 0.45, Math.max(1.2, radius * 0.4));
    }
  }

  shot(
    origin: [number, number, number],
    ends: [number, number, number][],
    color: number,
    self: boolean,
  ): void {
    const three = this.three;
    const from = new three.Vector3(origin[0], origin[2], origin[1]);
    for (const end of ends) {
      const to = new three.Vector3(end[0], end[2], end[1]);
      const material = new three.LineBasicMaterial({
        color,
        transparent: true,
        // Our own tracer is drawn faint: it leaves the camera, so a bright one
        // is a line down the middle of the screen and nothing else.
        opacity: self ? 0.35 : 0.8,
      });
      const line = new three.Line(this.tracerGeo, material);
      line.position.copy(from);
      // Explicitly rotating the geometry's +Z onto the shot direction rather
      // than using `lookAt`, whose axis convention differs between cameras and
      // everything else and is the kind of thing that is wrong by 180°.
      const length = from.distanceTo(to);
      if (length > 1e-4) {
        line.quaternion.setFromUnitVectors(
          new three.Vector3(0, 0, 1),
          to.clone().sub(from).divideScalar(length),
        );
      }
      line.scale.set(1, 1, length);
      this.add(line, material, TRACER_LIFE);

      const impactMat = new three.MeshBasicMaterial({
        color: 0xffd9a0,
        transparent: true,
        opacity: 0.9,
      });
      const impact = new three.Mesh(this.impactGeo, impactMat);
      impact.position.copy(to);
      this.add(impact, impactMat, IMPACT_LIFE, 2.4);
    }
  }

  private add(
    object: THREE.Object3D,
    material: THREE.Material & { opacity: number },
    life: number,
    scale = 1,
  ): void {
    this.scene.add(object);
    this.live.push({ object, material, age: 0, life, base: material.opacity, scale });
    while (this.live.length > MAX_LIVE) this.retire(this.live.shift()!);
  }

  /** Age everything by `dt`, fading and retiring as it goes. */
  update(dt: number): void {
    for (let i = this.live.length - 1; i >= 0; i--) {
      const entry = this.live[i];
      entry.age += dt;
      const t = entry.age / entry.life;
      if (t >= 1) {
        this.retire(entry);
        this.live.splice(i, 1);
        continue;
      }
      entry.material.opacity = entry.base * (1 - t);
      if (entry.scale !== 1) {
        const s = 1 + (entry.scale - 1) * t;
        entry.object.scale.setScalar(s);
      }
    }
  }

  private retire(entry: Live): void {
    this.scene.remove(entry.object);
    entry.material.dispose();
  }

  dispose(): void {
    for (const entry of this.live) this.retire(entry);
    this.live = [];
    this.tracerGeo.dispose();
    this.impactGeo.dispose();
    this.blastGeo.dispose();
  }

  get size(): number {
    return this.live.length;
  }
}
