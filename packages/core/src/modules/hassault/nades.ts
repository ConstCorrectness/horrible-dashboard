/**
 * Drawing thrown utility: the grenade in the air, and what it leaves behind.
 *
 * Everything here is a **renderer for something the server already decided**.
 * The projectile's position, the cloud's centre and radius, and how long either
 * has left all arrive in the snapshot; nothing in this file simulates, predicts
 * or decides. That is the same contract `effects.ts` has for tracers, and it
 * exists for the same reason: a client-side arc would be a second implementation
 * of the bounce whose only job is to occasionally disagree with the first.
 *
 * Positions are interpolated between snapshots rather than drawn raw. At 20 Hz a
 * grenade travelling 34 cubes a second moves nearly two cubes between packets,
 * and drawn without interpolation it strobes across the room. Interpolation is
 * cheap here precisely *because* nothing is predicted: there is no correction to
 * fight with, so a plain lerp toward the newest position is exact within a tick.
 *
 * ### Smoke, and why it is not a particle system
 *
 * A cloud is a **sphere the same size as the one the server is testing against**
 * (`grenades.sight_blocked_by`), drawn with a noise shader on the inside as well
 * as the outside so walking into one fills the screen. A particle billboard
 * cloud looks better in a screenshot and is a lie in a firefight: its visual
 * edge is nowhere near the volume that actually blocks sight, so players learn a
 * shape that does not match the rule. Matching the server's own volume is worth
 * more than a prettier edge.
 *
 * Takes the three namespace as a parameter rather than importing it, so this
 * file never pulls three into the bundle — the same contract as `avatars.ts`,
 * `effects.ts`, `backdrop.ts` and `surfaces.ts`.
 */
import type * as THREE from 'three';

import type { NadeRow, ZoneRow } from './net';

/** How fast a drawn grenade converges on the position the server last sent. */
const FOLLOW = 18;

/** Colour per kind. The grenade body, and the light it throws when it goes off. */
const TINT: Record<string, number> = {
  he: 0x4d5a3f,
  flash: 0xb8b8c0,
  smoke: 0x3f5160,
  fire: 0x7a3a22,
};

const ZONE_TINT: Record<string, number> = {
  smoke: 0xb9c2cc,
  fire: 0xff6a2a,
};

interface LiveNade {
  group: THREE.Group;
  body: THREE.Mesh;
  /** The blinking fuse light, which is how you read the time left on the floor. */
  light: THREE.Mesh;
  kind: string;
  /** Where it is drawn, chasing `target`. */
  x: number;
  y: number;
  z: number;
  target: { x: number; y: number; z: number };
  fuse: number;
}

interface SmokeChannel {
  x: number;
  y: number;
  z: number;
  age: number;
  life: number;
  radius: number;
}

interface LiveZone {
  mesh: THREE.Mesh;
  material: THREE.ShaderMaterial;
  kind: string;
  /** Seconds left when last told, for the fade-out. */
  left: number;
  duration: number;
  clearedUntil?: number;
  channels: SmokeChannel[];
}

export class NadePool {
  private nades = new Map<string, LiveNade>();
  private zones = new Map<string, LiveZone>();
  private readonly bodyGeo: THREE.SphereGeometry;
  private readonly lightGeo: THREE.SphereGeometry;
  private readonly zoneGeo: THREE.SphereGeometry;
  private readonly bodyMats = new Map<string, THREE.MeshLambertMaterial>();
  private readonly lightMat: THREE.MeshBasicMaterial;
  private elapsed = 0;

  constructor(
    private readonly three: typeof THREE,
    private readonly scene: THREE.Scene,
  ) {
    // A grenade is a few centimetres across in a world where a cube is ~36cm,
    // so it is drawn deliberately larger than life: at true scale it is a pixel
    // and the thing you most need to see is where it landed.
    this.bodyGeo = new three.SphereGeometry(0.32, 10, 8);
    this.lightGeo = new three.SphereGeometry(0.12, 6, 5);
    this.zoneGeo = new three.SphereGeometry(1, 24, 16);
    this.lightMat = new three.MeshBasicMaterial({ color: 0xff3b30 });
    for (const [kind, color] of Object.entries(TINT)) {
      this.bodyMats.set(kind, new three.MeshLambertMaterial({ color }));
    }
  }

