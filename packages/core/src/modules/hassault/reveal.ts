/**
 * The map assembling itself.
 *
 * The loading animation *is* the level: cubes rise into place along a front that
 * sweeps outward from the middle of the map, lit at the frontier and settling into
 * their normal shading behind it. Driven by the real load progress, so what you're
 * watching is the thing you're waiting for rather than a spinner beside it.
 *
 * Two decisions worth keeping:
 *
 * **It patches the existing `MeshLambertMaterial` via `onBeforeCompile` instead of
 * swapping in a `ShaderMaterial`.** A separate material would have to reproduce
 * three's lighting exactly or the world would visibly pop the instant the build
 * finished — and it never quite does. Patching means the lit result is identical
 * by construction; when `uReveal` passes 1 there is nothing left to switch off.
 *
 * **The build order is computed from the vertex position, not from a new
 * attribute.** `buildWorldMesh` emits positions as (cube x, height, cube y)
 * directly in three-space and the mesh has an identity transform, so object space
 * *is* world space and a position is all the ordering needs. That keeps
 * geometry.ts — which is pure, tested, and shared with the physics — completely
 * untouched by a visual effect.
 */
import type * as THREE from 'three';

/**
 * How wide the moving front is, in units of overall progress. Each vertex
 * animates over this band, so the build is a travelling wave rather than a line:
 * at any instant roughly this fraction of the map is mid-flight.
 */
const BAND = 0.14;
/** How far below its resting place a cube starts. */
const RISE = 14;

export interface Reveal {
  /**
   * Drive the build, 0..1. Values are eased and clamped internally; 1 means fully
   * settled with no shader work left visible.
   */
  set(progress: number): void;
  /** Re-aim at a newly loaded map. */
  fit(center: [number, number], radius: number, height: number): void;
  /** Skip straight to the finished world (used for `prefers-reduced-motion`). */
  complete(): void;
}

/**
 * Patch `material` so it can be revealed progressively. Safe to call once per
 * material, before it is first used to draw.
 */
export function installReveal(material: THREE.Material): Reveal {
  const uReveal = { value: 0 };
  const uCenter = { value: [0, 0] as [number, number] };
  const uRadius = { value: 1 };
  const uHeight = { value: 1 };

  material.onBeforeCompile = (shader) => {
    shader.uniforms.uReveal = uReveal;
    shader.uniforms.uRevealCenter = uCenter;
    shader.uniforms.uRevealRadius = uRadius;
    shader.uniforms.uRevealHeight = uHeight;

    // `hd_build` is the vertex's place in the queue, 0 (first) to 1 (last):
    // mostly distance from the map's centre, nudged later by height so a wall
    // arrives after the floor it stands on, plus a per-column hash so the front
    // is ragged instead of a clean expanding ring.
    const common = `
      uniform float uReveal;
      uniform vec2 uRevealCenter;
      uniform float uRevealRadius;
      uniform float uRevealHeight;
      varying float vBuildLocal;

      float hd_hash(vec2 p) {
        return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
      }

      float hd_build(vec3 p) {
        float radial = clamp(length(p.xz - uRevealCenter) / max(uRevealRadius, 0.001), 0.0, 1.0);
        float height = clamp(p.y / max(uRevealHeight, 0.001), 0.0, 1.0);
        // Hash per 4-unit column so a whole cube shares one offset; hashing per
        // vertex would tear individual quads apart at their own corners.
        float jitter = hd_hash(floor(p.xz * 0.25));
        return clamp(radial * 0.70 + height * 0.16 + jitter * 0.14, 0.0, 1.0);
      }

      // 0 = not yet arrived, 1 = settled.
      float hd_local(vec3 p) {
        return clamp((uReveal - hd_build(p)) / ${BAND.toFixed(3)}, 0.0, 1.0);
      }
    `;

    shader.vertexShader = inject(shader.vertexShader, '#include <common>', common, 'vertex common');
    // `begin_vertex` declares `vec3 transformed = vec3( position )`, so this is
    // where a displacement belongs — before the normal/projection chunks read it.
    shader.vertexShader = inject(
      shader.vertexShader,
      '#include <begin_vertex>',
      `
        float hdL = hd_local(position);
        vBuildLocal = hdL;
        // Smoothstep so a cube decelerates into place rather than arriving linearly.
        float hdEase = hdL * hdL * (3.0 - 2.0 * hdL);
        transformed.y -= (1.0 - hdEase) * ${RISE.toFixed(1)};
      `,
      'vertex begin',
    );

    shader.fragmentShader = inject(
      shader.fragmentShader,
      '#include <common>',
      `
        uniform float uReveal;
        varying float vBuildLocal;
      `,
      'fragment common',
    );
    // Discard before any lighting work: a vertex that has not arrived is not
    // drawn at all, so the world builds rather than fades up.
    shader.fragmentShader = inject(
      shader.fragmentShader,
      '#include <clipping_planes_fragment>',
      `
        if (vBuildLocal <= 0.0) discard;
      `,
      'fragment clip',
    );
    // Last chunk in the shader, so the frontier glow is added over the final lit
    // colour. Cubes land hot and cool into their normal shading.
    shader.fragmentShader = inject(
      shader.fragmentShader,
      '#include <dithering_fragment>',
      `
        float hdEdge = 1.0 - smoothstep(0.0, 0.85, vBuildLocal);
        gl_FragColor.rgb += vec3(0.35, 0.62, 1.0) * hdEdge * 0.85;
      `,
      'fragment glow',
    );
  };
  // Changing the program source after a material has been compiled once needs the
  // cache key to change too, or three hands back the unpatched program.
  material.customProgramCacheKey = () => 'hassault-reveal';
  material.needsUpdate = true;

  return {
    set(progress: number) {
      const p = Number.isFinite(progress) ? Math.max(0, Math.min(1, progress)) : 0;
      // Driven past 1 by one band width so the *last* vertices finish their own
      // animation — at exactly 1.0 the far corner would still be mid-rise.
      uReveal.value = p * (1 + BAND);
    },
    fit(center: [number, number], radius: number, height: number) {
      uCenter.value = center;
      uRadius.value = Math.max(radius, 0.001);
      uHeight.value = Math.max(height, 0.001);
    },
    complete() {
      uReveal.value = 1 + BAND;
    },
  };
}

/**
 * `String.replace` on a chunk that isn't there silently does nothing, which would
 * mean a reveal that never reveals and no error to explain it. Chunk names are a
 * three-internal contract, so a miss is worth saying out loud.
 */
function inject(source: string, chunk: string, addition: string, where: string): string {
  if (!source.includes(chunk)) {
    console.warn(`[hassault] reveal: shader chunk ${chunk} missing (${where})`);
    return source;
  }
  return source.replace(chunk, `${chunk}\n${addition}`);
}
