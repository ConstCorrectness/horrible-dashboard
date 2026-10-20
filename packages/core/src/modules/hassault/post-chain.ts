/**
 * What happens between the scene and the canvas: bloom, then sharpening.
 *
 * **The native client's chain, pass for pass** (`apps/native-fps/src/post.wgsl`,
 * `post.rs`): the same threshold and knee, the same five-level 13-tap
 * downsample and 3×3 tent upsample, the same screen blend, the same
 * contrast-adaptive sharpening. `browser_parity.rs` pins the numbers in both.
 *
 * **On display-referred light**, as native has it: what passes the threshold
 * is what is already near white (lamps, screens, the sun, a muzzle flash), not
 * a sunlit wall. three tone maps inside each material only when drawing to the
 * canvas, so with the chain on the scene goes to a linear half-float target and
 * `DISPLAY` applies three's own ACES and sRGB curve to it, the same arithmetic
 * the materials would have done. Fog, sky and every material are untouched.
 *
 * **Off costs nothing.** With bloom and sharpening both at zero the chain is
 * skipped and the frame is the plain `renderer.render` it always was, canvas
 * MSAA included.
 *
 * Settings are the native client's (`hassault.video.bloom`, `hassault.video.sharpen`),
 * machine-scoped, 0 by default at every browser tier, as at every native tier
 * below Ultra.
 */
import type * as THREE from 'three';

/** Bloom mips below the half-resolution first one (native `LEVELS`). */
export const LEVELS = 5;
/** What passes the threshold, on the display-referred image (native `THRESHOLD`). */
export const THRESHOLD = 0.82;
/** The soft knee around it (native `KNEE`). */
export const KNEE = 0.12;

