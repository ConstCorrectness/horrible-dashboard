/**
 * A map's light rig, sky, fog and point lights — the browser's half.
 *
 * The numbers are served (`backend/modules/hassault/atmosphere.py`) and resolved
 * there, so the pane, the standalone web build and the native window draw one set
 * of them. They used to be literals in `HorribleAssaultPanel.tsx`, which lit every
 * map identically — a desert at noon and an office at night under one sun.
 *
 * What stays here is a **fallback** for a node older than the field, and it is
 * the server's own default for the map's format; `apps/native-fps/tests/
 * browser_parity.rs` reads both copies and the Python and holds them together.
 *
 * Pure apart from `createSky`, which takes three as a parameter: the rest is
 * testable with no WebGL context, like `world.ts` and `geometry.ts`.
 */
import type * as THREE from 'three';

import type { Atmosphere, MapInfo, MapLight } from './api';

/** `atmosphere.CUBE_DEFAULT`: the rig the pane always lit cube maps with. */
export const CUBE_DEFAULT: Atmosphere = {
  skyZenith: 0x11161f,
  skyHorizon: 0x11161f,
  hemiSky: 0xbfd4ff,
  hemiGround: 0x33302c,
  hemiIntensity: 1.55,
  sunColor: 0xfff2dd,
  sunIntensity: 1.75,
  sunDir: [0.55, 0.82, 0.36],
  fillColor: 0x9fb6ff,
  fillIntensity: 0.45,
  fillDir: [-0.5, 0.35, -0.7],
  fogColor: 0x11161f,
  fogDensity: 0.0055,
  exposure: 1.15,
  sunDisc: false,
};

/** `atmosphere.GLTF_DEFAULT`: daylight, for a modelled map open to the sky. */
export const GLTF_DEFAULT: Atmosphere = {
  ...CUBE_DEFAULT,
  skyZenith: 0x3f74c8,
  skyHorizon: 0x9cbde8,
  fogColor: 0x9cbde8,
  fogDensity: 0.001,
  sunDisc: true,
};

/** What to draw a map with: the served block, or its format's default. */
export function resolveAtmosphere(info: Pick<MapInfo, 'atmosphere' | 'format'>): Atmosphere {
  if (info.atmosphere) return info.atmosphere;
  return info.format === 'gltf' ? GLTF_DEFAULT : CUBE_DEFAULT;
}

/**
 * A light's intensity in candela: `intensity × radius / 2`.
 *
 * Mirrored by `candela` in `atmosphere.rs`. Linear in the radius on purpose: the
 * first convention was `(r/2)^2`, and under inverse-square falloff that made a
 * wide lamp a white blowout on the wall beside it. A wider light still reaches
 * further and burns brighter, only not quadratically so.
 */
export function candela(light: MapLight): number {
  return light.intensity * light.radius * 0.5;
}

/**
 * three's `getDistanceAttenuation` for `decay = 2` — what a `PointLight` with
 * `distance = radius` does in the shader, written out so the native port can be
 * pinned against it.
 */
export function pointAttenuation(distance: number, range: number): number {
  const falloff = 1 / Math.max(distance * distance, 0.01);
  const ratio = distance / range;
  const window = Math.min(Math.max(1 - ratio * ratio * ratio * ratio, 0), 1);
  return falloff * window * window;
}

/** A map light in three space (y up), where every placement goes: `(x, z, y)`. */
export function lightPosition(light: MapLight): [number, number, number] {
  return [light.x, light.z, light.y];
}

/**
 * The `count` lights whose reach comes nearest `eye` — distance less range — the
 * same order the native client keeps. A big lamp across the room outranks a
 * small one just behind you that lights nothing you can see.
 */
export function nearestLights(
  lights: readonly MapLight[],
  eye: readonly [number, number, number],
  count: number,
): MapLight[] {
  if (count <= 0 || lights.length === 0) return [];
  const scored = lights
    .filter((l) => l.radius > 0 && l.intensity > 0)
    .map((l) => {
      const [x, y, z] = lightPosition(l);
      const d = Math.hypot(x - eye[0], y - eye[1], z - eye[2]);
      return { l, score: d - l.radius };
    });
  scored.sort((a, b) => a.score - b.score);
  return scored.slice(0, count).map((s) => s.l);
}

/** A unit vector from a served direction, defaulting to straight up. */
export function unit(d: readonly [number, number, number]): [number, number, number] {
  const len = Math.hypot(d[0], d[1], d[2]);
  return len > 1e-6 ? [d[0] / len, d[1] / len, d[2] / len] : [0, 1, 0];
}

/**
 * The sky dome: a large inward-facing sphere whose shader is `sky_radiance` from
 * `lighting.wgsl.inc`, line for line, so the two clients paint the same sky.
 *
 * Follows the camera (its position is set to the eye each frame) and ignores
 * depth and fog: it is the backdrop, drawn first, and anything in front of it
 * simply lands on top.
 */
export function createSky(three: typeof THREE, atmosphere: Atmosphere): THREE.Mesh {
  const color = (hex: number) => new three.Color(hex);
  const sun = unit(atmosphere.sunDir);
  const material = new three.ShaderMaterial({
    uniforms: {
      zenith: { value: color(atmosphere.skyZenith) },
      horizon: { value: color(atmosphere.skyHorizon) },
      sunColor: { value: color(atmosphere.sunColor).multiplyScalar(atmosphere.sunIntensity) },
      sunDir: { value: new three.Vector3(sun[0], sun[1], sun[2]) },
      sunDisc: { value: atmosphere.sunDisc ? 1 : 0 },
    },
    vertexShader: /* glsl */ `
      varying vec3 vDirection;
      void main() {
        vDirection = position;
        vec4 p = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        // On the far plane, so nothing the camera can see is ever behind it.
        gl_Position = p.xyww;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform vec3 zenith;
      uniform vec3 horizon;
      uniform vec3 sunColor;
      uniform vec3 sunDir;
      uniform float sunDisc;
      varying vec3 vDirection;
      void main() {
        vec3 d = normalize(vDirection);
        float up = clamp(d.y, 0.0, 1.0);
        vec3 color = mix(horizon, zenith, sqrt(up));
        if (sunDisc > 0.5) {
          float s = max(dot(d, sunDir), 0.0);
          color += sunColor * (pow(s, 1800.0) * 6.0 + pow(s, 24.0) * 0.12);
        }
        gl_FragColor = vec4(color, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }
    `,
    side: three.BackSide,
    depthWrite: false,
    depthTest: false,
    fog: false,
  });
  // three's `ShaderMaterial` skips the renderer's tone curve unless asked; the
  // sky is radiance like everything else and has to go through the same one.
  material.toneMapped = true;
  const mesh = new three.Mesh(new three.SphereGeometry(1, 32, 16), material);
  mesh.scale.setScalar(500);
  mesh.renderOrder = -1000;
  mesh.frustumCulled = false;
  mesh.name = 'hassault-sky';
  return mesh;
}
