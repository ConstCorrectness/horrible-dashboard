/**
 * What happens between the scene and the canvas: bloom, the tone curve,
 * sharpening and a dither.
 *
 * **The native client's chain, pass for pass** (`apps/native-fps/src/post.wgsl`,
 * `post.rs`): the same threshold and knee, the same five-level 13-tap
 * downsample and 3×3 tent upsample, the same contrast-adaptive sharpening, the
 * same dither. `browser_parity.rs` pins the numbers in both. Native also has
 * FXAA, for its Medium level; the browser's Medium multisamples instead.
 *
 * **HDR bloom.** three tone maps inside each material only when drawing to the
 * canvas, so with the chain on the scene goes to a linear half-float target.
 * The bloom is thresholded on that linear light (times the exposure), so what
 * glows is what is brighter than white — a lamp's emissive at 6, the sun, a
 * screen, the sun's disc — by how far over it is, and a sunlit wall (about 0.95)
 * does not. (The muzzle flash is unlit at 1 and has its own wider cone.)
 * `DISPLAY` then adds the glow to the scene and applies three's own ACES and
 * sRGB curve, the arithmetic the materials would have done, so the glow rolls
 * off into the curve's shoulder with the light it came from. Every material is
 * untouched; the clear colour and the fog colour are run backwards through the
 * curve for the length of the scene render (`sceneColoursForChain`), because
 * inside a render target three mixes fog in linear light *before* the curve —
 * the order native uses always.
 *
 * **Off costs nothing.** With bloom and sharpening both at zero the chain is
 * skipped and the frame is the plain `renderer.render` it always was, canvas
 * MSAA included.
 *
 * Settings are the native client's (`hassault.video.bloom`, `hassault.video.sharpen`),
 * machine-scoped. Bloom defaults to 0.25 (native `MODERATE_BLOOM`, Medium's and
 * High's), and the browser draws it where its tier's `bloom` row is on — off at
 * Low, so Low still skips the chain.
 */
import type * as THREE from 'three';

/** `hassault.video.bloom`'s default: native `MODERATE_BLOOM`, Medium's and High's. */
export const MODERATE_BLOOM = 0.25;

/** Bloom mips below the half-resolution first one (native `LEVELS`). */
export const LEVELS = 5;
/** What passes the threshold, in exposed linear light (native `THRESHOLD`). */
export const THRESHOLD = 1.4;
/** The soft knee's half-width around it (native `KNEE`). */
export const KNEE = 0.5;

/**
 * The share of a colour of luminance `l` the prefilter keeps: `PREFILTER`'s
 * soft-knee arithmetic on the CPU, so the threshold can be tested against the
 * scenes it was tuned on.
 */
export function bloomShare(l: number, threshold = THRESHOLD, knee = KNEE): number {
  const k = Math.max(knee, 1e-4);
  const soft = Math.min(Math.max(l - threshold + k, 0), 2 * k);
  return Math.max(0, Math.max((soft * soft) / (4 * k), l - threshold) / Math.max(l, 1e-4));
}