  /**
   * Reconcile with the snapshot: add what is new, drop what is gone.
   *
   * Keyed by the server's id rather than by array position, which is what makes
   * interpolation possible at all — a grenade that changed index between
   * snapshots would otherwise be drawn flying to another grenade's position.
   */
  sync(nades: NadeRow[] | undefined, zones: ZoneRow[] | undefined): void {
    const seenNades = new Set<string>();
    for (const row of nades ?? []) {
      seenNades.add(row.id);
      let live = this.nades.get(row.id);
      if (!live) live = this.createNade(row);
      // Cube (x, y, height) → three (x, height, z), the same mapping the world
      // mesh uses.
      live.target = { x: row.x, y: row.z, z: row.y };
      live.fuse = row.fuse;
    }
    for (const [id, live] of this.nades) {
      if (seenNades.has(id)) continue;
      this.scene.remove(live.group);
      this.nades.delete(id);
    }

    const seenZones = new Set<string>();
    for (const row of zones ?? []) {
      seenZones.add(row.id);
      let live = this.zones.get(row.id);
      if (!live) live = this.createZone(row);
      live.left = row.left;
      live.duration = row.duration;
      live.clearedUntil = row.cleared;
      live.mesh.position.set(row.x, row.z, row.y);
      live.mesh.scale.setScalar(row.r);

      if (row.channels && live.kind === 'smoke') {
        for (const [cx, cy, cz, remaining] of row.channels) {
          const r = row.r;
          if (r > 0.01) {
            const lx = (cx - row.x) / r;
            const ly = (cz - row.z) / r;
            const lz = (cy - row.y) / r;
            const exists = live.channels.some(
              (c) => Math.hypot(c.x - lx, c.y - ly, c.z - lz) < 0.15,
            );
            if (!exists) {
              live.channels.push({
                x: lx,
                y: ly,
                z: lz,
                age: Math.max(0, 0.8 - remaining),
                life: 0.8,
                radius: 0.26,
              });
            }
          }
        }
      }
    }
    for (const [id, live] of this.zones) {
      if (seenZones.has(id)) continue;
      this.scene.remove(live.mesh);
      live.material.dispose();
      this.zones.delete(id);
    }
  }

  /**
   * Punch temporary bullet visibility tunnels through any active smoke clouds pierced by a shot.
   * CS2 mechanic: enables tactical peek sightlines when spraying or sniping through smoke.
   */
  punchSmoke(origin: [number, number, number], end: [number, number, number]): void {
    for (const live of this.zones.values()) {
      if (live.kind !== 'smoke' || live.left <= 0) continue;
      const r = live.mesh.scale.x;
      if (r <= 0.01) continue;

      // Mesh position is Three (cx, cz, cy) = (x, y, z) in cube coords
      const zx = live.mesh.position.x;
      const zy = live.mesh.position.z;
      const zz = live.mesh.position.y;

      const vx = end[0] - origin[0];
      const vy = end[1] - origin[1];
      const vz = end[2] - origin[2];
      const segSq = vx * vx + vy * vy + vz * vz;
      if (segSq < 1e-6) continue;

      const wx = zx - origin[0];
      const wy = zy - origin[1];
      const wz = zz - origin[2];
      const t = Math.max(0, Math.min(1, (wx * vx + wy * vy + wz * vz) / segSq));
      const px = origin[0] + t * vx;
      const py = origin[1] + t * vy;
      const pz = origin[2] + t * vz;

      const distSq = (px - zx) ** 2 + (py - zy) ** 2 + (pz - zz) ** 2;
      if (distSq <= r * r) {
        // Pierced the smoke! Local coordinates on the unit sphere
        const lx = (px - zx) / r;
        const ly = (pz - zz) / r;
        const lz = (py - zy) / r;
        live.channels.push({
          x: lx,
          y: ly,
          z: lz,
          age: 0,
          life: 0.8,
          radius: 0.28,
        });
        if (live.channels.length > 8) {
          live.channels.shift();
        }
      }
    }
  }

