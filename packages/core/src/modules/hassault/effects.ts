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

import type { SurfaceMaterial } from './decals';

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
const MAX_LIVE = 160;

/** What an effect is drawn as: each kind is one geometry and one material setup. */
type Kind = 'tracer' | 'impact' | 'particle' | 'shell' | 'core';

interface Spawned {
  object: THREE.Mesh | THREE.Line;
  material: THREE.MeshBasicMaterial | THREE.LineBasicMaterial;
}

interface Live {
  kind: Kind;
  object: THREE.Object3D;
  material: THREE.Material & { opacity: number };
  age: number;
  life: number;
  /** Starting opacity, so the fade is `base * (1 - t)` and not a compounding decay. */
  base: number;
  scale: number;
  velocity?: THREE.Vector3;
  drag?: number;
  gravity?: number;
}

export class EffectsPool {
  private live: Live[] = [];
  /**
   * Retired effects, by kind, waiting to be reused.
   *
   * An effect used to be a new material and mesh per particle, disposed when it
   * faded. Beyond the garbage, disposing a material releases its program, and
   * three deletes a program the moment nothing uses it: so the first tracer
   * after a quiet spell re-linked its shader, mid-firefight, on the frame it
   * was fired. Reused, a kind's program lives as long as the pool.
   */
  private free: Record<Kind, Spawned[]> = {
    tracer: [],
    impact: [],
    particle: [],
    shell: [],
    core: [],
  };
  private tracerGeo: THREE.BufferGeometry;
  private impactGeo: THREE.BufferGeometry;
  private particleGeo: THREE.BufferGeometry;
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
    this.particleGeo = new three.BoxGeometry(0.045, 0.045, 0.045);
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

    const { object: shell, material: shellMat } = this.spawn('shell', tint, 0.5);
    shell.position.copy(position);
    // `add` grows an entry from 1 to `scale` over its life, so the shell is a
    // unit sphere told to end up at the blast's real radius — it must not be
    // pre-scaled here or the first frame of the animation would overwrite it.
    this.add(shell, shellMat, BLAST_LIFE, radius);

    // A smoke or a fire has no flash — the cloud it becomes is the event, and a
    // white pop in front of it would read as a second explosion.
    if (kind === 'he' || kind === 'flash') {
      const { object: core, material: coreMat } = this.spawn(
        'core',
        kind === 'flash' ? 0xffffff : 0xffcf7a,
        0.95,
      );
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
    // Your own shot draws nothing here. Its endpoint is by definition under the
    // crosshair, so the impact puff — a pale low-poly sphere grown to ~0.4 cubes
    // — landed as a white shape on the reticle every time you fired, and the
    // tracer is a line from your own eye. The muzzle flash and the crosshair kick
    // carry the local shot; this is for seeing where *other* people's fire goes.
    if (self) return;
    for (const end of ends) {
      const to = new three.Vector3(end[0], end[2], end[1]);
      const { object: line, material } = this.spawn('tracer', color, 0.8);
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

      const { object: impact, material: impactMat } = this.spawn('impact', 0xffd9a0, 0.9);
      impact.position.copy(to);
      this.add(impact, impactMat, IMPACT_LIFE, 2.4);
    }
  }

  /** Golden helmet dink spark burst for armored headshots. */
  helmetDink(at: [number, number, number]): void {
    const three = this.three;
    const pos = new three.Vector3(at[0], at[2], at[1]);
    const { object: dinkMesh, material: dinkMat } = this.spawn('impact', 0xffea00, 1.0);
    dinkMesh.position.copy(pos);
    this.add(dinkMesh, dinkMat, 0.22, 4.2);
  }

