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

/**
 * How a cloud's opacity moves, in seconds and fractions. The native client
 * carries the same numbers (`nades.rs`) and `browser_parity.rs` pins the two
 * together: a smoke that thins sooner on one client is a smoke one player can
 * see through first.
 */
export const SMOKE_BLOOM = 0.65;
export const SMOKE_FADE = 1.6;
/** Opacity left while an HE blast has the cloud open. */
export const SMOKE_CLEARED = 0.08;
/**
 * A bullet tunnel's radius in cubes and its life in seconds — the server's
 * (`match.py`, the channel append in `_fire`). Used for a shot this client
 * punches before the server's channel arrives; the wire's own radius wins.
 */
export const SMOKE_CHANNEL_RADIUS = 1.2;
export const SMOKE_CHANNEL_LIFE = 0.8;

/** A cloud's opacity, from its age, its time left and whether it is cleared. */
export function smokeOpacity(age: number, left: number, cleared: boolean): number {
  const bloom = Math.min(1, Math.max(0, age) / SMOKE_BLOOM);
  const fade = Math.min(1, Math.max(0, left) / SMOKE_FADE);
  return Math.max(0, Math.min(1, bloom * fade * (cleared ? SMOKE_CLEARED : 1)));
}

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

/** A bullet tunnel, in **three** world coordinates (x, height, z) and cubes. */
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
  /** Seconds left of an HE blast's clearing, counted down locally. */
  clearedFor: number;
  channels: SmokeChannel[];
  /** The lobes: children of `mesh`, sharing its material. */
  lobes: THREE.Mesh[];
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
      live.clearedFor = row.clearedFor ?? 0;
      live.mesh.position.set(row.x, row.z, row.y);
      live.mesh.scale.setScalar(row.r);
      if (live.lobes.length === 0 && row.nodes && row.nodes.length > 0 && row.r > 0.01) {
        this.addLobes(live, row);
      }

      if (row.channels && live.kind === 'smoke') {
        for (const [cx, cy, cz, remaining, radius] of row.channels) {
          // Cube (x, y, height) → three (x, height, z).
          const [wx, wy, wz] = [cx, cz, cy];
          // A tunnel this client already punched for its own shot is the same
          // tunnel arriving from the server: adopt it rather than draw two.
          const mine = live.channels.find((c) => Math.hypot(c.x - wx, c.y - wy, c.z - wz) < 0.5);
          const fresh = {
            x: wx,
            y: wy,
            z: wz,
            age: Math.max(0, SMOKE_CHANNEL_LIFE - remaining),
            life: SMOKE_CHANNEL_LIFE,
            radius: radius ?? SMOKE_CHANNEL_RADIUS,
          };
          if (mine) Object.assign(mine, fresh);
          else live.channels.push(fresh);
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
        // Pierced: the tunnel sits where the shot passed nearest the centre,
        // which is where the server puts it too (`_fire`).
        live.channels.push({
          x: px,
          y: pz,
          z: py,
          age: 0,
          life: SMOKE_CHANNEL_LIFE,
          radius: SMOKE_CHANNEL_RADIUS,
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
    for (const live of this.zones.values()) {
      live.material.uniforms.uTime.value = this.elapsed;
      // Counted down here between snapshots: the server sends seconds left, and
      // a 20 Hz step in a cloud's opacity reads as flicker.
      live.left = Math.max(0, live.left - dt);
      live.clearedFor = Math.max(0, live.clearedFor - dt);
      // Clouds bloom in over their first moment and thin out at the end, rather
      // than appearing and vanishing at full density — the two instants where a
      // hard cut would read as the effect glitching rather than expiring.
      const isCleared = live.kind === 'smoke' && live.clearedFor > 0;
      live.material.uniforms.uOpacity.value = smokeOpacity(
        live.duration - live.left,
        live.left,
        isCleared,
      );

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
        varying vec3 vWorld;
        void main() {
          vLocal = position;
          vWorld = (modelMatrix * vec4(position, 1.0)).xyz;
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
        varying vec3 vWorld;

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

            // Bullet tunnels, carved where **the view ray** passes within a
            // tunnel's radius of its centre — the server's question for a line
            // of sight (\`grenades.sight_blocked_by\`), so the hole you see is
            // the hole a bot sees through. The native client's \`smoke_tunnel\`
            // is the same function. Testing the surface point instead, as this
            // used to, only ever opened a hole for a shot grazing the edge.
            float channelVoid = 0.0;
            vec3 rayDir = normalize(vWorld - cameraPosition);
            for (int i = 0; i < 8; i++) {
              if (i >= uChannelCount) break;
              vec4 ch = uChannels[i];
              if (ch.w > 0.001) {
                float t = max(dot(ch.xyz - cameraPosition, rayDir), 0.0);
                float d = length(ch.xyz - (cameraPosition + rayDir * t));
                channelVoid = max(channelVoid, smoothstep(ch.w, ch.w * 0.25, d));
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
      clearedFor: row.clearedFor ?? 0,
      channels: [],
      lobes: [],
    };
    this.zones.set(row.id, live);
    return live;
  }

  /**
   * The cloud's lobes, as children of its sphere sharing its material.
   *
   * They are part of what blocks sight on the server (`Zone.spheres`), so they
   * are part of what is drawn: an edge of smoke that stops a bot must be an edge
   * of smoke a player can see. In the parent's unit space, so they follow its
   * scale and opacity for free.
   */
  private addLobes(live: LiveZone, row: ZoneRow): void {
    for (const node of row.nodes ?? []) {
      // Cube (x, y, height) → three (x, height, z), relative and unit-scaled.
      const lobe = new this.three.Mesh(this.zoneGeo, live.material);
      lobe.position.set((node.x - row.x) / row.r, (node.z - row.z) / row.r, (node.y - row.y) / row.r);
      lobe.scale.setScalar(node.r / row.r);
      lobe.renderOrder = live.mesh.renderOrder;
      lobe.castShadow = false;
      lobe.receiveShadow = false;
      live.mesh.add(lobe);
      live.lobes.push(lobe);
    }
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
        // Standing in a tunnel thins the shroud the same way looking down one
        // thins the cloud.
        let channelVoid = 0;
        for (const ch of live.channels) {
          const fade = Math.max(0, 1 - ch.age / ch.life);
          const radius = ch.radius * fade;
          const chDist = Math.hypot(x - ch.x, y - ch.y, z - ch.z);
          if (radius > 0 && chDist < radius) {
            channelVoid = Math.max(channelVoid, 1 - chDist / radius);
          }
        }
        const t = 1 - dist / r;
        const opacity = smokeOpacity(live.duration - live.left, live.left, live.clearedFor > 0);
        const density = Math.min(1, t * 1.6) * opacity * (1.0 - channelVoid * 0.9);
        if (density > maxDensity) maxDensity = density;
      }
    }
    return maxDensity;
  }
}
