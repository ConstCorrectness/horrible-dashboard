/**
 * The neuron stack: every layer's MLP neurons as a plate of points, the plates
 * stacked in depth, a camera that orbits, zooms and slides from plate to plate on
 * springs. Imperative three.js owned by one React effect, like `Avatar3D.tsx`.
 *
 * - **Every neuron is drawn.** One `Points` per layer over a shared grid of
 *   positions, with its own `heat` attribute; the point shader colours by heat and
 *   fades by distance from the focused plate. Qwen3-4B is 36 × 9728 points.
 * - **On demand.** A frame is drawn when something changed (`invalidate`) and
 *   keeps being drawn only while the camera is still moving. A hidden pane never
 *   fires `requestAnimationFrame`, so each invalidation also arms a timer that
 *   draws the settled frame without it.
 * - **Picking is analytic** (`camera.ts` `pickCell`): the pointer's ray against each
 *   visible plate's plane, never a Raycaster over the points.
 * - **Colours are theme tokens**, resolved through `readThemeTokens` and re-read
 *   when the theme switches.
 */
import * as THREE from 'three';

import { readThemeTokens } from '../../../theme';
import {
  clampCamera,
  DEFAULT_CAMERA,
  pickCell,
  stepCamera,
  ZERO_VELOCITY,
  type CameraParams,
  type CameraVelocity,
  type LayerPlane,
  type Vec3,
} from './camera';
import { adjacent, cellOf, gridOf, type Grid } from './grid';

/** World width of a plate, whatever the layer's width. */
const PLATE = 4.8;
/** Camera distance at zoom 1: the plate fits with a margin at FOV degrees. */
const FOV = 35;
const BASE_DISTANCE = 9.6;
/** Above this zoom the view is "inspect": one plate, panning instead of orbiting. */
export const DETAIL_ZOOM = 1.7;
/** Pixels under which a stack is drawn compactly (fewer plates around the focus). */
const COMPACT_WIDTH = 900;
const PULSE_MS = 650;

export interface Selection {
  layer: number;
  neuron: number;
}

/** A steered neuron, drawn with a halo in its polarity's colour. */
export interface Mark {
  layer: number;
  neuron: number;
  /** Sign of its influence: +1 pushes, −1 pulls, 0 neutral or disabled. */
  polarity: -1 | 0 | 1;
  /** |influence| / max dose, in [0, 1]. */
  amount: number;
}

/** What is under the pointer, in canvas CSS pixels. */
export interface Hover {
  layer: number;
  neuron: number;
  x: number;
  y: number;
}

export interface StackCallbacks {
  /** A neuron on the focused plate was clicked (or reached by the keyboard). */
  onSelect?: (s: Selection) => void;
  /** Another plate was clicked: traverse to it. */
  onLayer?: (layer: number) => void;
  onHover?: (h: Hover | null) => void;
  /** Inspect mode entered or left. */
  onDetail?: (detail: boolean) => void;
  /** A pointer gesture started (for the sound and the hint). */
  onInteraction?: () => void;
}

interface Colors {
  pos: THREE.Color;
  neg: THREE.Color;
  neutral: THREE.Color;
  line: THREE.Color;
  focus: THREE.Color;
  accent: THREE.Color;
}

const TOKENS = [
  'ramp-pos',
  'ramp-neg',
  'text-faint',
  'border-strong',
  'text-dim',
  'accent',
] as const;

/** Any CSS colour → THREE.Color, through a canvas (which accepts every syntax). */
function cssColor(value: string, fallback: THREE.Color): THREE.Color {
  if (!value) return fallback.clone();
  try {
    const ctx = document.createElement('canvas').getContext('2d');
    if (!ctx) return new THREE.Color(value);
    ctx.fillStyle = value;
    return new THREE.Color(ctx.fillStyle);
  } catch {
    return fallback.clone();
  }
}

function readColors(): Colors {
  const t = readThemeTokens(TOKENS);
  const grey = new THREE.Color(0.6, 0.6, 0.62);
  return {
    pos: cssColor(t['ramp-pos'], grey),
    neg: cssColor(t['ramp-neg'], grey),
    neutral: cssColor(t['text-faint'], grey),
    line: cssColor(t['border-strong'], grey),
    focus: cssColor(t['text-dim'], grey),
    accent: cssColor(t.accent, grey),
  };
}