  /** Advance the drawing. `dt` in seconds. */
  update(dt: number): void {
    this.elapsed += dt;
    const follow = Math.min(1, dt * FOLLOW);
    for (const live of this.nades.values()) {
      live.x += (live.target.x - live.x) * follow;
      live.y += (live.target.y - live.y) * follow;
      live.z += (live.target.z - live.z) * follow;
      live.group.position.set(live.x, live.y, live.z);
      // Tumbling, so a grenade reads as thrown rather than as floating. Purely
      // cosmetic — the server has no idea which way up it is.
      live.group.rotation.x += dt * 7;
      live.group.rotation.z += dt * 4;
      // The fuse light blinks faster as the time runs out, which is the one cue
      // that tells you whether to run. Cheap, and it works in peripheral vision.
      const rate = live.fuse > 0 ? 2 + 10 / Math.max(0.25, live.fuse) : 24;
      live.light.visible = Math.sin(this.elapsed * rate) > 0;
    }
    const nowSec = Date.now() / 1000;
    for (const live of this.zones.values()) {
      live.material.uniforms.uTime.value = this.elapsed;
      // Clouds bloom in over their first moment and thin out at the end, rather
      // than appearing and vanishing at full density — the two instants where a
      // hard cut would read as the effect glitching rather than expiring.
      const age = live.duration - live.left;
      const bloom = Math.min(1, age / 0.65);
      const fade = Math.min(1, live.left / 1.6);
      const isCleared = live.kind === 'smoke' && live.clearedUntil !== undefined && live.clearedUntil > nowSec;
      const clearFactor = isCleared ? 0.08 : 1.0;
      live.material.uniforms.uOpacity.value = Math.max(0, Math.min(1, bloom * fade * clearFactor));

      if (live.kind === 'smoke' && live.channels.length > 0) {
        for (let i = live.channels.length - 1; i >= 0; i--) {
          const ch = live.channels[i];
          ch.age += dt;
          if (ch.age >= ch.life) {
            live.channels.splice(i, 1);
          }
        }
        const uChannels = live.material.uniforms.uChannels.value as THREE.Vector4[];
        const count = Math.min(8, live.channels.length);
        for (let i = 0; i < count; i++) {
          const ch = live.channels[i];
          const fadeAmount = Math.max(0, 1.0 - ch.age / ch.life);
          uChannels[i].set(ch.x, ch.y, ch.z, fadeAmount * ch.radius);
        }
        live.material.uniforms.uChannelCount.value = count;
      }
    }
  }

  dispose(): void {
    for (const live of this.nades.values()) this.scene.remove(live.group);
    for (const live of this.zones.values()) {
      this.scene.remove(live.mesh);
      live.material.dispose();
    }
    this.nades.clear();
    this.zones.clear();
    this.bodyGeo.dispose();
    this.lightGeo.dispose();
    this.zoneGeo.dispose();
    this.lightMat.dispose();
    for (const mat of this.bodyMats.values()) mat.dispose();
  }

  private createNade(row: NadeRow): LiveNade {
    const three = this.three;
    const group = new three.Group();
    const material = this.bodyMats.get(row.kind) ?? this.bodyMats.get('he')!;
    const body = new three.Mesh(this.bodyGeo, material);
    // Squashed into a canister rather than a ball: at this size a sphere reads
    // as a dropped item, and the four kinds have to be told apart at a glance.
    body.scale.set(0.75, 1.25, 0.75);
    group.add(body);
    const light = new three.Mesh(this.lightGeo, this.lightMat);
    light.position.y = 0.34;
    group.add(light);
    group.position.set(row.x, row.z, row.y);
    this.scene.add(group);
    const live: LiveNade = {
      group,
      body,
      light,
      kind: row.kind,
      x: row.x,
      y: row.z,
      z: row.y,
      target: { x: row.x, y: row.z, z: row.y },
      fuse: row.fuse,
    };
    this.nades.set(row.id, live);
    return live;
  }