const VERTEX = /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}`;

/** The scene plus its bloom, through three's own ACES (`tonemapping_pars_fragment`)
 * and sRGB OETF, verbatim, so the chain's image is the canvas's. The exposure
 * is applied to the scene here rather than inside `aces`, because the bloom was
 * taken from exposed light already. */
const DISPLAY = /* glsl */ `
uniform sampler2D source;
uniform sampler2D bloom;
uniform float exposure;
uniform float intensity;
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
  color = color / 0.6;
  color = ACESInputMat * color;
  color = RRTAndODTFit(color);
  color = ACESOutputMat * color;
  return clamp(color, 0.0, 1.0);
}
vec3 srgb(vec3 v) {
  return mix(pow(v, vec3(0.41666)) * 1.055 - vec3(0.055), v * 12.92, vec3(lessThanEqual(v, vec3(0.0031308))));
}
void main() {
  vec3 light = texture2D(source, vUv).rgb * exposure;
  if (intensity > 0.001) light += texture2D(bloom, vUv).rgb * intensity;
  gl_FragColor = vec4(srgb(aces(light)), 1.0);
}`;

/** `fs_prefilter`: half resolution, four taps, a quadratic soft knee. */
const PREFILTER = /* glsl */ `
uniform sampler2D source;
uniform vec2 texel;
uniform float threshold;
uniform float knee;
uniform float exposure;
varying vec2 vUv;
void main() {
  vec3 c = texture2D(source, vUv + texel * vec2(-0.5, -0.5)).rgb;
  c += texture2D(source, vUv + texel * vec2(0.5, -0.5)).rgb;
  c += texture2D(source, vUv + texel * vec2(-0.5, 0.5)).rgb;
  c += texture2D(source, vUv + texel * vec2(0.5, 0.5)).rgb;
  // Exposed, as native's scene already is.
  c *= 0.25 * exposure;
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

/** `fs_composite`: contrast-adaptive sharpening, then the dither. */
const COMPOSITE = /* glsl */ `
uniform sampler2D source;
uniform vec2 texel;
uniform float amount;
varying vec2 vUv;
// Interleaved gradient noise (Jimenez), as native's \`ign\`.
float ign(vec2 p) {
  return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715))));
}
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
  // One step of triangular noise on the 8-bit canvas, so a sky or a fogged
  // corridor quantises as grain rather than bands. \`color\` is already
  // sRGB-encoded here (DISPLAY did it), so the step is 1/255 as it stands.
  color += (ign(gl_FragCoord.xy) + ign(gl_FragCoord.xy + vec2(47.0, 17.0)) - 1.0) / 255.0;
  gl_FragColor = vec4(clamp(color, 0.0, 1.0), 1.0);
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

/**
 * Put the scene's clear and fog colours into the chain's space for one render,
 * and return the function that puts them back.
 *
 * Both are colours the player has to *see* exactly: the canvas never tone maps
 * the background, and on the canvas three mixes fog after its colour-space
 * encode, so a fully fogged wall is the map's hex. Inside the chain's target
 * both go through `DISPLAY`'s curve with everything else — and fog is mixed in
 * linear light first — so unswapped, the sky would come back lifted and the
 * far end of a corridor would recede into grey instead of the map's colour.
 * Native does the same to its uniforms (`display_hex_to_scene`, `atmosphere.rs`).
 */
export function sceneColoursForChain(
  three: typeof THREE,
  scene: THREE.Scene,
  exposure: number,
): () => void {
  const toScene = (c: THREE.Color): THREE.Color => {
    const [r, g, b] = inverseAces([c.r, c.g, c.b].map((x) => Math.min(1, x)) as Vec3, exposure);
    return new three.Color(r, g, b);
  };
  const background = scene.background;
  const swapBackground = background && (background as THREE.Color).isColor;
  if (swapBackground) scene.background = toScene(background as THREE.Color);
  const fog = scene.fog;
  const fogColor = fog ? fog.color.clone() : null;
  if (fog) fog.color.copy(toScene(fog.color));
  return () => {
    if (swapBackground) scene.background = background;
    if (fog && fogColor) fog.color.copy(fogColor);
  };
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
      display: make(DISPLAY, {
        source: { value: null },
        bloom: { value: null },
        exposure: { value: 1 },
        intensity: { value: 0 },
      }),
      prefilter: make(PREFILTER, {
        source: { value: null },
        texel: v2(),
        threshold: { value: THRESHOLD },
        knee: { value: KNEE },
        exposure: { value: 1 },
      }),
      down: make(DOWN, { source: { value: null }, texel: v2() }),
      up: make(UP, { source: { value: null }, texel: v2() }),
      composite: make(COMPOSITE, {
        source: { value: null },
        texel: v2(),
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

  /**
   * The target `render` would draw the scene into with these settings: its own
   * scene target with the chain on, the current one (the canvas) with it off.
   * For `prewarm`, which has to compile against the same one.
   */
  sceneTarget(settings: PostSettings, antialias: boolean): THREE.WebGLRenderTarget | null {
    if (!chainActive(settings)) return this.renderer.getRenderTarget();
    const size = this.renderer.getDrawingBufferSize(new this.three.Vector2());
    this.ensure(Math.max(1, size.x), Math.max(1, size.y), antialias ? 4 : 0);
    return this.scene;
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

    // The clear and fog colours, pre-distorted so that tone mapping them gives
    // back the colours the canvas would have shown (`inverseAces`).
    const restore = sceneColoursForChain(this.three, scene, r.toneMappingExposure);
    try {
      r.setRenderTarget(sceneTarget);
      r.render(scene, camera);
    } finally {
      restore();
    }
    r.autoClear = true;

    const m = this.materials;
    const exposure = r.toneMappingExposure;
    const bloom = Math.min(1, Math.max(0, settings.bloom));
    if (bloom > 0.001 && this.mips.length > 0) {
      // From the scene's linear light, before the curve.
      m.prefilter.uniforms.source.value = sceneTarget.texture;
      m.prefilter.uniforms.texel.value.set(1 / sceneTarget.width, 1 / sceneTarget.height);
      m.prefilter.uniforms.exposure.value = exposure;
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

    m.display.uniforms.source.value = sceneTarget.texture;
    m.display.uniforms.bloom.value = this.mips[0]?.texture ?? null;
    m.display.uniforms.exposure.value = exposure;
    m.display.uniforms.intensity.value = bloom > 0.001 ? bloom : 0;
    this.pass(m.display, display);

    m.composite.uniforms.source.value = display.texture;
    m.composite.uniforms.texel.value.set(1 / display.width, 1 / display.height);
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