const POINT_VERTEX = /* glsl */ `
  attribute float heat;
  uniform float uSize;
  uniform float uScale;
  varying float vHeat;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mv;
    float h = min(abs(heat), 1.0);
    gl_PointSize = max(1.0, uSize * uScale * (1.0 + 0.8 * h) / -mv.z);
    vHeat = heat;
  }
`;

const POINT_FRAGMENT = /* glsl */ `
  uniform vec3 uPos;
  uniform vec3 uNeg;
  uniform vec3 uNeutral;
  uniform float uAlpha;
  varying float vHeat;
  void main() {
    vec2 c = gl_PointCoord * 2.0 - 1.0;
    float r = dot(c, c);
    if (r > 1.0) discard;
    float h = min(abs(vHeat), 1.0);
    vec3 col = mix(uNeutral, vHeat >= 0.0 ? uPos : uNeg, h);
    // A lit sphere-ish dot: brighter core, soft rim.
    float rim = smoothstep(1.0, 0.45, r);
    col *= 0.78 + 0.32 * (1.0 - r);
    gl_FragColor = vec4(col, uAlpha * rim * (0.55 + 0.45 * h));
    // Colours arrive in three's linear working space; back to the canvas's sRGB.
    #include <colorspace_fragment>
  }
`;

const MARK_VERTEX = /* glsl */ `
  attribute vec3 color;
  attribute float amount;
  uniform float uSize;
  uniform float uScale;
  varying vec3 vColor;
  varying float vAmount;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = max(6.0, uSize * uScale * (2.4 + 3.0 * amount) / -mv.z);
    vColor = color;
    vAmount = amount;
  }
`;

const MARK_FRAGMENT = /* glsl */ `
  varying vec3 vColor;
  varying float vAmount;
  void main() {
    vec2 c = gl_PointCoord * 2.0 - 1.0;
    float r = length(c);
    if (r > 1.0) discard;
    float core = smoothstep(0.32, 0.18, r);
    float halo = (1.0 - smoothstep(0.0, 1.0, r)) * (0.25 + 0.5 * vAmount);
    gl_FragColor = vec4(vColor, max(core, halo));
    #include <colorspace_fragment>
  }
`;

interface Plate {
  group: THREE.Group;
  points: THREE.Points;
  material: THREE.ShaderMaterial;
  heat: THREE.BufferAttribute;
  outline: THREE.LineLoop;
  outlineMaterial: THREE.LineBasicMaterial;
  rules: THREE.LineSegments;
  rulesMaterial: THREE.LineBasicMaterial;
  label: HTMLButtonElement;
}

export class NeuronStack {
  readonly grid: Grid;
  private readonly spacing: number;
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera3 = new THREE.PerspectiveCamera(FOV, 1, 0.05, 200);
  private readonly plates: Plate[] = [];
  private readonly sharedPositions: THREE.BufferAttribute;
  private readonly marks: THREE.Points;
  private readonly marksMaterial: THREE.ShaderMaterial;
  private readonly ring: THREE.Mesh;
  private readonly pulse: THREE.Mesh;
  private readonly hoverBox: THREE.LineLoop;
  private readonly disposables: { dispose(): void }[] = [];
  private colors: Colors;