  /**
   * Surface-aware ballistic impact spatter: dust puffs for stone/concrete, bright ricochet
   * sparks for metal, splinter flecks for wood, and glittering shards for glass.
   */
  impactSpatter(
    at: [number, number, number],
    normal: [number, number, number],
    material: SurfaceMaterial = 'default',
  ): void {
    const three = this.three;
    // Cube [x, y, height] -> three [x, height, z]
    const pos = new three.Vector3(at[0], at[2], at[1]);
    const norm = new three.Vector3(normal[0], normal[2], normal[1]).normalize();

    // Subtle tangent basis for scattering particles outward around normal
    const up = Math.abs(norm.y) < 0.9 ? new three.Vector3(0, 1, 0) : new three.Vector3(1, 0, 0);
    const tangent = new three.Vector3().crossVectors(norm, up).normalize();
    const bitangent = new three.Vector3().crossVectors(norm, tangent).normalize();

    if (material === 'metal') {
      // Hot ricochet sparks: fast, glancing spray with glowing yellow-orange embers
      const count = 5;
      for (let i = 0; i < count; i++) {
        const theta = Math.random() * Math.PI * 2;
        const spread = 0.3 + Math.random() * 0.9;
        const vel = norm
          .clone()
          .multiplyScalar(6 + Math.random() * 8)
          .addScaledVector(tangent, Math.cos(theta) * spread * 10)
          .addScaledVector(bitangent, Math.sin(theta) * spread * 10);

        const sparkColor = Math.random() > 0.3 ? 0xffea55 : 0xffaa22;
        const { object: spark, material: sparkMat } = this.spawn('particle', sparkColor, 1.0);
        spark.position.copy(pos);
        this.add(spark, sparkMat, 0.16 + Math.random() * 0.08, 1.2, vel, 2.0, 32.0);
      }
    } else if (material === 'wood') {
      // Splinter debris
      const count = 4;
      for (let i = 0; i < count; i++) {
        const theta = Math.random() * Math.PI * 2;
        const spread = 0.2 + Math.random() * 0.6;
        const vel = norm
          .clone()
          .multiplyScalar(4 + Math.random() * 5)
          .addScaledVector(tangent, Math.cos(theta) * spread * 6)
          .addScaledVector(bitangent, Math.sin(theta) * spread * 6);

        const woodColor = Math.random() > 0.5 ? 0x8a5a2e : 0xbfa073;
        const { object: splinter, material: woodMat } = this.spawn('particle', woodColor, 0.9);
        splinter.position.copy(pos);
        this.add(splinter, woodMat, 0.22 + Math.random() * 0.08, 1.0, vel, 4.0, 22.0);
      }
    } else if (material === 'glass') {
      // Crystalline glass shards
      const count = 5;
      for (let i = 0; i < count; i++) {
        const vel = norm
          .clone()
          .multiplyScalar(5 + Math.random() * 7)
          .addScaledVector(tangent, (Math.random() - 0.5) * 8)
          .addScaledVector(bitangent, (Math.random() - 0.5) * 8);

        const { object: shard, material: glassMat } = this.spawn('particle', 0xe0f4fc, 0.95);
        shard.position.copy(pos);
        this.add(shard, glassMat, 0.18 + Math.random() * 0.06, 0.9, vel, 3.0, 25.0);
      }
    } else {
      // Concrete / plaster / stone: dust puff
      const count = 4;
      for (let i = 0; i < count; i++) {
        const theta = Math.random() * Math.PI * 2;
        const spread = 0.2 + Math.random() * 0.7;
        const vel = norm
          .clone()
          .multiplyScalar(2.5 + Math.random() * 3.5)
          .addScaledVector(tangent, Math.cos(theta) * spread * 4)
          .addScaledVector(bitangent, Math.sin(theta) * spread * 4);

        const dustColor = Math.random() > 0.5 ? 0xcccccc : 0xa6a6a6;
        const { object: dust, material: dustMat } = this.spawn('particle', dustColor, 0.85);
        dust.position.copy(pos);
        this.add(dust, dustMat, 0.24 + Math.random() * 0.08, 1.8, vel, 9.0, 8.0);
      }
    }
  }