const VERTEX = /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}`;

/** three's own ACES (`tonemapping_pars_fragment`) and sRGB OETF, verbatim, so
 * the chain's image is the canvas's. */
const DISPLAY = /* glsl */ `
uniform sampler2D source;
uniform float exposure;
varying vec2 vUv;
vec3 RRTAndODTFit(vec3 v) {
  vec3 a = v * (v + 0.0245786) - 0.000090537;
  vec3 b = v * (0.983729 * v + 0.4329510) + 0.238081;
  return a / b;
}
vec3 aces(vec3 color) {
  const mat3 ACESInputMat = mat3(
    vec3(0.59719, 0.07600, 0.02840),
    vec3(0.35458, 0.90834, 0.13383),
    vec3(0.04823, 0.01566, 0.83777));
  const mat3 ACESOutputMat = mat3(
    vec3(1.60475, -0.10208, -0.00327),
    vec3(-0.53108, 1.10813, -0.07276),
    vec3(-0.07367, -0.00605, 1.07602));
  color *= exposure / 0.6;
  color = ACESInputMat * color;
  color = RRTAndODTFit(color);
  color = ACESOutputMat * color;
  return clamp(color, 0.0, 1.0);
}
vec3 srgb(vec3 v) {
  return mix(pow(v, vec3(0.41666)) * 1.055 - vec3(0.055), v * 12.92, vec3(lessThanEqual(v, vec3(0.0031308))));
}
void main() {
  gl_FragColor = vec4(srgb(aces(texture2D(source, vUv).rgb)), 1.0);
}`;

/** `fs_prefilter`: half resolution, four taps, a quadratic soft knee. */
const PREFILTER = /* glsl */ `
uniform sampler2D source;
uniform vec2 texel;
uniform float threshold;
uniform float knee;
varying vec2 vUv;
void main() {
  vec3 c = texture2D(source, vUv + texel * vec2(-0.5, -0.5)).rgb;
  c += texture2D(source, vUv + texel * vec2(0.5, -0.5)).rgb;
  c += texture2D(source, vUv + texel * vec2(-0.5, 0.5)).rgb;
  c += texture2D(source, vUv + texel * vec2(0.5, 0.5)).rgb;
  c *= 0.25;
  float k = max(knee, 1e-4);
  float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
  float soft = clamp(l - threshold + k, 0.0, 2.0 * k);
  float contribution = max(soft * soft / (4.0 * k), l - threshold) / max(l, 1e-4);
  gl_FragColor = vec4(c * max(contribution, 0.0), 1.0);
}`;

/** `fs_down`: Jimenez's 13-tap filter, which does not flicker as pixels move. */
const DOWN = /* glsl */ `
uniform sampler2D source;
uniform vec2 texel;
varying vec2 vUv;
vec3 at(vec2 o) { return texture2D(source, vUv + texel * o).rgb; }
void main() {
  vec3 o = at(vec2(0.0)) * 0.125;
  o += (at(vec2(-2.0, -2.0)) + at(vec2(2.0, -2.0)) + at(vec2(-2.0, 2.0)) + at(vec2(2.0, 2.0))) * 0.03125;
  o += (at(vec2(0.0, -2.0)) + at(vec2(-2.0, 0.0)) + at(vec2(2.0, 0.0)) + at(vec2(0.0, 2.0))) * 0.0625;
  o += (at(vec2(-1.0, -1.0)) + at(vec2(1.0, -1.0)) + at(vec2(-1.0, 1.0)) + at(vec2(1.0, 1.0))) * 0.125;
  gl_FragColor = vec4(o, 1.0);
}`;

/** `fs_up`: a 3×3 tent, blended additively onto the larger mip. */
const UP = /* glsl */ `
uniform sampler2D source;
uniform vec2 texel;
varying vec2 vUv;
vec3 at(vec2 o) { return texture2D(source, vUv + texel * o).rgb; }
void main() {
  vec3 o = at(vec2(0.0)) * 4.0;
  o += (at(vec2(-1.0, 0.0)) + at(vec2(1.0, 0.0)) + at(vec2(0.0, -1.0)) + at(vec2(0.0, 1.0))) * 2.0;
  o += at(vec2(-1.0, -1.0)) + at(vec2(1.0, -1.0)) + at(vec2(-1.0, 1.0)) + at(vec2(1.0, 1.0));
  gl_FragColor = vec4(o / 16.0, 1.0);
}`;

/** `fs_composite`: contrast-adaptive sharpening, then the bloom by screen blend. */
const COMPOSITE = /* glsl */ `
uniform sampler2D source;
uniform sampler2D bloom;
uniform vec2 texel;
uniform float intensity;
uniform float amount;
varying vec2 vUv;
void main() {
  vec3 color = texture2D(source, vUv).rgb;
  if (amount > 0.001) {
    vec3 n = texture2D(source, vUv + vec2(0.0, -texel.y)).rgb;
    vec3 s = texture2D(source, vUv + vec2(0.0, texel.y)).rgb;
    vec3 w = texture2D(source, vUv + vec2(-texel.x, 0.0)).rgb;
    vec3 e = texture2D(source, vUv + vec2(texel.x, 0.0)).rgb;
    vec3 lo = min(color, min(min(n, s), min(w, e)));
    vec3 hi = max(color, max(max(n, s), max(w, e)));
    vec3 room = min(lo, vec3(1.0) - hi) / max(hi, vec3(1e-4));
    vec3 adapt = sqrt(clamp(room, vec3(0.0), vec3(1.0)));
    float peak = -1.0 / mix(8.0, 5.0, amount);
    vec3 wgt = adapt * peak;
    color = clamp((color + (n + s + w + e) * wgt) / (vec3(1.0) + 4.0 * wgt), vec3(0.0), vec3(1.0));
  }
  if (intensity > 0.001) {
    vec3 g = texture2D(bloom, vUv).rgb * intensity;
    color = vec3(1.0) - (vec3(1.0) - color) * (vec3(1.0) - g);
  }
  gl_FragColor = vec4(color, 1.0);
}`;

/** three's ACES (column-major, as GLSL reads its `mat3`), for the inverse. */
const ACES_IN = [0.59719, 0.076, 0.0284, 0.35458, 0.90834, 0.13383, 0.04823, 0.01566, 0.83777];
const ACES_OUT = [1.60475, -0.10208, -0.00327, -0.53108, 1.10813, -0.07276, -0.07367, -0.00605, 1.07602];

type Vec3 = [number, number, number];

/** `m * v` for a column-major 3×3. */
function mul(m: number[], v: Vec3): Vec3 {
  return [
    m[0] * v[0] + m[3] * v[1] + m[6] * v[2],
    m[1] * v[0] + m[4] * v[1] + m[7] * v[2],
    m[2] * v[0] + m[5] * v[1] + m[8] * v[2],
  ];
}

function invert3(m: number[]): number[] {
  const [a, b, c, d, e, f, g, h, i] = m;
  const A = e * i - f * h;
  const B = -(d * i - f * g);
  const C = d * h - e * g;
  const det = a * A + b * B + c * C;
  return [
    A / det,
    (c * h - b * i) / det,
    (b * f - c * e) / det,
    B / det,
    (a * i - c * g) / det,
    (c * d - a * f) / det,
    C / det,
    (b * g - a * h) / det,
    (a * e - b * d) / det,
  ];
}

const ACES_IN_INV = invert3(ACES_IN);
const ACES_OUT_INV = invert3(ACES_OUT);

/** three's ACES on a linear colour, before the sRGB curve: the reference. */
export function aces(color: Vec3, exposure: number): Vec3 {
  const scaled = color.map((c) => (c * exposure) / 0.6) as Vec3;
  const v = mul(ACES_IN, scaled);
  const fit = v.map((x) => (x * (x + 0.0245786) - 0.000090537) / (x * (0.983729 * x + 0.432951) + 0.238081)) as Vec3;
  return mul(ACES_OUT, fit).map((x) => Math.min(1, Math.max(0, x))) as Vec3;
}

/**
 * The linear colour ACES maps back to `target`: what the scene's clear colour
 * must be inside the chain so the display pass returns the colour the canvas
 * path would have shown. three never tone maps the background on the canvas,
 * and the chain tone maps everything in its target, background included.
 */
export function inverseAces(target: Vec3, exposure: number): Vec3 {
  const fit = mul(ACES_OUT_INV, target);
  const v = fit.map((y) => {
    // a(v)/b(v) = y, rearranged: (1 - 0.983729y)v² + (0.0245786 - 0.432951y)v
    // - (0.000090537 + 0.238081y) = 0; the positive root. Past the curve's
    // ceiling there is none, and the brightest finite value stands in.
    const qa = 1 - 0.983729 * y;
    const qb = 0.0245786 - 0.432951 * y;
    const qc = -(0.000090537 + 0.238081 * y);
    if (qa <= 1e-6) return 1e3;
    return (-qb + Math.sqrt(Math.max(0, qb * qb - 4 * qa * qc))) / (2 * qa);
  }) as Vec3;
  return mul(ACES_IN_INV, v).map((x) => Math.max(0, (x * 0.6) / exposure)) as Vec3;
}

export interface PostSettings {
  /** 0..1, `hassault.video.bloom`. */
  bloom: number;
  /** 0..1, `hassault.video.sharpen`. */
  sharpen: number;
}

/** Whether the chain runs at all: off is the plain render, at no cost. */
export function chainActive(settings: PostSettings): boolean {
  return settings.bloom > 0.001 || settings.sharpen > 0.001;
}

/** The bloom chain's sizes, half resolution first, each level half the last,
 * never below one pixel. Native sizes its mips the same way. */
export function mipSizes(width: number, height: number, levels = LEVELS): [number, number][] {
  const out: [number, number][] = [];
  let w = Math.max(1, Math.floor(width / 2));
  let h = Math.max(1, Math.floor(height / 2));
  for (let i = 0; i <= levels; i += 1) {
    out.push([w, h]);
    w = Math.max(1, Math.floor(w / 2));
    h = Math.max(1, Math.floor(h / 2));
  }
  return out;
}

export class PostChain {
  private readonly three: typeof THREE;
  private readonly renderer: THREE.WebGLRenderer;
  private readonly quad: THREE.Mesh;
  private readonly camera: THREE.Camera;
  private readonly quadScene: THREE.Scene;
  private readonly materials: Record<string, THREE.ShaderMaterial>;
  private scene: THREE.WebGLRenderTarget | null = null;
  private display: THREE.WebGLRenderTarget | null = null;
  private mips: THREE.WebGLRenderTarget[] = [];
  private size: [number, number] = [0, 0];
  private samples = 0;

  constructor(three: typeof THREE, renderer: THREE.WebGLRenderer) {
    this.three = three;
    this.renderer = renderer;
    const make = (fragmentShader: string, uniforms: Record<string, THREE.IUniform>) =>
      new three.ShaderMaterial({
        vertexShader: VERTEX,
        fragmentShader,
        uniforms,
        depthTest: false,
        depthWrite: false,
        toneMapped: false,
      });
    const v2 = () => ({ value: new three.Vector2() });
    this.materials = {
      display: make(DISPLAY, { source: { value: null }, exposure: { value: 1 } }),
      prefilter: make(PREFILTER, {
        source: { value: null },
        texel: v2(),
        threshold: { value: THRESHOLD },
        knee: { value: KNEE },
      }),
      down: make(DOWN, { source: { value: null }, texel: v2() }),
      up: make(UP, { source: { value: null }, texel: v2() }),
      composite: make(COMPOSITE, {
        source: { value: null },
        bloom: { value: null },
        texel: v2(),
        intensity: { value: 0 },
        amount: { value: 0 },
      }),
    };
    this.materials.up.blending = three.AdditiveBlending;
    this.materials.up.transparent = true;
    this.quad = new three.Mesh(new three.PlaneGeometry(2, 2), this.materials.display);
    this.quad.frustumCulled = false;
    this.quadScene = new three.Scene();
    this.quadScene.add(this.quad);
    this.camera = new three.Camera();
  }

  private target(w: number, h: number, samples = 0, depth = false): THREE.WebGLRenderTarget {
    const t = this.three;
    return new t.WebGLRenderTarget(w, h, {
      type: t.HalfFloatType,
      minFilter: t.LinearFilter,
      magFilter: t.LinearFilter,
      wrapS: t.ClampToEdgeWrapping,
      wrapT: t.ClampToEdgeWrapping,
      // Only the scene needs depth; the passes are fullscreen quads.
      depthBuffer: depth,
      samples,
    });
  }

  /** (Re)build the targets for this drawing-buffer size and MSAA. */
  private ensure(width: number, height: number, samples: number): void {
    if (
      this.size[0] === width &&
      this.size[1] === height &&
      this.samples === samples &&
      this.scene
    ) {
      return;
    }
    this.disposeTargets();
    this.size = [width, height];
    this.samples = samples;
    this.scene = this.target(width, height, samples, true);
    this.display = this.target(width, height);
    this.mips = mipSizes(width, height).map(([w, h]) => this.target(w, h));
  }

  private pass(material: THREE.ShaderMaterial, out: THREE.WebGLRenderTarget | null): void {
    this.quad.material = material;
    this.renderer.setRenderTarget(out);
    this.renderer.render(this.quadScene, this.camera);
  }

  /**
   * Draw a frame. With the chain off this is exactly `renderer.render`.
   * `antialias` is the tier's MSAA, which a render target needs asking for
   * (the canvas's own flag does not reach it).
   */
  render(
    scene: THREE.Scene,
    camera: THREE.Camera,
    settings: PostSettings,
    antialias: boolean,
  ): void {
    const r = this.renderer;
    if (!chainActive(settings)) {
      r.render(scene, camera);
      return;
    }
    const size = r.getDrawingBufferSize(new this.three.Vector2());
    this.ensure(Math.max(1, size.x), Math.max(1, size.y), antialias ? 4 : 0);
    const sceneTarget = this.scene!;
    const display = this.display!;
    const prior = r.getRenderTarget();
    const autoClear = r.autoClear;

    // The clear colour, pre-distorted so that tone mapping it gives back the
    // colour the canvas would have cleared to (`inverseAces`).
    const background = scene.background;
    const swapped =
      background && (background as THREE.Color).isColor
        ? (background as THREE.Color).clone()
        : null;
    if (swapped) {
      const want = swapped.clone();
      const [r0, g0, b0] = inverseAces(
        [want.r, want.g, want.b].map((c) => Math.min(1, c)) as Vec3,
        r.toneMappingExposure,
      );
      scene.background = new this.three.Color(r0, g0, b0);
    }
    r.setRenderTarget(sceneTarget);
    r.render(scene, camera);
    if (swapped) scene.background = background;
    r.autoClear = true;

    const m = this.materials;
    m.display.uniforms.source.value = sceneTarget.texture;
    m.display.uniforms.exposure.value = r.toneMappingExposure;
    this.pass(m.display, display);

    const bloom = Math.min(1, Math.max(0, settings.bloom));
    if (bloom > 0.001 && this.mips.length > 0) {
      m.prefilter.uniforms.source.value = display.texture;
      m.prefilter.uniforms.texel.value.set(1 / display.width, 1 / display.height);
      this.pass(m.prefilter, this.mips[0]);
      for (let i = 1; i < this.mips.length; i += 1) {
        const from = this.mips[i - 1];
        m.down.uniforms.source.value = from.texture;
        m.down.uniforms.texel.value.set(1 / from.width, 1 / from.height);
        this.pass(m.down, this.mips[i]);
      }
      // Back up the chain, each level added onto the larger one.
      r.autoClear = false;
      for (let i = this.mips.length - 1; i > 0; i -= 1) {
        const from = this.mips[i];
        m.up.uniforms.source.value = from.texture;
        m.up.uniforms.texel.value.set(1 / from.width, 1 / from.height);
        this.pass(m.up, this.mips[i - 1]);
      }
      r.autoClear = true;
    }

    m.composite.uniforms.source.value = display.texture;
    m.composite.uniforms.bloom.value = this.mips[0]?.texture ?? null;
    m.composite.uniforms.texel.value.set(1 / display.width, 1 / display.height);
    m.composite.uniforms.intensity.value = bloom;
    m.composite.uniforms.amount.value = Math.min(1, Math.max(0, settings.sharpen));
    this.pass(m.composite, prior);
    r.autoClear = autoClear;
  }

  private disposeTargets(): void {
    this.scene?.dispose();
    this.display?.dispose();
    for (const mip of this.mips) mip.dispose();
    this.scene = null;
    this.display = null;
    this.mips = [];
  }

  dispose(): void {
    this.disposeTargets();
    for (const mat of Object.values(this.materials)) mat.dispose();
    this.quad.geometry.dispose();
  }
}