  private camera: CameraParams;
  private target: CameraParams;
  private velocity: CameraVelocity = { ...ZERO_VELOCITY };
  private selection: Selection = { layer: 0, neuron: 0 };
  private markList: Mark[] = [];
  private hover: Hover | null = null;
  private hoverLayer: number | null = null;
  private isolated = false;
  private overview: { yaw: number; pitch: number } | null = null;
  private pulseStart = -1e9;
  private width = 1;
  private height = 1;
  private frame: number | null = null;
  private fallback: ReturnType<typeof setTimeout> | null = null;
  private lastTime = 0;
  private disposed = false;
  private readonly pointers = new Map<number, { x: number; y: number }>();
  private gesture: { moved: number; pinched: boolean; pan: boolean; distance?: number } | null =
    null;
  private readonly events = new AbortController();
  private readonly resizeObserver: ResizeObserver;
  private readonly themeObserver: MutationObserver;
  private readonly reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly labels: HTMLElement,
    readonly layers: number,
    ffn: number,
    private readonly callbacks: StackCallbacks = {},
    options: { pixelRatio?: number } = {},
  ) {
    this.grid = gridOf(ffn);
    this.spacing = PLATE / Math.max(1, this.grid.cols - 1);
    this.camera = { ...DEFAULT_CAMERA };
    this.target = { ...DEFAULT_CAMERA };
    this.colors = readColors();

    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(options.pixelRatio ?? Math.min(devicePixelRatio || 1, 2));
    this.renderer.setClearColor(new THREE.Color(0, 0, 0), 0);

    // The grid's positions in a plate's own frame: column → −z (screen right when
    // the camera faces the plate from +x), row → −y, centred on the origin.
    const { cols, rows, ffn: n } = this.grid;
    const pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const { row, col } = cellOf(i, this.grid);
      pos[i * 3] = 0;
      pos[i * 3 + 1] = ((rows - 1) / 2 - row) * this.spacing;
      pos[i * 3 + 2] = ((cols - 1) / 2 - col) * this.spacing;
    }
    this.sharedPositions = new THREE.BufferAttribute(pos, 3);

    const halfW = ((cols - 1) / 2 + 0.8) * this.spacing;
    const halfH = ((rows - 1) / 2 + 0.8) * this.spacing;
    const outlineGeometry = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, halfH, halfW),
      new THREE.Vector3(0, halfH, -halfW),
      new THREE.Vector3(0, -halfH, -halfW),
      new THREE.Vector3(0, -halfH, halfW),
    ]);
    const rulePoints: THREE.Vector3[] = [];
    for (let k = 16; k < Math.max(cols, rows); k += 16) {
      if (k < cols) {
        const z = ((cols - 1) / 2 - k + 0.5) * this.spacing;
        rulePoints.push(new THREE.Vector3(0, halfH, z), new THREE.Vector3(0, -halfH, z));
      }
      if (k < rows) {
        const y = ((rows - 1) / 2 - k + 0.5) * this.spacing;
        rulePoints.push(new THREE.Vector3(0, y, halfW), new THREE.Vector3(0, y, -halfW));
      }
    }
    const rulesGeometry = new THREE.BufferGeometry().setFromPoints(rulePoints);
    this.disposables.push(outlineGeometry, rulesGeometry);

    for (let l = 0; l < layers; l++)
      this.plates.push(this.buildPlate(l, outlineGeometry, rulesGeometry));

    // Steered neurons: a halo per mark, rebuilt when the marks change.
    this.marksMaterial = new THREE.ShaderMaterial({
      vertexShader: MARK_VERTEX,
      fragmentShader: MARK_FRAGMENT,
      uniforms: { uSize: { value: this.spacing }, uScale: { value: 1 } },
      transparent: true,
      depthWrite: false,
      depthTest: false,
    });
    this.marks = new THREE.Points(new THREE.BufferGeometry(), this.marksMaterial);
    this.marks.renderOrder = 3;
    this.marks.frustumCulled = false;
    this.scene.add(this.marks);
    this.disposables.push(this.marksMaterial);

    // The selection ring, its pulse, and the hover square: in the plate's plane.
    const ringGeometry = new THREE.RingGeometry(0.86, 1, 40);
    ringGeometry.rotateY(Math.PI / 2);
    const ringMaterial = new THREE.MeshBasicMaterial({
      transparent: true,
      opacity: 0.85,
      depthTest: false,
      side: THREE.DoubleSide,
    });
    this.ring = new THREE.Mesh(ringGeometry, ringMaterial);
    this.ring.renderOrder = 4;
    const pulseMaterial = ringMaterial.clone();
    this.pulse = new THREE.Mesh(ringGeometry, pulseMaterial);
    this.pulse.renderOrder = 4;
    const boxGeometry = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 1, 1),
      new THREE.Vector3(0, 1, -1),
      new THREE.Vector3(0, -1, -1),
      new THREE.Vector3(0, -1, 1),
    ]);
    const boxMaterial = new THREE.LineBasicMaterial({ transparent: true, depthTest: false });
    this.hoverBox = new THREE.LineLoop(boxGeometry, boxMaterial);
    this.hoverBox.renderOrder = 4;
    this.scene.add(this.ring, this.pulse, this.hoverBox);
    this.disposables.push(ringGeometry, ringMaterial, pulseMaterial, boxGeometry, boxMaterial);
    this.applyColors();

    this.bindInput();
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(canvas);
    this.themeObserver = new MutationObserver(() => {
      this.colors = readColors();
      this.applyColors();
      this.invalidate();
    });
    this.themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme', 'class', 'style'],
    });
    document.addEventListener('visibilitychange', () => !document.hidden && this.invalidate(), {
      signal: this.events.signal,
    });
    this.resize();
  }

  // ── public ────────────────────────────────────────────────────────────────

  setSelection(s: Selection, pulse = true): void {
    const layerChanged = s.layer !== this.selection.layer;
    this.selection = { ...s };
    this.target.layer = s.layer;
    if (layerChanged && this.target.zoom > DETAIL_ZOOM) {
      // A new plate starts in overview.
      this.leaveDetail();
    } else if (this.target.zoom > DETAIL_ZOOM) {
      this.centerOn(s.neuron);
    }
    if (pulse && !this.reducedMotion.matches) this.pulseStart = performance.now();
    this.invalidate();
  }

  getSelection(): Selection {
    return { ...this.selection };
  }

  /** One layer's heat in [-1, 1] per neuron, or null to clear it. */
  setHeat(layer: number, heat: Float32Array | null): void {
    const plate = this.plates[layer];
    if (!plate) return;
    const arr = plate.heat.array as Float32Array;
    if (heat) arr.set(heat.subarray(0, arr.length));
    else arr.fill(0);
    plate.heat.needsUpdate = true;
    this.invalidate();
  }

  clearHeat(): void {
    for (let l = 0; l < this.plates.length; l++) this.setHeat(l, null);
  }

  setMarks(marks: readonly Mark[]): void {
    this.markList = marks.filter((m) => m.layer >= 0 && m.layer < this.layers);
    this.invalidate();
  }

  /** Neighbouring neuron on the focused plate, as the arrow keys move. */
  step(dx: number, dy: number): void {
    const neuron = adjacent(this.selection.neuron, dx, dy, this.grid);
    this.callbacks.onSelect?.({ layer: this.selection.layer, neuron });
  }

  orbit(dYaw: number, dPitch: number): void {
    this.target = clampCamera({
      ...this.target,
      yaw: this.target.yaw + dYaw,
      pitch: this.target.pitch + dPitch,
    });
    this.invalidate();
  }

  zoomBy(factor: number): void {
    const before = this.target.zoom;
    this.target = clampCamera({ ...this.target, zoom: before * factor });
    if (before <= DETAIL_ZOOM && this.target.zoom > DETAIL_ZOOM) {
      this.centerOn(this.selection.neuron);
      this.callbacks.onDetail?.(true);
    } else if (before > DETAIL_ZOOM && this.target.zoom <= DETAIL_ZOOM) {
      this.target.panX = this.target.panY = 0;
      this.callbacks.onDetail?.(false);
    }
    this.hover = null;
    this.callbacks.onHover?.(null);
    this.invalidate();
  }

  /** Toggle inspect: face the plate, zoom onto the selection; or back to overview. */
  inspect(): void {
    if (this.target.zoom > DETAIL_ZOOM) {
      this.leaveDetail();
    } else {
      this.overview = { yaw: this.target.yaw, pitch: this.target.pitch };
      this.target = {
        ...this.target,
        zoom: this.width <= COMPACT_WIDTH ? 6 : 3.2,
        yaw: 1.42,
        pitch: 0.06,
      };
      this.centerOn(this.selection.neuron);
      this.callbacks.onDetail?.(true);
    }
    this.invalidate();
  }

  get detail(): boolean {
    return this.target.zoom > DETAIL_ZOOM;
  }

  setIsolated(isolated: boolean): void {
    this.isolated = isolated;
    this.invalidate();
  }

  /** Spread the plates apart while browsing layers (the layer picker is open). */
  setSpread(spread: boolean): void {
    this.target.spread = spread ? 1 : 0;
    this.invalidate();
  }

  reset(): void {
    this.target = { ...DEFAULT_CAMERA, layer: this.selection.layer };
    this.overview = null;
    this.isolated = false;
    this.callbacks.onDetail?.(false);
    this.invalidate();
  }

  /** Screen position of a neuron, in canvas CSS pixels (null when off screen or hidden). */
  project(layer: number, neuron: number): { x: number; y: number } | null {
    const v = this.worldOf(layer, neuron).project(this.camera3);
    if (v.z > 1 || Math.abs(v.x) > 1.05 || Math.abs(v.y) > 1.05) return null;
    return { x: ((v.x + 1) / 2) * this.width, y: ((1 - v.y) / 2) * this.height };
  }

  invalidate(): void {
    if (this.disposed) return;
    if (this.frame === null && !document.hidden) {
      this.frame = requestAnimationFrame((t) => this.draw(t));
    }
    // A hidden pane (or a backgrounded tab) never fires rAF: draw the settled
    // frame anyway, so the canvas never sits stale or blank.
    if (this.fallback === null) {
      this.fallback = setTimeout(() => {
        this.fallback = null;
        if (this.disposed || this.frame === null) return;
        cancelAnimationFrame(this.frame);
        this.frame = null;
        this.camera = { ...this.target };
        this.velocity = { ...ZERO_VELOCITY };
        this.render(performance.now());
      }, 250);
    }
  }

  destroy(): void {
    this.disposed = true;
    this.events.abort();
    this.resizeObserver.disconnect();
    this.themeObserver.disconnect();
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    if (this.fallback !== null) clearTimeout(this.fallback);
    for (const p of this.plates) {
      p.points.geometry.dispose();
      p.material.dispose();
      p.outlineMaterial.dispose();
      p.rulesMaterial.dispose();
      p.label.remove();
    }
    this.marks.geometry.dispose();
    for (const d of this.disposables) d.dispose();
    this.renderer.dispose();
  }

  // ── building ──────────────────────────────────────────────────────────────

  private buildPlate(
    l: number,
    outlineGeometry: THREE.BufferGeometry,
    rulesGeometry: THREE.BufferGeometry,
  ): Plate {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', this.sharedPositions);
    const heat = new THREE.BufferAttribute(new Float32Array(this.grid.ffn), 1);
    heat.setUsage(THREE.DynamicDrawUsage);
    geometry.setAttribute('heat', heat);
    const material = new THREE.ShaderMaterial({
      vertexShader: POINT_VERTEX,
      fragmentShader: POINT_FRAGMENT,
      uniforms: {
        uSize: { value: this.spacing * 0.42 },
        uScale: { value: 1 },
        uAlpha: { value: 1 },
        uPos: { value: new THREE.Color() },
        uNeg: { value: new THREE.Color() },
        uNeutral: { value: new THREE.Color() },
      },
      transparent: true,
      depthWrite: false,
    });
    const points = new THREE.Points(geometry, material);
    points.frustumCulled = false;
    const outlineMaterial = new THREE.LineBasicMaterial({ transparent: true, depthWrite: false });
    const outline = new THREE.LineLoop(outlineGeometry, outlineMaterial);
    const rulesMaterial = new THREE.LineBasicMaterial({ transparent: true, depthWrite: false });
    const rules = new THREE.LineSegments(rulesGeometry, rulesMaterial);
    const group = new THREE.Group();
    group.add(rules, outline, points);
    this.scene.add(group);

    const label = document.createElement('button');
    label.type = 'button';
    label.className = 'nrn-layer-label';
    label.textContent = `L${l}`;
    label.tabIndex = -1;
    label.addEventListener('click', () => this.callbacks.onLayer?.(l), {
      signal: this.events.signal,
    });
    this.labels.append(label);
    return { group, points, material, heat, outline, outlineMaterial, rules, rulesMaterial, label };
  }

  private applyColors(): void {
    const c = this.colors;
    for (const p of this.plates) {
      p.material.uniforms.uPos.value.copy(c.pos);
      p.material.uniforms.uNeg.value.copy(c.neg);
      p.material.uniforms.uNeutral.value.copy(c.neutral);
      p.rulesMaterial.color.copy(c.line);
    }
    (this.ring.material as THREE.MeshBasicMaterial).color.copy(c.accent);
    (this.pulse.material as THREE.MeshBasicMaterial).color.copy(c.accent);
    (this.hoverBox.material as THREE.LineBasicMaterial).color.copy(c.focus);
  }

  // ── geometry ──────────────────────────────────────────────────────────────

  private gap(camera = this.camera): number {
    return 1.15 + camera.spread * 0.45;
  }

  private plateX(l: number, camera = this.camera): number {
    return -l * this.gap(camera);
  }

  private plane(l: number): LayerPlane {
    const { cols, rows } = this.grid;
    return {
      origin: {
        x: this.plateX(l),
        y: ((rows - 1) / 2) * this.spacing,
        z: ((cols - 1) / 2) * this.spacing,
      },
      colAxis: { x: 0, y: 0, z: -1 },
      rowAxis: { x: 0, y: -1, z: 0 },
      spacing: this.spacing,
    };
  }

  private worldOf(layer: number, neuron: number): THREE.Vector3 {
    const { row, col } = cellOf(neuron, this.grid);
    const { cols, rows } = this.grid;
    return new THREE.Vector3(
      this.plateX(layer),
      ((rows - 1) / 2 - row) * this.spacing,
      ((cols - 1) / 2 - col) * this.spacing,
    );
  }

  private centerOn(neuron: number): void {
    const { row, col } = cellOf(neuron, this.grid);
    this.target = clampCamera(
      {
        ...this.target,
        panX: col - (this.grid.cols - 1) / 2,
        panY: row - (this.grid.rows - 1) / 2,
      },
      { grid: this.grid },
    );
  }

  private leaveDetail(): void {
    this.target.zoom = 1;
    this.target.panX = this.target.panY = 0;
    if (this.overview) Object.assign(this.target, this.overview);
    this.overview = null;
    this.callbacks.onDetail?.(false);
  }

  private resize(): void {
    const r = this.canvas.getBoundingClientRect();
    if (!r.width || !r.height) return;
    this.width = r.width;
    this.height = r.height;
    this.renderer.setSize(r.width, r.height, false);
    this.camera3.aspect = r.width / r.height;
    this.camera3.updateProjectionMatrix();
    this.invalidate();
  }

  private placeCamera(): void {
    const c = this.camera;
    const target = new THREE.Vector3(
      this.plateX(c.layer),
      -c.panY * this.spacing,
      -c.panX * this.spacing,
    );
    const d = BASE_DISTANCE / c.zoom;
    this.camera3.position.set(
      target.x + d * Math.sin(c.yaw) * Math.cos(c.pitch),
      target.y + d * Math.sin(c.pitch),
      target.z + d * Math.cos(c.yaw) * Math.cos(c.pitch),
    );
    this.camera3.lookAt(target);
    this.camera3.updateMatrixWorld();
  }

  // ── drawing ───────────────────────────────────────────────────────────────

  private draw(time: number): void {
    this.frame = null;
    if (this.disposed) return;
    // rAF fired, so this pane is live: the snap-to-target fallback is not needed.
    if (this.fallback !== null) {
      clearTimeout(this.fallback);
      this.fallback = null;
    }
    const dt = this.lastTime ? (time - this.lastTime) / 1000 : 1 / 60;
    this.lastTime = time;
    let moving: boolean;
    if (this.reducedMotion.matches) {
      this.camera = { ...this.target };
      this.velocity = { ...ZERO_VELOCITY };
      moving = false;
    } else {
      const s = stepCamera(this.camera, this.velocity, this.target, dt);
      this.camera = s.camera;
      this.velocity = s.velocity;
      moving = s.moving;
    }
    const pulsing = performance.now() - this.pulseStart < PULSE_MS;
    this.render(time);
    if (moving || pulsing) {
      this.frame = requestAnimationFrame((t) => this.draw(t));
    } else {
      this.lastTime = 0;
    }
  }

  private render(now: number): void {
    this.placeCamera();
    const c = this.camera;
    const compact = this.width <= COMPACT_WIDTH;
    const reach = compact ? 2 : 4;
    const detailFade = Math.min(1, Math.max(0.06, 2.8 - c.zoom));
    const scale =
      (this.height * this.renderer.getPixelRatio()) / (2 * Math.tan((FOV * Math.PI) / 360));
    const sel = this.selection;
    const showLabels = c.zoom <= DETAIL_ZOOM;

    this.plates.forEach((p, l) => {
      const d = Math.abs(l - c.layer);
      const focused = l === sel.layer;
      const visible = d < reach + 0.01 && (!this.isolated || focused);
      p.group.visible = visible;
      p.group.position.x = this.plateX(l);
      if (!visible) {
        p.label.hidden = true;
        return;
      }
      const near = Math.exp(-d * 1.7);
      const fade = Math.max(0.28, 1 - d / 7) * (focused ? 1 : detailFade);
      const u = p.material.uniforms;
      u.uScale.value = scale;
      u.uSize.value = this.spacing * (focused ? 0.46 : 0.34);
      u.uAlpha.value = (focused ? 0.95 : 0.5) * fade;
      p.outlineMaterial.color.copy(
        l === this.hoverLayer ? this.colors.accent : focused ? this.colors.focus : this.colors.line,
      );
      p.outlineMaterial.opacity = (0.35 + near * 0.35 + (l === this.hoverLayer ? 0.3 : 0)) * fade;
      p.rulesMaterial.opacity = (0.06 + near * 0.08) * fade;

      // The plate's label above its top-left corner, while not inspecting.
      if (showLabels && fade > 0.2) {
        const corner = new THREE.Vector3(
          this.plateX(l),
          ((this.grid.rows - 1) / 2 + 2.5) * this.spacing,
          ((this.grid.cols - 1) / 2) * this.spacing,
        ).project(this.camera3);
        const x = ((corner.x + 1) / 2) * this.width;
        const y = ((1 - corner.y) / 2) * this.height;
        const onScreen =
          corner.z < 1 && x > 8 && x < this.width - 8 && y > 8 && y < this.height - 8;
        p.label.hidden = !onScreen;
        if (onScreen) {
          p.label.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
          p.label.style.opacity = String(Math.min(1, fade * 1.2));
          p.label.classList.toggle('focused', focused);
          p.label.classList.toggle('hovered', l === this.hoverLayer);
        }
      } else {
        p.label.hidden = true;
      }
    });

    // Marks on visible plates.
    const shown = this.markList.filter((m) => this.plates[m.layer]?.group.visible);
    const positions = new Float32Array(shown.length * 3);
    const colors = new Float32Array(shown.length * 3);
    const amounts = new Float32Array(shown.length);
    shown.forEach((m, i) => {
      this.worldOf(m.layer, m.neuron).toArray(positions, i * 3);
      const col =
        m.polarity > 0 ? this.colors.pos : m.polarity < 0 ? this.colors.neg : this.colors.neutral;
      col.toArray(colors, i * 3);
      amounts[i] = m.amount;
    });
    const g = this.marks.geometry;
    g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    g.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    g.setAttribute('amount', new THREE.BufferAttribute(amounts, 1));
    this.marksMaterial.uniforms.uScale.value = scale;

    // Selection ring and its pulse.
    const at = this.worldOf(sel.layer, sel.neuron);
    const selVisible = this.plates[sel.layer]?.group.visible ?? false;
    this.ring.visible = selVisible;
    this.ring.position.copy(at);
    this.ring.scale.setScalar(this.spacing * 1.7);
    const since = now - this.pulseStart;
    this.pulse.visible = selVisible && since >= 0 && since < PULSE_MS;
    if (this.pulse.visible) {
      const t = since / PULSE_MS;
      this.pulse.position.copy(at);
      this.pulse.scale.setScalar(this.spacing * (1.7 + t * 5));
      (this.pulse.material as THREE.MeshBasicMaterial).opacity = 0.5 * (1 - t) ** 2;
    }

    // Hover square.
    this.hoverBox.visible = this.hover !== null;
    if (this.hover) {
      this.hoverBox.position.copy(this.worldOf(this.hover.layer, this.hover.neuron));
      this.hoverBox.scale.setScalar(this.spacing * 0.75);
    }

    this.renderer.render(this.scene, this.camera3);
  }

  // ── input ─────────────────────────────────────────────────────────────────

  private ray(x: number, y: number): { origin: Vec3; dir: Vec3 } {
    const ndc = new THREE.Vector2((x / this.width) * 2 - 1, -(y / this.height) * 2 + 1);
    const rc = new THREE.Raycaster();
    rc.setFromCamera(ndc, this.camera3);
    const o = rc.ray.origin;
    const d = rc.ray.direction;
    return { origin: { x: o.x, y: o.y, z: o.z }, dir: { x: d.x, y: d.y, z: d.z } };
  }

  /**
   * What is under (x, y): a neuron on the focused plate wins; otherwise the nearest
   * other visible plate the ray crosses inside its grid. Nothing while traversing.
   */
  private pick(
    x: number,
    y: number,
  ): { kind: 'neuron' | 'layer'; layer: number; neuron: number } | null {
    if (Math.abs(this.camera.layer - this.selection.layer) > 0.12) return null;
    const { origin, dir } = this.ray(x, y);
    const sel = this.selection.layer;
    if (this.plates[sel]?.group.visible) {
      const n = pickCell(origin, dir, this.plane(sel), this.grid);
      if (n !== null) return { kind: 'neuron', layer: sel, neuron: n };
    }
    let best: { layer: number; neuron: number; t: number } | null = null;
    this.plates.forEach((p, l) => {
      if (l === sel || !p.group.visible) return;
      const n = pickCell(origin, dir, this.plane(l), this.grid);
      if (n === null) return;
      // Distance along the ray to that plate's x.
      const t = Math.abs((this.plateX(l) - origin.x) / (dir.x || 1e-9));
      if (!best || t < best.t) best = { layer: l, neuron: n, t };
    });
    const b = best as { layer: number; neuron: number; t: number } | null;
    return b ? { kind: 'layer', layer: b.layer, neuron: b.neuron } : null;
  }

  private local(e: PointerEvent | MouseEvent): { x: number; y: number } {
    const r = this.canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  private setHover(h: Hover | null, layer: number | null): void {
    const key = (v: Hover | null) => (v ? `${v.layer}:${v.neuron}` : '');
    if (key(h) === key(this.hover) && layer === this.hoverLayer) {
      if (h && this.hover) {
        this.hover.x = h.x;
        this.hover.y = h.y;
      }
      return;
    }
    this.hover = h;
    this.hoverLayer = layer;
    this.callbacks.onHover?.(h);
    this.canvas.style.cursor = layer !== null ? 'pointer' : h ? 'crosshair' : 'grab';
    this.invalidate();
  }

  private bindInput(): void {
    const on = <K extends keyof HTMLElementEventMap>(
      type: K,
      fn: (e: HTMLElementEventMap[K]) => void,
      opts: AddEventListenerOptions = {},
    ) => this.canvas.addEventListener(type, fn, { ...opts, signal: this.events.signal });
    const spread = () => {
      const [a, b] = [...this.pointers.values()];
      return Math.hypot(a.x - b.x, a.y - b.y);
    };

    on('pointerdown', (e) => {
      this.canvas.setPointerCapture(e.pointerId);
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (this.pointers.size === 1) {
        this.gesture = {
          moved: 0,
          pinched: false,
          pan: e.shiftKey || e.button === 2 || this.target.zoom > DETAIL_ZOOM,
        };
      } else if (this.gesture) {
        this.gesture.pinched = true;
        this.gesture.distance = spread();
      }
      this.setHover(null, null);
      this.callbacks.onInteraction?.();
    });
    on('contextmenu', (e) => e.preventDefault());
    on('pointermove', (e) => {
      const prev = this.pointers.get(e.pointerId);
      if (prev) {
        this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (!this.gesture) return;
        if (this.pointers.size > 1) {
          const dist = spread();
          if (this.gesture.distance) this.zoomBy(dist / this.gesture.distance);
          this.gesture.distance = dist;
          return;
        }
        const dx = e.clientX - prev.x;
        const dy = e.clientY - prev.y;
        this.gesture.moved += Math.abs(dx) + Math.abs(dy);
        if (this.gesture.pan) {
          // Pixels → cells at the target plate's distance.
          const worldPerPx =
            (2 * (BASE_DISTANCE / this.target.zoom) * Math.tan((FOV * Math.PI) / 360)) /
            this.height;
          const cells = worldPerPx / this.spacing;
          this.target = clampCamera(
            {
              ...this.target,
              panX: this.target.panX - (dx * cells) / Math.max(0.3, Math.sin(this.target.yaw)),
              panY: this.target.panY - dy * cells,
            },
            { grid: this.grid },
          );
        } else {
          this.target = clampCamera({
            ...this.target,
            yaw: this.target.yaw - dx * 0.004,
            pitch: this.target.pitch + dy * 0.003,
          });
        }
        this.invalidate();
        return;
      }
      if (e.pointerType === 'touch') return;
      const p = this.local(e);
      const hit = this.pick(p.x, p.y);
      if (hit?.kind === 'neuron') {
        this.setHover({ layer: hit.layer, neuron: hit.neuron, x: p.x, y: p.y }, null);
      } else {
        this.setHover(null, hit?.layer ?? null);
      }
    });
    const end = (e: PointerEvent, cancelled: boolean) => {
      if (!this.pointers.has(e.pointerId)) return;
      const g = this.gesture;
      this.pointers.delete(e.pointerId);
      if (!this.pointers.size) this.gesture = null;
      if (!cancelled && g && !g.pinched && g.moved < 7 && e.button !== 2) {
        const p = this.local(e);
        const hit = this.pick(p.x, p.y);
        if (hit?.kind === 'layer') this.callbacks.onLayer?.(hit.layer);
        else if (hit) this.callbacks.onSelect?.({ layer: hit.layer, neuron: hit.neuron });
      }
      this.invalidate();
    };
    on('pointerup', (e) => end(e, false));
    on('pointercancel', (e) => end(e, true));
    on('lostpointercapture', (e) => end(e, true));
    on('pointerleave', () => this.setHover(null, null));
    on(
      'wheel',
      (e) => {
        e.preventDefault();
        const dy = Math.max(-150, Math.min(150, e.deltaY));
        this.zoomBy(Math.exp(-dy * 0.0018));
      },
      { passive: false },
    );
    on('dblclick', (e) => {
      e.preventDefault();
      this.inspect();
    });
  }
}