  /**
   * A cloud, as a sphere with a noise shader on both faces.
   *
   * `side: DoubleSide` and `depthWrite: false` are the two settings that decide
   * whether walking into a smoke works: with front faces only, stepping inside
   * puts the camera past the geometry and the cloud disappears exactly when it
   * should be blinding you.
   */
  private createZone(row: ZoneRow): LiveZone {
    const three = this.three;
    const fire = row.kind === 'fire';
    const material = new three.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      side: three.DoubleSide,
      uniforms: {
        uTime: { value: 0 },
        uOpacity: { value: 0 },
        uColor: { value: new three.Color(ZONE_TINT[row.kind] ?? 0xffffff) },
        uFire: { value: fire ? 1 : 0 },
        uChannels: {
          value: Array.from({ length: 8 }, () => new three.Vector4(0, 0, 0, 0)),
        },
        uChannelCount: { value: 0 },
      },
      vertexShader: `
        varying vec3 vViewNormal;
        varying vec3 vViewDir;
        varying vec3 vLocal;
        void main() {
          vLocal = position;
          vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
          vViewNormal = normalize(normalMatrix * normal);
          vViewDir = normalize(-mvPosition.xyz);
          gl_Position = projectionMatrix * mvPosition;
        }
      `,
      fragmentShader: `
        uniform float uTime;
        uniform float uOpacity;
        uniform vec3 uColor;
        uniform float uFire;
        uniform vec4 uChannels[8];
        uniform int uChannelCount;
        varying vec3 vViewNormal;
        varying vec3 vViewDir;
        varying vec3 vLocal;

        // Value noise, the same shape as the surface detail's — a hashed lattice
        // read at three frequencies. Procedural because there is no texture to
        // ship and nobody else's smoke to borrow.
        float hash(vec3 p) {
          return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453123);
        }
        float noise(vec3 p) {
          vec3 i = floor(p);
          vec3 f = fract(p);
          f = f * f * (3.0 - 2.0 * f);
          float n = mix(
            mix(mix(hash(i), hash(i + vec3(1,0,0)), f.x),
                mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
            mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x),
                mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y),
            f.z);
          return n;
        }

        void main() {
          // Drifting upward, faster for fire than for smoke.
          vec3 p = vLocal * 2.2 - vec3(0.0, uTime * (uFire > 0.5 ? 1.6 : 0.35), 0.0);
          float n = noise(p) * 0.55 + noise(p * 2.3) * 0.3 + noise(p * 5.1) * 0.15;

          // View-ray chord thickness through spherical volume along the view line
          float chord = clamp(abs(dot(normalize(vViewNormal), normalize(vViewDir))), 0.0, 1.0);

          float density;
          if (uFire > 0.5) {
            density = clamp(chord * 1.3 + n * 0.5 - 0.2, 0.0, 1.0);
          } else {
            // CS2-style volumetric smoke:
            // Core area (chord > 0.38) is completely opaque (alpha = 1.0),
            // strictly preventing anything behind it from being seen.
            // Opacity smoothly rolls off toward the soft boundary edge.
            float coreDensity = smoothstep(0.04, 0.42, chord);
            density = clamp(coreDensity * 1.3 + (n - 0.5) * 0.35, 0.0, 1.0);
            if (chord > 0.38) {
              density = 1.0;
            }

            // CS2 Bullet smoke channels: holes punched through volumetric smoke by high-velocity rounds
            float channelVoid = 0.0;
            for (int i = 0; i < 8; i++) {
              if (i >= uChannelCount) break;
              vec4 ch = uChannels[i];
              if (ch.w > 0.001) {
                float d = length(vLocal - ch.xyz);
                if (d < ch.w) {
                  float hole = smoothstep(ch.w, ch.w * 0.25, d);
                  channelVoid = max(channelVoid, hole);
                }
              }
            }
            density *= (1.0 - channelVoid * 0.95);
          }

          vec3 color = uColor;
          if (uFire > 0.5) {
            // Fire is hotter at its base and where the noise is thickest.
            color = mix(vec3(0.75, 0.12, 0.02), vec3(1.0, 0.85, 0.25), density * 0.9);
          } else {
            // Volumetric smoke micro-shading: realistic self-shadowing and highlights
            color = mix(uColor * 0.82, uColor * 1.14, n * 0.7 + chord * 0.3);
          }

          float alpha = density * uOpacity * (uFire > 0.5 ? 0.75 : 1.0);
          if (alpha < 0.01) discard;
          gl_FragColor = vec4(color, alpha);
        }
      `,
    });
    const mesh = new three.Mesh(this.zoneGeo, material);
    mesh.position.set(row.x, row.z, row.y);
    mesh.scale.setScalar(row.r);
    // Drawn after the world so it blends over it, and never into the shadow map.
    mesh.renderOrder = 3;
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    this.scene.add(mesh);
    const live: LiveZone = {
      mesh,
      material,
      kind: row.kind,
      left: row.left,
      duration: row.duration,
      channels: [],
    };
    this.zones.set(row.id, live);
    return live;
  }

  /**
   * How deeply the camera is submerged inside an active smoke cloud (0.0 = clear, 1.0 = thick core).
   * Used to render the first-person smoke blindness shroud.
   */
  smokeDensityAt(x: number, y: number, z: number): number {
    let maxDensity = 0;
    for (const live of this.zones.values()) {
      if (live.kind !== 'smoke' || live.left <= 0) continue;
      const dx = live.mesh.position.x - x;
      const dy = live.mesh.position.y - y;
      const dz = live.mesh.position.z - z;
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
      const r = live.mesh.scale.x;
      if (dist < r) {
        let channelVoid = 0;
        if (live.channels.length > 0) {
          const lx = dx / r;
          const ly = dy / r;
          const lz = dz / r;
          for (const ch of live.channels) {
            const chDist = Math.hypot(lx - ch.x, ly - ch.y, lz - ch.z);
            if (chDist < ch.radius) {
              const fade = Math.max(0, 1 - ch.age / ch.life);
              channelVoid = Math.max(channelVoid, (1 - chDist / ch.radius) * fade);
            }
          }
        }
        const t = 1 - dist / r;
        const bloom = Math.min(1, (live.duration - live.left) / 0.65);
        const fade = Math.min(1, live.left / 1.6);
        const density = Math.min(1, t * 1.6) * bloom * fade * (1.0 - channelVoid * 0.9);
        if (density > maxDensity) maxDensity = density;
      }
    }
    return maxDensity;
  }
}