  /**
   * Dramatic crystalline glass shatter burst when a breakable window is breached.
   */
  glassShatter(at: [number, number, number], normal?: [number, number, number]): void {
    const three = this.three;
    const pos = new three.Vector3(at[0], at[2], at[1]);
    const norm = normal
      ? new three.Vector3(normal[0], normal[2], normal[1]).normalize()
      : new three.Vector3(0, 0, 1);

    const helper = Math.abs(norm.y) < 0.9 ? new three.Vector3(0, 1, 0) : new three.Vector3(1, 0, 0);
    const tangent = new three.Vector3().crossVectors(norm, helper).normalize();
    const bitangent = new three.Vector3().crossVectors(norm, tangent).normalize();

    const shardCount = 28;
    for (let i = 0; i < shardCount; i++) {
      const outDir = Math.random() > 0.4 ? 1 : -1;
      const speed = 4.0 + Math.random() * 8.5;
      const vel = norm
        .clone()
        .multiplyScalar(outDir * (2.0 + Math.random() * 5.0))
        .addScaledVector(tangent, (Math.random() - 0.5) * speed)
        .addScaledVector(bitangent, (Math.random() - 0.5) * speed + 2.5);

      const glassColor = Math.random() > 0.5 ? 0xeaf7ff : 0xd2edfc;
      const { object: shard, material: glassMat } = this.spawn('particle', glassColor, 0.92);
      shard.position.copy(pos).addScaledVector(vel, 0.02);
      const scale = 0.8 + Math.random() * 1.6;
      this.add(shard, glassMat, 0.35 + Math.random() * 0.25, scale, vel, 2.5, 24.0);
    }
  }

  /** A fresh-looking effect of `kind`: reused if one is free, built if not. */
  private spawn(kind: Kind, color: number, opacity: number): Spawned {
    const three = this.three;
    let entry = this.free[kind].pop();
    if (!entry) {
      if (kind === 'tracer') {
        const material = new three.LineBasicMaterial({ transparent: true });
        entry = { object: new three.Line(this.tracerGeo, material), material };
      } else {
        const blast = kind === 'shell' || kind === 'core';
        const material = new three.MeshBasicMaterial({
          transparent: true,
          wireframe: kind === 'shell',
          depthWrite: !blast,
        });
        const geometry = kind === 'impact' ? this.impactGeo : blast ? this.blastGeo : this.particleGeo;
        entry = { object: new three.Mesh(geometry, material), material };
      }
      entry.object.userData.effectKind = kind;
    }
    entry.material.color.setHex(color);
    entry.material.opacity = opacity;
    entry.object.position.set(0, 0, 0);
    entry.object.quaternion.identity();
    entry.object.scale.set(1, 1, 1);
    return entry;
  }

  private add(
    object: THREE.Object3D,
    material: THREE.Material & { opacity: number },
    life: number,
    scale = 1,
    velocity?: THREE.Vector3,
    drag?: number,
    gravity?: number,
  ): void {
    this.scene.add(object);
    this.live.push({
      kind: object.userData.effectKind as Kind,
      object,
      material,
      age: 0,
      life,
      base: material.opacity,
      scale,
      velocity,
      drag,
      gravity,
    });
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
      if (entry.velocity) {
        entry.object.position.addScaledVector(entry.velocity, dt);
        if (entry.gravity) entry.velocity.y -= entry.gravity * dt;
        if (entry.drag) entry.velocity.multiplyScalar(Math.max(0, 1 - entry.drag * dt));
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
    this.free[entry.kind].push({
      object: entry.object as Spawned['object'],
      material: entry.material as Spawned['material'],
    });
  }

  dispose(): void {
    for (const entry of this.live) this.retire(entry);
    this.live = [];
    for (const list of Object.values(this.free)) {
      for (const { material } of list) material.dispose();
      list.length = 0;
    }
    this.tracerGeo.dispose();
    this.impactGeo.dispose();
    this.particleGeo.dispose();
    this.blastGeo.dispose();
  }

  get size(): number {
    return this.live.length;
  }
}
