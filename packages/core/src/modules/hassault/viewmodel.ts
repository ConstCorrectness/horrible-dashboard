/**
 * The gun in your hands — a first-person view model, built out of primitives.
 *
 * There was nothing here before: the player was a floating camera with a
 * crosshair, and the only evidence of a weapon was the HUD's ammo counter. This
 * draws the thing the counter is about.
 *
 * **Procedural, like everything else in this module.** AssaultCube's weapon models
 * are its copyright and are never bundled (docs/modules/hassault.mdx), so these
 * are boxes and cylinders in the shape of a gun: a receiver, a barrel, a magazine,
 * a stock. Untextured and unmistakably geometric — but a shotgun reads as a
 * shotgun and a sniper reads as a sniper, which is the whole job. When real
 * (synthesized) models land they replace `build`, and nothing else here changes.
 *
 * Parented to the **camera**, not to the scene: a view model has no world
 * position, it has a position in front of your eyes, and the alternative is
 * recomputing its transform from the camera's every frame and getting it subtly
 * wrong on the frame the camera moves. three only renders camera children when the
 * camera is itself in the scene graph, which is why the constructor adds it.
 *
 * Takes `three` as a parameter rather than importing it, like `avatars.ts` and
 * `effects.ts`, so the lazy-load stays in one place.
 */
import type * as THREE from 'three';

import { ArmRig, gripsFor, type GripAnchors, type Vec3 } from './arms';
import {
  blendCurl,
  clipFor,
  inspectClipFor,
  knifeArchetype,
  knifePropId,
  sampleInspect,
  type InspectClip,
  type InspectSample,
} from './inspects';
import {
  PROP_ENV_INTENSITY,
  fitKnifeModel,
  fitWeaponModel,
  loadWeaponModel,
} from './models/weapons';
import {
  AuthoredPoseSource,
  curlOver,
  DRAW_DURATION,
  THROW_DURATION,
  mergePose,
  selectLocomotion,
  type ActionClip,
  type PartialPose,
  type PoseSource,
} from './viewclips';

import {
  FLASH_CORE,
  FLASH_HALO,
  FLASH_LIFE,
  FLASH_SMOKE,
  FLASH_SMOKE_LIFE,
  FLASH_SMOKE_RISE,
  clampFlashScale,
  createFlashTexture,
  drawFlashTile,
  drawSmokeTile,
  flashScale,
  haloScale,
  smokePuff,
} from './flash';

import { MOVE_SPEED } from './player';
import { createDetailTexture } from './surfaces';

/**
 * Where the weapon rests, in camera space: right hand, below the sight line.
 *
 * The sizes below are in cube units, which are worth a sanity check: the eye sits
 * 4.5 cubes up and eyes are about 1.6 m off the ground, so a cube is roughly
 * 36 cm and a 90 cm rifle is about two and a half cubes long. That is why the
 * models are the length they are rather than whatever looked right on one screen.
 */
const HOME = { x: 0.58, y: -0.46, z: -0.98 };
/** Centered ADS aim position aligned with sights line */
const ADS_POS = { x: 0.0, y: -0.38, z: -0.85 };

/** Recoil decay and reload-dip rates, per second. */
const KICK_DECAY = 11;
const RELOAD_RATE = 6;

/**
 * Fractions of a reload spent taking the weapon down, and bringing it back.
 *
 * **Fractions, not seconds**, so the dip stretches to whatever `reloadTime` the
 * server serves: a 1.4s pistol reload and a 2.6s shotgun reload both come back
 * up on the frame the magazine is full. Written in seconds the two would need a
 * table nobody would keep in step with `weapons.py`.
 *
 * This is a port of the native client's `reload_envelope`, which has had it for
 * a while — the browser kept a fixed-rate exponential approach that did neither
 * end well: a fast reload was still on its way down when the magazine filled,
 * and a slow one sat at the bottom and then snapped back. Nothing caught that
 * divergence, so `browser_parity.rs` now pins all six numbers here.
 */
const RELOAD_DIP_IN = 0.22;
const RELOAD_DIP_OUT = 0.3;

/**
 * Seconds to take a weapon out of frame, and to bring the next one up.
 *
 * The holster is faster than the draw on purpose: putting something away is a
 * motion you have already committed to, while bringing one up is the moment that
 * has to read as an *arrival*. Equal times make a swap look like one mechanical
 * sweep in a single direction.
 */
const HOLSTER_TIME = 0.13;
const DRAW_TIME = 0.25;

/**
 * How long the weapon waits down after a switch was *asked for*.
 *
 * The client does not own the slot — in a match `select` sends a request and the
 * server decides — so the holster is an anticipation, and this is how long it is
 * willing to be wrong for: comfortably past a round trip, and short enough that
 * a refused switch does not leave a player staring at their own knees. A
 * confirmed swap cancels the hold outright rather than waiting out the rest.
 */
const HOLSTER_HOLD = 0.4;

/**
 * The reload dip's weight across the reload's own length: down, hold, up.
 *
 * Takes a **fraction**, so the shape is the same on every weapon and its
 * duration is whatever the server said.
 */
export function reloadEnvelope(progress: number): number {
  const p = Math.max(0, Math.min(1, progress));
  if (p < RELOAD_DIP_IN) return ease(p / RELOAD_DIP_IN);
  if (p > 1 - RELOAD_DIP_OUT) return ease((1 - p) / RELOAD_DIP_OUT);
  return 1;
}

/**
 * Move `value` toward `target` at a fixed rate, arriving exactly.
 *
 * Linear rather than the exponential approach used for sway and kick, because a
 * swap is an *action with a length*: an exponential never arrives, so the weapon
 * would be perpetually a few percent stowed and the draw would have no moment at
 * which it is over.
 */
function approach(value: number, target: number, seconds: number, dt: number): number {
  const step = dt / Math.max(1e-4, seconds);
  return target > value ? Math.min(target, value + step) : Math.max(target, value - step);
}

export interface ViewModelFrame {
  /** Horizontal speed in cubes per second, for the walk cycle. */
  speed: number;
  onGround: boolean;
  reloading: boolean;
  /** Whether the in-progress reload is from an empty magazine (dry reload). */
  reloadingEmpty?: boolean;
  /**
   * How far through the reload we are, 0..1, or `null` when that cannot be known
   * — the weapon's `reloadTime` has not been served, or is zero.
   *
   * `null` rather than a bare number, and the distinction is load-bearing: `0`
   * is "the reload has just started" and `null` is "there is no length to
   * measure against". Collapsed into one number, a server that serves no
   * `reloadTime` would look like a reload permanently at its first frame — the
   * weapon would go down and stay there.
   */
  reloadProgress?: number | null;
  /** View angles, so the weapon can lag a turn slightly instead of being welded on. */
  yaw: number;
  pitch: number;
  /** Lateral strafe input (-1 to 1) for physics-based strafe inertia. */
  strafe?: number;
  /** Whether player is actively sprinting */
  sprint?: boolean;
  /** False while dead, spectating, or before deploying. */
  visible: boolean;
  /**
   * Seconds since the player last landed, for the landing dip.
   *
   * A duration rather than a timestamp, the same shape `you.move.sinceLanded`
   * has and for the same reason: the two simulated clocks are unrelated, and a
   * timestamp from one measured against the other is a number with no meaning.
   */
  sinceLanded?: number;
  /** Aim-down-sights weight (0 hipfire to 1 ADS). Lerps weapon model towards sight-line. */
  ads?: number;
}

/**
 * The equipped skin for the weapon in your hands.
 *
 * Only the four things a *procedural* weapon can actually express. The skin
 * economy also carries a rarity, a collection, a pattern seed and a name, and
 * none of those change what a box made of boxes looks like — so none of them are
 * here. What does change it: two colours, how they are laid out, and the wear.
 */
export interface WeaponSkin {
  id?: string;
  name?: string;
  baseColor: string;
  accentColor: string;
  /** `solid` | `camo` | `anodized` | `custom_art` | `patina` | `fade`. */
  patternType: string;
  /** 0 Factory New … 1 Battle-Scarred. */
  floatValue: number;
}

/**
 * The equipped skin for each weapon, keyed by weapon id.
 *
 * Takes the whole inventory because that is what the node serves — there is no
 * "what am I wearing" route, and asking for one to save this four-line filter
 * would be a second source of truth for the same fact.
 *
 * An instance whose definition did not come with it is **skipped rather than
 * guessed**: without `baseColor` there is no skin to apply, and inventing one
 * would put a colour on the weapon that the armoury never showed the player.
 */
export function equippedSkins(
  inventory: {
    isEquipped: boolean;
    floatValue: number;
    definition?: {
      id?: string;
      name?: string;
      weaponId: string;
      baseColor: string;
      accentColor: string;
      patternType: string;
    };
  }[],
): Record<string, WeaponSkin> {
  const out: Record<string, WeaponSkin> = {};
  for (const item of inventory) {
    if (!item.isEquipped || !item.definition) continue;
    out[item.definition.weaponId] = {
      id: item.definition.id,
      name: item.definition.name,
      baseColor: item.definition.baseColor,
      accentColor: item.definition.accentColor,
      patternType: item.definition.patternType,
      floatValue: item.floatValue,
    };
  }
  return out;
}

/** The unskinned weapon: the palette every gun had before the armoury existed. */
const DEFAULT_PALETTE = {
  body: 0x3a4048,
  dark: 0x1c2026,
  grip: 0x4a3f33,
  accent: 0x8a929c,
};

/** Where a skin's colours go, once wear has been applied. */
interface Palette {
  body: number;
  dark: number;
  grip: number;
  accent: number;
}

/** `#rrggbb` to a three-friendly integer. Anything unparseable falls back. */
function parseColor(value: string, fallback: number): number {
  const hex = /^#?([0-9a-f]{6})$/i.exec(value.trim());
  return hex ? parseInt(hex[1], 16) : fallback;
}

function mix(a: number, b: number, t: number): number {
  const f = Math.max(0, Math.min(1, t));
  const out = [16, 8, 0].map((shift) => {
    const ca = (a >> shift) & 0xff;
    const cb = (b >> shift) & 0xff;
    return Math.round(ca + (cb - ca) * f);
  });
  return (out[0] << 16) | (out[1] << 8) | out[2];
}

/** Grime. What a Battle-Scarred rifle is mixed toward. */
const WEAR_COLOR = 0x5a554e;

/**
 * The darkest a *skinned* surface is allowed to be.
 *
 * `assault_slate`'s base colour is `#09090b`, which is a legitimate design and
 * draws as a gun-shaped hole: there are no speculars on these materials and the
 * weapon sits in the darkest corner of the screen. The floor keeps the skin's hue
 * and lifts only its brightness — the smallest lie that leaves the weapon
 * readable — and it is applied **only to skins**, so a player carrying none sees
 * exactly the palette they always did.
 */
const MIN_LUMA = 0.14;

function luma(hex: number): number {
  return (0.299 * ((hex >> 16) & 0xff) + 0.587 * ((hex >> 8) & 0xff) + 0.114 * (hex & 0xff)) / 255;
}

function lift(hex: number): number {
  const l = luma(hex);
  if (l >= MIN_LUMA) return hex;
  return mix(hex, 0xffffff, (MIN_LUMA - l) / Math.max(1e-3, 1 - l));
}

/**
 * A skin's colours, arranged for a weapon made of boxes.
 *
 * **Wear is applied here rather than being decoration**, because a float value
 * you cannot see is a number the whole economy is built on and nobody can check.
 * Factory New (0.03) is essentially untouched; Battle-Scarred (0.8) is visibly
 * dulled toward grime. It is a mix rather than a texture for the same reason
 * everything else in this module is procedural — there is no texture set, and
 * shipping one would be shipping somebody else's work.
 *
 * `patternType` cannot be a *pattern* without textures either, so it decides how
 * the two colours are distributed across the parts instead. That is enough for a
 * Fade to read as a fade and a Camo not to read as a Slate.
 */
function paletteFor(skin: WeaponSkin | null): Palette {
  if (!skin) return { ...DEFAULT_PALETTE };
  const base = parseColor(skin.baseColor, DEFAULT_PALETTE.body);
  const accent = parseColor(skin.accentColor, DEFAULT_PALETTE.accent);
  let palette: Palette;
  switch (skin.patternType) {
    case 'fade':
      // Two colours across the length of the weapon, which is what a fade is.
      palette = {
        body: base,
        dark: mix(base, accent, 0.5),
        grip: accent,
        accent: mix(accent, 0xffffff, 0.25),
      };
      break;
    case 'camo':
      // Blotches are not available, so the parts alternate instead.
      palette = {
        body: base,
        dark: mix(base, 0x000000, 0.55),
        grip: mix(base, accent, 0.65),
        accent: mix(base, 0x000000, 0.3),
      };
      break;
    case 'anodized':
      // Metal dyed in one colour, with bright hardware.
      palette = {
        body: base,
        dark: mix(base, 0x000000, 0.4),
        grip: mix(base, 0x000000, 0.65),
        accent,
      };
      break;
    case 'patina':
      // Case-hardened and Damascus: the *metal* is the pattern, so the two
      // colours sit next to each other on the hardware and the furniture stays
      // out of it. Without this branch a Case Hardened drew as a Slate.
      palette = {
        body: mix(base, accent, 0.35),
        dark: mix(accent, 0x000000, 0.35),
        grip: mix(base, 0x000000, 0.7),
        accent: mix(base, 0xffffff, 0.2),
      };
      break;
    case 'custom_art':
      // Painted art: high contrast is the whole look, so the accent gets whole
      // parts rather than trim, and the two colours never meet in a mix.
      palette = {
        body: base,
        dark: accent,
        grip: mix(base, 0x000000, 0.72),
        accent: mix(accent, 0xffffff, 0.3),
      };
      break;
    default:
      // `solid`, and anything new: the base carries the weapon and the accent
      // picks out the barrel and the sights.
      palette = {
        body: base,
        dark: mix(base, 0x000000, 0.5),
        grip: mix(accent, 0x000000, 0.5),
        accent,
      };
  }
  const wear = Math.max(0, Math.min(1, skin.floatValue)) * 0.55;
  return {
    body: lift(mix(palette.body, WEAR_COLOR, wear)),
    dark: lift(mix(palette.dark, WEAR_COLOR, wear * 0.7)),
    grip: lift(mix(palette.grip, WEAR_COLOR, wear)),
    accent: lift(mix(palette.accent, WEAR_COLOR, wear)),
  };
}

/** Scale every channel of a pose. Used to fade a walk cycle in with the bob. */
function scalePose(pose: PartialPose, k: number): PartialPose {
  const out: PartialPose = {};
  if (pose.primary) out.primary = [pose.primary[0] * k, pose.primary[1] * k, pose.primary[2] * k];
  if (pose.support) out.support = [pose.support[0] * k, pose.support[1] * k, pose.support[2] * k];
  if (pose.primaryRoll !== undefined) out.primaryRoll = pose.primaryRoll * k;
  if (pose.supportRoll !== undefined) out.supportRoll = pose.supportRoll * k;
  return out;
}

/** Two optional offsets, summed; absent is zero. */
function addVec(a: Vec3 | undefined, b: Vec3 | undefined): Vec3 | undefined {
  if (!a) return b;
  if (!b) return a;
  return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
}

/** `v` turned by `angle` about the unit-ish `axis` (Rodrigues). */
function rollAbout(v: Vec3, axis: Vec3, angle: number): Vec3 {
  if (Math.abs(angle) < 1e-6) return v;
  const l = Math.hypot(axis[0], axis[1], axis[2]) || 1;
  const k: Vec3 = [axis[0] / l, axis[1] / l, axis[2] / l];
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  const dot = k[0] * v[0] + k[1] * v[1] + k[2] * v[2];
  const cross: Vec3 = [k[1] * v[2] - k[2] * v[1], k[2] * v[0] - k[0] * v[2], k[0] * v[1] - k[1] * v[0]];
  return [0, 1, 2].map((i) => v[i] * c + cross[i] * s + k[i] * dot * (1 - c)) as Vec3;
}

/** A grip anchor plus a pose offset. An absent offset leaves the hand on the gun. */
function offsetBy(anchor: Vec3, offset: Vec3 | undefined): Vec3 {
  if (!offset) return anchor;
  return [anchor[0] + offset[0], anchor[1] + offset[1], anchor[2] + offset[2]];
}

/** One weapon's geometry, as `build` hands it back. */
interface Shape {
  group: THREE.Group;
  /** Muzzle position in the model's own space, where the flash goes. */
  muzzle: [number, number, number];
  /** Resting rotation, since a knife is not held like a rifle. */
  rest: [number, number, number];
}

interface Built extends Shape {
  /** This model's own resources, freed when it is swapped out. */
  geometries: THREE.BufferGeometry[];
  materials: THREE.Material[];
  /** Overwritten when a prop lands and the muzzle moves to its barrel. */
  muzzle: [number, number, number];
}

/**
 * One weapon in the hands, swapped by id.
 *
 * `setWeapon` is idempotent, so the render loop can call it every frame with
 * whatever the server last said we are holding and only pay for real changes.
 */
/**
 * Smootherstep — Perlin's, with a continuous second derivative.
 *
 * Smoothstep's acceleration jumps at both ends; over a 0.3s rise that is a
 * visible tick as the weapon leaves rest.
 */
function ease(x: number): number {
  const c = clamp(x, 0, 1);
  // Clamped on the way out as well as in. The polynomial is monotonic on [0,1]
  // and cannot exceed 1 algebraically, but in floats it lands on
  // 1.0000000000000013 near the top — enough to fail the envelope's own bound
  // check, and enough for a caller that trusts the range to scale a pose very
  // slightly past the pose it was told about.
  return clamp(c * c * c * (c * (c * 6 - 15) + 10), 0, 1);
}

export class WeaponViewModel {
  /** The pivot everything hangs off: animation moves this, never the model. */
  public readonly pivot: THREE.Group;
  private built: Built | null = null;
  private weaponId = '';
  /** The skin the current model was built with, so a change of skin rebuilds it
   * and an unchanged one does not. */
  private skinKey = '';
  private currentSkin: WeaponSkin | null = null;
  /** Geometries created by the build in progress, collected by `box`/`tube`. */
  private building: THREE.BufferGeometry[] = [];

  /**
   * Which weapon+skin the in-flight prop load belongs to.
   *
   * A swap mid-download is the whole reason this exists: the fetch is async and
   * `setWeapon` is not, so a pistol's GLB can land after the player has already
   * switched to the sniper. Stamping the request and checking it on arrival is
   * what stops the wrong gun appearing in your hands a second after you changed
   * weapons — a race that is invisible on a fast connection and reliable on a
   * slow one.
   */
  private propToken = '';

  /**
   * The muzzle flash, as three billboards rather than one cone — see `flash.ts`
   * for what was wrong with the cone and why a sprite cannot repeat it.
   *
   * Three because they are three different events sharing one moment: a hot
   * core, a wider warm fringe, and the wisp that outlives both. Two additive,
   * one not — smoke that adds light is a dimmer flash, not smoke.
   */
  private flashCore: THREE.Sprite | null = null;
  private flashHalo: THREE.Sprite | null = null;
  private flashSmoke: THREE.Sprite | null = null;
  private flashAge = FLASH_LIFE;
  private smokeAge = FLASH_SMOKE_LIFE;
  /** Where the smoke started, so it can drift from there. */
  private smokeOrigin: [number, number, number] = [0, 0, 0];
  /**
   * Which shot this is. Seeds the per-shot size variation, replacing a
   * `Math.random()` that ran *every frame the flash was lit* — so one shot was
   * two or three differently-sized flashes rather than one event.
   */
  private flashSeed = 0;
  /**
   * The hands. Built once and solved onto the weapon's grip anchors every frame
   * — see `arms.ts` for why that means they inherit every animation this class
   * already has, for free.
   */
  private arms: ArmRig | null = null;
  /** Where this weapon's hands go. Re-read on every swap. */
  private grips: GripAnchors = gripsFor('');
  /** Where the poses come from. One implementation today — see `PoseSource`. */
  private poses: PoseSource = new AuthoredPoseSource();
  /** The one-shot action running now, and how far into it, or `null`. */
  private action: { clip: ActionClip; t: number; duration: number } | null = null;
  /** Built once per view model and shared by every weapon it ever holds. */
  private flashTexture: THREE.Texture | null = null;
  private smokeTexture: THREE.Texture | null = null;
  private weaponMixer: THREE.AnimationMixer | null = null;
  private weaponReloadAction: THREE.AnimationAction | null = null;
  private kick = 0;
  private bobPhase = 0;
  private reloadT = 0;
  /** How far out of frame the weapon is, 0..1. 1 is fully stowed. */
  private stow = 0;
  /** Seconds left of a holster that was *asked for* but not yet confirmed. */
  private holsterHold = 0;
  /**
   * Set while the weapon is not being drawn at all, so coming back — respawning,
   * or the match starting — plays a draw rather than having the gun materialise
   * at rest. A spawn is an arrival.
   */
  private drawOnReturn = true;
  private lastYaw: number | null = null;
  private lastPitch = 0;
  private swayX = 0;
  private swayY = 0;
  private swayRoll = 0;
  private strafeSway = 0;
  /** Smoothed walk factor: the *input* is a step function, and a bob that snaps
   * to full amplitude on the frame W goes down looks like a glitch, not a stride. */
  private walk = 0;
  /**
   * Seconds into the inspect animation, or `null` when it is not running.
   *
   * A *duration* rather than a flag plus a separate clock: the pose is a
   * function of how far in it is, and every frame of it — including the fact
   * that it has finished — falls out of one number.
   */
  private inspectT: number | null = null;
  /**
   * The fitted prop, as the inspect needs it: its rest transform, the spin
   * marker, and the named parts a clip may swing. `null` while the boxes are up.
   */
  private prop: {
    model: THREE.Object3D;
    position: THREE.Vector3;
    quaternion: THREE.Quaternion;
    pivot: THREE.Vector3;
    nodes: Map<string, { obj: THREE.Object3D; rest: THREE.Quaternion }>;
  } | null = null;
  /** Current ADS interpolation state, 0 (hipfire) to 1 (full ADS) */
  private adsT = 0;
  /** Active knife melee animation and elapsed seconds */
  private knifeAction: 'slash' | 'stab' | null = null;
  private knifeActionT = 0;
  /** Idle respiratory sway phase */
  private breathPhase = 0;
  /** Sprint weapon tuck transition (0 = idle/walk, 1 = tucked sprint) */
  private sprintT = 0;

  // Built with the model rather than shared, because they now carry the skin:
  // two weapons in one match are two different guns, and a material shared
  // between them could only ever show one of them. Freed by `release`.
  private metal!: THREE.MeshPhongMaterial;
  private dark!: THREE.MeshPhongMaterial;
  private grip!: THREE.MeshPhongMaterial;
  private accent!: THREE.MeshPhongMaterial;
  /** Fine grain, shared by all four. Owned here and freed with them. */
  private grain: THREE.Texture | null = null;

  /**
   * What a metallic prop reflects. `null` renders it nearly black — see
   * `createPropEnvironment`, which is why the panel builds one.
   */
  private environment: THREE.Texture | null = null;

  constructor(
    private readonly three: typeof THREE,
    // Not kept: it is only needed to put the camera in the graph, once. The
    // weapon itself is parented to the camera and never touches the scene again.
    scene: THREE.Scene,
    private readonly camera: THREE.Camera,
  ) {
    this.setPalette(null);

    this.pivot = new three.Group();
    this.pivot.position.set(HOME.x, HOME.y, HOME.z);
    // Cleared of the world's fog and lit by the scene's lights like anything
    // else; `renderOrder` keeps it drawn last so it never z-fights a wall it is
    // technically intersecting when you stand with your nose against one.
    this.pivot.renderOrder = 2;
    camera.add(this.pivot);
    // Camera children are only rendered when the camera is in the scene graph.
    if (!camera.parent) scene.add(camera);
    // **On the camera, not on the pivot.** The pivot carries the weapon's bob,
    // sway, kick and stow; the arms have to reach a *point on the weapon* from a
    // shoulder that does not move with it, so they live one level up and the
    // pivot's transform reaches them through the grip anchor instead.
    this.arms = new ArmRig(three, this.pivot.parent ?? camera);
  }

  /**
   * Swap the model. A no-op when already holding this weapon in this skin.
   *
   * The skin is part of the identity, not a property set afterwards: the
   * materials are baked into the built model, so changing one without rebuilding
   * would leave the gun in your hands wearing the previous skin with no sign
   * that anything was applied.
   */
  setWeapon(id: string, skin: WeaponSkin | null = null): void {
    const skinKey = skin
      ? `${skin.id ?? ''}|${skin.baseColor}|${skin.accentColor}|${skin.patternType}|${skin.floatValue}`
      : '';
    if (id === this.weaponId && skinKey === this.skinKey) return;
    const swapped = id !== this.weaponId;
    this.weaponId = id;
    this.skinKey = skinKey;
    this.currentSkin = skin;
    this.release();
    if (!id) return;
    this.setPalette(skin);

    this.building = [];
    const shape = this.build(id, skin);
    this.grips = gripsFor(id);
    this.pivot.add(shape.group);
    shape.group.rotation.set(shape.rest[0], shape.rest[1], shape.rest[2]);

    // The flash lives on the model, not the pivot: it belongs at the end of
    // *this* barrel, and a shared one would sit at the wrong place after a swap.
    const flashMats = this.buildFlash(shape.group, shape.muzzle);
    this.built = {
      ...shape,
      geometries: this.building,
      materials: flashMats,
    };
    this.building = [];

    if (swapped) {
      // Snapped fully stowed rather than eased there: the model has *already*
      // changed, so there is nothing left to take down — easing from here would
      // lower the new weapon out of frame and then raise it again. The hold is
      // cleared because the swap it was waiting for has arrived.
      this.stow = 1;
      this.holsterHold = 0;
    }

    // The boxes are already in your hands by this point. The prop, if there is
    // one, arrives behind them — see `models/weapons.ts` for why this is not
    // awaited.
    this.requestProp(id, skinKey, skin);
  }

  /**
   * Fetch this weapon's prop and swap it in for the boxes when it lands.
   *
   * Every exit is a no-op that leaves the boxes standing: no prop for this
   * weapon, a failed fetch, or the player having swapped weapons since. That is
   * the design — a prop is an upgrade over a working model, never a dependency
   * of one.
   */
  private requestProp(id: string, skinKey: string, skin: WeaponSkin | null): void {
    const token = `${id}|${skinKey}`;
    this.propToken = token;
    const propId =
      id === 'knife' ? knifePropId(knifeArchetype(skin?.id ?? '', skin?.name ?? '')) : id;
    void loadWeaponModel(propId)
      .then((asset) => {
        // Three ways to be stale, and they are all the same check: the weapon
        // changed, the skin changed, or the view model was disposed while the
        // fetch was in flight.
        if (!asset || this.propToken !== token || !this.built) return;
        const built = this.built;
        const { model, muzzle } =
          id === 'knife'
            ? fitKnifeModel(this.three, asset.prototype)
            : fitWeaponModel(this.three, asset.prototype, built.group);

        // The skin tints the prop rather than repainting it: these materials
        // carry real texture maps, and `color` multiplies the base colour map.
        // For special knife archetypes with baked rarity textures, white is identity.
        const isArchetypeKnife = propId.startsWith('knife_');
        const tint = skin && !isArchetypeKnife ? paletteFor(skin).body : 0xffffff;
        const materials: THREE.Material[] = [];
        const wear = skin ? Math.max(0, Math.min(1, skin.floatValue)) : 0.25;
        const isSpecial =
          skin !== null &&
          (skin.patternType === 'fade' ||
            skin.patternType === 'anodized' ||
            skin.patternType === 'patina');
        const baseRoughness = isSpecial ? 0.12 : 0.35;
        const roughness = Math.min(0.95, baseRoughness + wear * 0.55);
        const metalness = isSpecial
          ? Math.max(0.35, 0.92 - wear * 0.45)
          : Math.max(0.1, 0.6 - wear * 0.4);
        const envIntensity = isSpecial
          ? PROP_ENV_INTENSITY * 1.35 * (1 - wear * 0.5)
          : PROP_ENV_INTENSITY * (1 - wear * 0.5);

        model.traverse((obj) => {
          const mesh = obj as THREE.Mesh;
          if (!mesh.isMesh) return;
          for (const mat of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
            const tinted = mat as THREE.MeshStandardMaterial;
            tinted.color?.setHex(tint);
            tinted.roughness = roughness;
            tinted.metalness = metalness;
            // Without this the weapon is a silhouette: a metal has no diffuse
            // term, so analytic lights alone leave it with nothing to return.
            tinted.envMap = this.environment;
            tinted.envMapIntensity = envIntensity;
            tinted.needsUpdate = true;
            materials.push(mat);
          }
        });

        // The boxes go, the flash stays: it belongs to *this* barrel and its
        // position is recomputed from the prop's own muzzle. Removing the
        // children rather than the group keeps the group's rest rotation and
        // everything hanging off it, including the flash.
        const keep = new Set<THREE.Object3D>(
          [this.flashCore, this.flashHalo, this.flashSmoke].filter(
            (s): s is THREE.Sprite => s !== null,
          ),
        );
        for (const child of [...built.group.children]) {
          if (!keep.has(child)) built.group.remove(child);
        }
        for (const geo of built.geometries) geo.dispose();
        built.geometries = [];
        built.group.add(model);
        this.rememberProp(model);
        // The prop is exported already oriented, so it needs none of the box
        // model's resting rotation — that was a property of how the boxes were
        // built, not of how a weapon is held.
        built.group.rotation.set(0, 0, 0);
        built.muzzle = muzzle;
        this.placeFlash(muzzle);
        // Tracked so `release` frees them: a clone owns its own materials.
        built.materials.push(...materials);

        // If the weapon prop has animations (such as the FN FAL reload clip), initialize mixer
        if (asset.animations && asset.animations.length > 0) {
          const mixer = new this.three.AnimationMixer(model);
          const reloadClip =
            asset.animations.find((c) => c.name === 'reload') || asset.animations[0];
          const action = mixer.clipAction(reloadClip);
          action.setLoop(this.three.LoopOnce, 1);
          action.clampWhenFinished = true;
          this.weaponMixer = mixer;
          this.weaponReloadAction = action;
        } else {
          this.weaponMixer = null;
          this.weaponReloadAction = null;
        }
      })
      .catch((err) => {
        // Said once, not swallowed: the boxes still render, so the only symptom
        // of a broken URL is a weapon that never gets its model and no reason
        // given anywhere.
        console.warn(`hassault: could not load the ${id} prop`, err);
      });
  }

  /**
   * Hand the view model the environment its props reflect.
   *
   * Separate from the constructor because building one needs the **renderer**,
   * and the view model deliberately never sees one — it owns a piece of the
   * scene graph, not a way to draw it. Applied to props only, so the world's
   * Lambert surfaces and the operator are untouched by it.
   */
  setEnvironment(environment: THREE.Texture | null): void {
    this.environment = environment;
    this.arms?.setEnvironment(environment);
    const built = this.built;
    if (!built) return;
    // Applied to whatever is already in the hands, so an environment arriving
    // after a prop has loaded is not silently ignored until the next swap.
    built.group.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (!mesh.isMesh) return;
      for (const mat of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
        const standard = mat as THREE.MeshStandardMaterial;
        if (!('envMap' in standard)) continue;
        standard.envMap = environment;
        standard.envMapIntensity = PROP_ENV_INTENSITY;
        standard.needsUpdate = true;
      }
    });
  }

  /**
   * A switch was asked for: take the weapon down while we wait to hear.
   *
   * Called from the key, **not** from the server's answer, and that is the point
   * — a swap that only began once the server confirmed would start a round trip
   * after the press and read as input lag. In a match the server owns the slot,
   * so this is a guess; `HOLSTER_HOLD` is how long the guess is allowed to stand
   * before the weapon comes back up on its own.
   *
   * Purely cosmetic: nothing here changes what is held, what can be fired, or
   * what the wire says. A refused switch costs a dip and nothing else — which is
   * also why equipping a grenade uses it rather than `ShotController.select`,
   * whose request would cancel an in-flight reload server-side.
   */
  holster(): void {
    if (this.built) this.holsterHold = HOLSTER_HOLD;
  }

  /**
   * Start a one-shot hand animation.
   *
   * Replaces whatever was running rather than queueing: two actions on one pair
   * of hands is the blend `models/clips.ts` warns about, and the newer one is
   * always the one the player just asked for.
   */
  private startAction(clip: ActionClip, duration: number): void {
    this.action = { clip, t: 0, duration: Math.max(1e-3, duration) };
  }

  /** The grenade throw's pull-pin, windup and release. */
  throwNade(): void {
    this.startAction('throw', THROW_DURATION);
  }

  /** Knife quick slash: rapid diagonal blade swipe. */
  slash(): void {
    this.knifeAction = 'slash';
    this.knifeActionT = 0;
    this.action = null;
    this.inspectT = null;
  }

  /** Knife heavy stab: powerful forward thrust along -Z. */
  stab(): void {
    this.knifeAction = 'stab';
    this.knifeActionT = 0;
    this.action = null;
    this.inspectT = null;
  }

  /** A shot left the barrel this frame: kick the model and light the muzzle (or slash if knife). */
  fire(): void {
    if (this.weaponId === 'knife') {
      this.slash();
      return;
    }
    // Additive but capped: holding down an assault rifle should climb to a steady
    // shake, not to a weapon behind the player's ear.
    this.kick = Math.min(1, this.kick + 0.8);
    this.flashAge = 0;
    this.smokeAge = 0;
    // One seed per shot, not per frame — see `flashSeed`.
    this.flashSeed += 1;
    // Firing cancels a hand animation for the reason it cancels an inspect: the
    // pose has the support hand somewhere other than the gun, and a shot drawn
    // with a hand in the magazine well is a shot that did not happen that way.
    this.action = null;
    // Firing cancels an inspect, and has to: the pose swings the barrel away
    // from the crosshair, so a shot fired mid-animation would be drawn leaving a
    // weapon pointed at the floor. The server resolved it against the real view
    // angles, which is what the crosshair is still showing.
    this.inspectT = null;
  }

  /**
   * Start the inspect animation — the weapon turned over in the hands.
   *
   * Purely cosmetic and purely local, the same concession client-side recoil
   * makes: it changes nothing about where a shot goes or what anyone else sees,
   * which is exactly what makes it safe to interrupt on any frame. That in turn
   * is what makes it usable in a match rather than a state you have to wait out.
   *
   * Pressing again while it runs restarts it rather than queueing a second pass:
   * the key means "show me the gun", and it should answer every press. A knife
   * is the exception: a second press mid-flourish jumps back to the clip's
   * `repeatFrom`, so mashing F chains twirls instead of re-raising the knife.
   */
  inspect(): void {
    if (!this.built) return;
    const clip = clipFor(this.inspectClipId());
    if (this.weaponId === 'knife' && this.inspectT !== null && clip.repeatFrom !== undefined) {
      this.inspectT = clip.repeatFrom * clip.duration;
    } else {
      this.inspectT = 0;
    }
  }

  /**
   * Record what the inspect moves on a freshly fitted prop: its rest transform,
   * where its `spin_origin` marker sits (in the prop's own space), and the rest
   * rotation of every named part a clip might swing.
   */
  private rememberProp(model: THREE.Object3D): void {
    const three = this.three;
    model.updateMatrixWorld(true);
    const inverse = model.matrixWorld.clone().invert();
    const pivot = model.getObjectByName('spin_origin');
    const nodes = new Map<string, { obj: THREE.Object3D; rest: THREE.Quaternion }>();
    model.traverse((o) => {
      if (o !== model && o.name) nodes.set(o.name, { obj: o, rest: o.quaternion.clone() });
    });
    this.prop = {
      model,
      position: model.position.clone(),
      quaternion: model.quaternion.clone(),
      pivot: pivot
        ? new three.Vector3().setFromMatrixPosition(pivot.matrixWorld.clone().premultiply(inverse))
        : new three.Vector3(),
      nodes,
    };
  }

  /**
   * Apply an inspect sample's `spin` and named parts to the prop, or put them
   * back at rest when there is no sample.
   *
   * On the prop rather than the pivot, and that is the point of `spin`: the
   * hands are solved through the pivot and the weapon group, so turning the
   * prop underneath them spins a karambit on the finger in its ring while the
   * hand stays where it is. The box fallback has no marker and simply does not
   * spin.
   */
  private applyInspectToProp(clip: InspectClip, sample: InspectSample | null): void {
    const prop = this.prop;
    if (!prop) return;
    const three = this.three;
    const spin = sample?.spin ?? 0;
    if (Math.abs(spin) > 1e-6) {
      const axis = new three.Vector3(...(clip.spinAxis ?? [1, 0, 0])).normalize();
      const turn = new three.Quaternion().setFromAxisAngle(axis, spin);
      // About the marker: p' = offset + o + R(p - o) — so the prop's own
      // rotation becomes R and its position moves by o - R o.
      const o = clip.spinPivot ? prop.pivot : new three.Vector3();
      const shift = o.clone().sub(o.clone().applyQuaternion(turn));
      prop.model.quaternion.copy(prop.quaternion).multiply(turn);
      prop.model.position.copy(prop.position).add(shift.applyQuaternion(prop.quaternion));
    } else {
      prop.model.quaternion.copy(prop.quaternion);
      prop.model.position.copy(prop.position);
    }
    for (const [name, node] of prop.nodes) {
      const rot = sample?.nodes[name];
      if (rot) {
        node.obj.quaternion
          .copy(node.rest)
          .multiply(new three.Quaternion().setFromEuler(new three.Euler(rot[0], rot[1], rot[2])));
      } else {
        node.obj.quaternion.copy(node.rest);
      }
    }
  }

  /** Which choreography the weapon in the hands runs. */
  private inspectClipId(): string {
    return inspectClipFor(this.weaponId, this.currentSkin?.id ?? '', this.currentSkin?.name ?? '');
  }

  /** Whether the animation is running, so the HUD can name what the weapon is
   * doing instead of the player wondering why it moved. */
  get inspecting(): boolean {
    return this.inspectT !== null;
  }

  /**
   * Advance the animation.
   *
   * Everything here is a *local* effect — nothing the server knows or cares
   * about. The same concession the recoil in `combat.ts` makes: angles are
   * client-owned, so how the gun waves about is nobody else's business.
   */
  update(dt: number, frame: ViewModelFrame): void {
    this.pivot.visible = frame.visible && this.built !== null;
    if (!this.pivot.visible) {
      // Reset the walk cycle rather than freezing it: coming back from death mid
      // stride would otherwise resume with the gun wherever it happened to be.
      this.bobPhase = 0;
      this.lastYaw = null;
      this.walk = 0;
      // Dying mid-inspect must not resume it on respawn: the animation is
      // something you asked for, not a state of the weapon.
      this.inspectT = null;
      // Nor may a half-finished holster: the request that started it belongs to
      // a life that has ended.
      this.holsterHold = 0;
      this.drawOnReturn = true;
      // The hands go with the weapon. Left drawn, they hang in the middle of a
      // dead player's spectator view holding nothing.
      this.arms?.update(this.grips, (p) => p, false);
      this.action = null;
      return;
    }

    if (this.drawOnReturn) {
      // First frame back. Start fully stowed so the weapon comes up into frame
      // rather than materialising at rest — a spawn is an arrival.
      this.drawOnReturn = false;
      this.stow = 1;
      // A spawn is an arrival, and the hands arrive with the gun.
      this.startAction('draw', DRAW_DURATION);
    }

    const target = Math.min(1, Math.max(0, frame.speed / MOVE_SPEED));
    this.walk += (target - this.walk) * Math.min(1, dt * 8);
    const walk = this.walk;
    this.bobPhase += dt * (4.5 + walk * 7.5);
    // Airborne, the weapon settles: bobbing in mid-air reads as a bug.
    const bobAmount = frame.onGround ? walk : walk * 0.15;

    // Turning drags the weapon behind the view for a fraction of a second, which
    // is the difference between a held object and a decal on the screen.
    let yawDelta = this.lastYaw === null ? 0 : frame.yaw - this.lastYaw;
    while (yawDelta > Math.PI) yawDelta -= Math.PI * 2;
    while (yawDelta < -Math.PI) yawDelta += Math.PI * 2;
    const pitchDelta = frame.pitch - this.lastPitch;
    this.lastYaw = frame.yaw;
    this.lastPitch = frame.pitch;
    const settle = Math.min(1, dt * 9);
    this.swayX += (clamp(-yawDelta * 2.2, -0.22, 0.22) - this.swayX) * settle;
    this.swayY += (clamp(-pitchDelta * 1.6, -0.18, 0.18) - this.swayY) * settle;
    this.swayRoll += (clamp(-yawDelta * 1.8, -0.15, 0.15) - this.swayRoll) * Math.min(1, dt * 10);

    // Lateral strafe inertia: strafing left swings weapon slightly right and rolls outward
    const strafeTarget = (frame.strafe ?? 0) * -0.06;
    this.strafeSway += (strafeTarget - this.strafeSway) * Math.min(1, dt * 8);

    // Landing shockwave dip (damped spring compression)
    const landPhase =
      frame.sinceLanded !== undefined && frame.sinceLanded < 0.28 ? frame.sinceLanded / 0.28 : 1;
    const landDip = landPhase < 1 ? -0.075 * Math.sin(landPhase * Math.PI) * (1.0 - landPhase) : 0;

    this.kick -= this.kick * Math.min(1, dt * KICK_DECAY);
    // The dip, on the server's clock where there is one. `reloadProgress` is
    // `null` only when there is no length to stretch across, and the fixed-rate
    // approach is what the whole client used to do — kept as the fallback so a
    // server that serves no `reloadTime` still dips rather than doing nothing.
    if (frame.reloading && frame.reloadProgress !== null && frame.reloadProgress !== undefined) {
      this.reloadT = reloadEnvelope(frame.reloadProgress);
      // The hands run on the reload's own progress rather than on a clock of
      // their own, so the mag goes in exactly when the magazine fills — one
      // authored motion stretched to whatever `reloadTime` was served.
      this.action = {
        clip: 'reload',
        t: frame.reloadProgress,
        duration: 1,
      };
      if (this.weaponMixer && this.weaponReloadAction) {
        if (!this.weaponReloadAction.isRunning()) {
          this.weaponReloadAction.play();
        }
        const clipDuration = this.weaponReloadAction.getClip().duration;
        this.weaponMixer.setTime(frame.reloadProgress * clipDuration);
      }
    } else {
      const reloadTarget = frame.reloading ? 1 : 0;
      this.reloadT += (reloadTarget - this.reloadT) * Math.min(1, dt * RELOAD_RATE);
      if (this.weaponMixer && this.weaponReloadAction && this.weaponReloadAction.isRunning()) {
        this.weaponReloadAction.stop();
        this.weaponMixer.setTime(0);
      }
    }

    // The stow: down while a switch is pending or one was asked for, back up
    // otherwise. Linear, because a swap is an action with a length — see
    // `approach`.
    if (this.holsterHold > 0) this.holsterHold = Math.max(0, this.holsterHold - dt);
    const stowTarget = this.holsterHold > 0 ? 1 : 0;
    this.stow = approach(
      this.stow,
      stowTarget,
      stowTarget > this.stow ? HOLSTER_TIME : DRAW_TIME,
      dt,
    );
    const stow = ease(this.stow);

    // A reload takes the weapon away for an animation of its own, and two poses
    // fighting over one pivot is a weapon that looks broken. The reload wins,
    // because it is the one the *server* is actually doing.
    if (frame.reloading) this.inspectT = null;
    // Advanced before it is read, so the frame it completes on is the frame the
    // weapon is back at rest rather than one after.
    const inspectClip = clipFor(this.inspectClipId());
    if (this.inspectT !== null) {
      const t = this.inspectT + dt;
      this.inspectT = t >= inspectClip.duration ? null : t;
    }

    // ADS smooth transition: target is frame.ads (defaulting to 0)
    const targetAds = frame.ads ?? 0;
    this.adsT += (targetAds - this.adsT) * Math.min(1, dt * 14.0);
    const adsDamp = 1.0 - 0.75 * this.adsT;
    if (this.adsT > 0.15) this.inspectT = null;

    const curHomeX = HOME.x * (1 - this.adsT) + ADS_POS.x * this.adsT;
    const curHomeY = HOME.y * (1 - this.adsT) + ADS_POS.y * this.adsT;
    const curHomeZ = HOME.z * (1 - this.adsT) + ADS_POS.z * this.adsT;

    const bobX = Math.cos(this.bobPhase * 0.5) * 0.05 * bobAmount * adsDamp;
    const bobY = Math.abs(Math.sin(this.bobPhase)) * -0.055 * bobAmount * adsDamp;

    // Empty reload bolt rack: between progress 0.68 and 0.88, rack the bolt/charging handle
    let boltPullZ = 0;
    let boltPullPitch = 0;
    let boltPullRoll = 0;
    if (
      frame.reloadingEmpty &&
      frame.reloadProgress !== null &&
      frame.reloadProgress !== undefined
    ) {
      const p = frame.reloadProgress;
      if (p >= 0.68 && p <= 0.88) {
        const boltPhase = Math.sin(((p - 0.68) / 0.2) * Math.PI);
        boltPullZ = -0.14 * boltPhase;
        boltPullPitch = 0.22 * boltPhase;
        boltPullRoll = 0.12 * boltPhase;
      }
    }

    // Where the inspect takes the weapon: a choreography per weapon and knife,
    // authored in `tools/blender/author_inspects.py` and sampled by
    // `inspects.ts`. `pos` and `rot` move the whole pivot — hands and all — and
    // are faded to rest at both ends; `spin`, the finger poses and the named
    // parts are applied below, to the prop and the hands.
    const isPistol = this.weaponId === 'pistol';
    const isShotgun = this.weaponId === 'shotgun';
    const isSniper = this.weaponId === 'sniper';

    const sample =
      this.inspectT !== null
        ? sampleInspect(this.inspectClipId(), this.inspectT / inspectClip.duration)
        : null;
    const inspectLiftX = sample?.pos[0] ?? 0;
    const inspectLiftY = sample?.pos[1] ?? 0;
    const inspectLiftZ = sample?.pos[2] ?? 0;
    const inspectPitch = sample?.rot[0] ?? 0;
    const inspectYaw = sample?.rot[1] ?? 0;
    const inspectRoll = sample?.rot[2] ?? 0;
    this.applyInspectToProp(inspectClip, sample);

    // Mechanical reload physical dynamics: mag release jolt, mag seat slam, and action rack
    let reloadImpulseY = 0;
    let reloadImpulseZ = 0;
    let reloadImpulsePitch = 0;
    let reloadImpulseRoll = 0;

    if (frame.reloadProgress !== null && frame.reloadProgress !== undefined) {
      const p = Math.max(0, Math.min(1, frame.reloadProgress));
      // 1. Mag drop jolt (p in 0.18..0.28)
      if (p >= 0.18 && p <= 0.28) {
        const s = Math.sin(((p - 0.18) / 0.1) * Math.PI);
        reloadImpulseY -= 0.045 * s;
        reloadImpulsePitch += 0.08 * s;
      }
      // 2. Mag insert upward slam impact (p in 0.58..0.68)
      if (p >= 0.58 && p <= 0.68) {
        const s = Math.sin(((p - 0.58) / 0.1) * Math.PI);
        reloadImpulseY += 0.065 * s;
        reloadImpulseZ -= 0.04 * s;
        reloadImpulsePitch -= 0.12 * s;
      }
      // 3. Action rack / slide release / shotgun pump / bolt cycle (p in 0.74..0.88)
      if (p >= 0.74 && p <= 0.88) {
        const rackT = (p - 0.74) / 0.14;
        if (isPistol) {
          const s = Math.sin(rackT * Math.PI);
          reloadImpulseZ += 0.06 * s;
          reloadImpulsePitch -= 0.1 * s;
        } else if (isShotgun) {
          const s = Math.sin(rackT * Math.PI * 2.0);
          reloadImpulseZ -= 0.08 * s;
          reloadImpulsePitch += 0.12 * s;
        } else if (isSniper) {
          const s = Math.sin(rackT * Math.PI);
          reloadImpulseZ -= 0.12 * s;
          reloadImpulsePitch += 0.16 * s;
          reloadImpulseRoll -= 0.1 * s;
        } else {
          const s = Math.sin(rackT * Math.PI);
          reloadImpulseZ -= 0.09 * s;
          reloadImpulsePitch += 0.14 * s;
          reloadImpulseRoll += 0.08 * s;
        }
      }
    }

    let knifeX = 0;
    let knifeY = 0;
    let knifeZ = 0;
    let knifePitch = 0;
    let knifeYaw = 0;
    let knifeRoll = 0;

    if (this.knifeAction !== null) {
      this.knifeActionT += dt;
      const duration = this.knifeAction === 'stab' ? 0.35 : 0.22;
      const progress = Math.min(1.0, this.knifeActionT / duration);
      const arc = Math.sin(progress * Math.PI);
      if (this.knifeAction === 'slash') {
        // Quick slash: fast diagonal swipe across the screen
        knifeX = -0.32 * arc;
        knifeY = 0.08 * arc;
        knifeZ = -0.12 * arc;
        knifePitch = 0.25 * arc;
        knifeYaw = -0.55 * arc;
        knifeRoll = -0.45 * arc;
      } else {
        // Heavy stab: powerful forward thrust along -Z
        knifeX = -0.12 * arc;
        knifeY = 0.06 * arc;
        knifeZ = -0.4 * arc;
        knifePitch = -0.12 * arc;
        knifeYaw = 0.15 * arc;
        knifeRoll = 0.55 * arc;
      }
      if (this.knifeActionT >= duration) {
        this.knifeAction = null;
      }
    }

    // Idle respiratory breathing sway
    this.breathPhase += dt * 2.2;
    const breathWeight = (1.0 - walk * 0.7) * (1.0 - this.sprintT) * adsDamp;
    const breathX = Math.sin(this.breathPhase * 0.5) * 0.0004 * breathWeight;
    const breathY = Math.cos(this.breathPhase) * 0.0003 * breathWeight;
    const breathPitch = Math.cos(this.breathPhase) * 0.006 * breathWeight;

    // Sprint weapon tuck transition
    const isSprinting = Boolean(
      frame.sprint || (frame.speed > MOVE_SPEED * 1.15 && frame.onGround),
    );
    const targetSprint = isSprinting ? 1.0 : 0.0;
    this.sprintT += (targetSprint - this.sprintT) * Math.min(1.0, dt * 8.0);
    const sprintDip = this.sprintT * -0.06;
    const sprintPitch = this.sprintT * -0.18;
    const sprintYaw = this.sprintT * -0.22;
    const sprintRoll = this.sprintT * 0.15;

    this.pivot.position.set(
      curHomeX +
        (bobX + (this.swayX + this.strafeSway) * adsDamp) -
        inspectLiftX +
        knifeX +
        breathX,
      // The stow drops the weapon out of frame entirely. Applied to the same
      // axis as the reload dip and *added* rather than blended, so a switch
      // asked for mid-reload takes the gun the rest of the way down instead of
      // fighting the dip for the pivot.
      curHomeY +
        (bobY + this.swayY * adsDamp + landDip) -
        this.reloadT * 0.55 +
        reloadImpulseY +
        inspectLiftY -
        stow * 1.15 +
        knifeY +
        breathY +
        sprintDip,
      // Recoil is mostly backwards: a gun that only rotates looks hinged.
      curHomeZ + this.kick * 0.28 + boltPullZ + reloadImpulseZ + inspectLiftZ + knifeZ,
    );
    this.pivot.rotation.set(
      this.kick * -0.16 +
        this.reloadT * 0.7 +
        boltPullPitch +
        reloadImpulsePitch +
        bobY * 0.4 +
        inspectPitch +
        stow * 0.9 +
        knifePitch +
        breathPitch +
        sprintPitch,
      (this.swayX * 0.7 + this.strafeSway * 0.5) * adsDamp +
        this.reloadT * 0.25 +
        inspectYaw +
        knifeYaw +
        sprintYaw,
      (this.swayX * 0.5 + this.swayRoll) * adsDamp +
        bobX * 0.6 +
        boltPullRoll +
        reloadImpulseRoll +
        inspectRoll +
        stow * 0.35 +
        knifeRoll +
        sprintRoll,
    );

    this.flashAge += dt;
    this.smokeAge += dt;
    this.updateFlash();
    this.updateArms(dt, frame, walk, stow, sample);
  }

  /**
   * Pose the hands and solve them onto the weapon.
   *
   * Two layers merged per channel and then run through one transform: the grip
   * anchor plus the pose offset, pushed through the pivot chain into camera
   * space. Because the anchors are points on the *weapon*, everything the pivot
   * is already doing — bob, sway, recoil, the reload dip, the stow, the inspect
   * roll — reaches the hands with nothing here knowing it happened.
   */
  private updateArms(
    dt: number,
    frame: ViewModelFrame,
    walk: number,
    stow: number,
    inspect: InspectSample | null,
  ): void {
    const arms = this.arms;
    const built = this.built;
    if (!arms || !built) return;

    // The action layer, advanced. A reload's `t` is driven by the server's own
    // progress above rather than by `dt`, so only the self-timed ones tick here.
    if (this.action && this.action.duration !== 1) {
      this.action.t += dt / this.action.duration;
      if (this.action.t >= 1) this.action = null;
    } else if (this.action && !frame.reloading && this.action.clip === 'reload') {
      // The reload ended without a final progress of 1 — a switch, a death, a
      // correction. Drop it rather than leaving the hand in the magazine well.
      this.action = null;
    }

    const clip = selectLocomotion({
      speed: frame.speed,
      moveSpeed: MOVE_SPEED,
      onGround: frame.onGround,
      sinceLanded: frame.sinceLanded ?? Infinity,
    });
    // Phase-driven off `bobPhase`, so the stride and the weapon bob cannot drift
    // apart. Scaled by `walk` on the two moving clips only: at a standstill the
    // idle track is the whole of it.
    const base = this.poses.locomotion(clip, this.bobPhase / (Math.PI * 2));
    const scaled: PartialPose = clip === 'walk' || clip === 'run' ? scalePose(base, walk) : base;
    const pose = this.action
      ? mergePose(scaled, this.poses.action(this.action.clip, Math.min(1, this.action.t)))
      : scaled;

    // The inspect's hand channels ride on top: offsets add to whatever the
    // layers above said, and its finger curls blend over the grip's own.
    const grips = this.grips;
    const primaryCurl = blendCurl(
      curlOver(grips.primaryCurl, pose.primaryFingers, pose.primaryFingersWeight),
      inspect?.primaryFingers ?? null,
    );
    const supportCurl = blendCurl(
      curlOver(grips.supportCurl, pose.supportFingers, pose.supportFingersWeight),
      inspect?.supportFingers ?? null,
    );
    const primaryOffset = addVec(pose.primary, inspect?.primary);
    const supportOffset = addVec(pose.support, inspect?.support);
    const primaryRoll = pose.primaryRoll ?? 0;
    const supportRoll = pose.supportRoll ?? 0;
    const anchors: GripAnchors = {
      ...grips,
      primary: offsetBy(grips.primary, primaryOffset),
      support: grips.support === null ? null : offsetBy(grips.support, supportOffset),
      primaryRoll: grips.primaryRoll + primaryRoll,
      supportRoll: grips.supportRoll + supportRoll,
      // A pose's roll turns the hand about its own aim — the same channel the
      // procedural arms read as a roll about the forearm.
      primaryUp: rollAbout(grips.primaryUp, grips.primaryAim, primaryRoll),
      supportUp: rollAbout(grips.supportUp, grips.supportAim, supportRoll),
      primaryCurl,
      supportCurl,
    };

    // The weapon's model space into camera space, through the same two matrices
    // the flash's distance uses. Recomputed here rather than cached, because the
    // pivot moves every frame and that motion is the entire point.
    built.group.updateMatrix();
    this.pivot.updateMatrix();
    // The anchors are in the prop's own space (`models/grips.json`), so they
    // go through the prop's fitted offset first — its rest position, not the
    // spun one, so a twirl turns the knife and not the hand.
    const propOffset = this.prop?.position;
    const toCamera = (p: Vec3): Vec3 => {
      const v = new this.three.Vector3(p[0], p[1], p[2]);
      if (propOffset) v.add(propOffset);
      v.applyMatrix4(built.group.matrix).applyMatrix4(this.pivot.matrix);
      return [v.x, v.y, v.z];
    };
    const dirToCamera = (d: Vec3): Vec3 => {
      const v = new this.three.Vector3(d[0], d[1], d[2]);
      v.transformDirection(built.group.matrix).transformDirection(this.pivot.matrix);
      return [v.x, v.y, v.z];
    };
    // Hidden while fully stowed: at `stow` 1 the weapon is out of frame, and two
    // arms reaching for it are two arms pointing at nothing.
    arms.update(anchors, toCamera, stow < 0.92, dirToCamera);
  }

  /**
   * Size and fade the three billboards.
   *
   * Split out of `update` because it is the whole of the change described in
   * `flash.ts` and reads as one thing: how big, how far from the eye, and
   * whether the cap bites.
   */
  private updateFlash(): void {
    const core = this.flashCore;
    const halo = this.flashHalo;
    const smoke = this.flashSmoke;
    if (!core || !halo || !smoke) return;

    const base = flashScale(this.weaponId, this.flashAge, this.flashSeed);
    // `base` is zero for a weapon with no muzzle — the knife, whose whole value
    // is that carrying it gives nothing away. Checked here rather than at the
    // build so a weapon swap needs no teardown.
    const lit = this.flashAge < FLASH_LIFE && base > 0;
    core.visible = lit;
    halo.visible = lit;
    if (lit) {
      // The muzzle's distance from the eye, which is what turns a size in cube
      // units into a fraction of the screen. Measured rather than assumed: the
      // pivot moves with every bob, sway and recoil kick, so a constant here
      // would be wrong exactly when the gun is furthest out.
      const distance = this.muzzleDistance();
      const fov = this.currentFov();
      const coreScale = clampFlashScale(base, distance, fov);
      const halo2 = clampFlashScale(haloScale(this.weaponId, base), distance, fov);
      core.scale.set(coreScale, coreScale, 1);
      halo.scale.set(halo2, halo2, 1);
    }

    const puff = smokePuff(this.weaponId, this.smokeAge);
    const smoking = this.smokeAge < FLASH_SMOKE_LIFE && puff.opacity > 0.002 && puff.scale > 0;
    smoke.visible = smoking;
    if (smoking) {
      const scale = clampFlashScale(puff.scale, this.muzzleDistance(), this.currentFov());
      smoke.scale.set(scale, scale, 1);
      (smoke.material as THREE.SpriteMaterial).opacity = puff.opacity;
      // Drifts up and a little further down the barrel, from where the shot was
      // taken rather than from wherever the muzzle has since moved to.
      const rise = (this.smokeAge / FLASH_SMOKE_LIFE) * FLASH_SMOKE_RISE;
      smoke.position.set(
        this.smokeOrigin[0],
        this.smokeOrigin[1] + rise,
        this.smokeOrigin[2] - 0.12 - rise * 0.4,
      );
    }
  }

  /**
   * How far the muzzle is from the eye, in cube units.
   *
   * The camera is the pivot's parent, so a point in the model's space becomes a
   * point in camera space — whose length is the distance, since the eye is the
   * camera's origin. No matrix inverse and no world-space round trip.
   */
  private muzzleDistance(): number {
    const built = this.built;
    if (!built) return 1;
    const p = new this.three.Vector3(built.muzzle[0], built.muzzle[1], built.muzzle[2]);
    built.group.updateMatrix();
    this.pivot.updateMatrix();
    p.applyMatrix4(built.group.matrix).applyMatrix4(this.pivot.matrix);
    return Math.max(0.05, p.length());
  }

  /**
   * The camera's vertical FOV in radians, **as it is right now**.
   *
   * Read live rather than captured, because scoping divides it: a cap computed
   * against the base FOV would be right hipfiring and four times too generous at
   * 4x, which is exactly the view a flash must not fill. A camera with no `fov`
   * (orthographic, or a test double) falls back to the perspective default
   * rather than disabling the cap.
   */
  private currentFov(): number {
    const fov = (this.camera as THREE.PerspectiveCamera).fov;
    return (typeof fov === 'number' && fov > 0 ? fov : 50) * (Math.PI / 180);
  }

  /**
   * Build the three sprites onto a freshly-built model, returning their
   * materials so `release` frees them with the rest of it.
   *
   * The *textures* are not among them: they are built once per view model and
   * shared by every weapon it holds, so they belong to `dispose`.
   */
  private buildFlash(group: THREE.Group, muzzle: [number, number, number]): THREE.Material[] {
    const three = this.three;
    if (!this.flashTexture) this.flashTexture = createFlashTexture(three, drawFlashTile());
    if (!this.smokeTexture) this.smokeTexture = createFlashTexture(three, drawSmokeTile());

    const sprite = (
      map: THREE.Texture,
      color: number,
      opacity: number,
      additive: boolean,
    ): { sprite: THREE.Sprite; material: THREE.SpriteMaterial } => {
      const material = new three.SpriteMaterial({
        map,
        color,
        transparent: true,
        opacity,
        // Never writes depth and never tests it: the flash belongs to the gun in
        // your hands, and a barrel poking a millimetre into a wall must not
        // clip it away on the one frame it matters.
        depthWrite: false,
        depthTest: false,
        blending: additive ? three.AdditiveBlending : three.NormalBlending,
      });
      const s = new three.Sprite(material);
      s.visible = false;
      // **Zero, not one.** These sprites live on the same group `fitWeaponModel`
      // measures when a prop lands, and `Box3.setFromObject` does not skip
      // invisible children — so a default 1x1 quad at the muzzle would enlarge
      // the target box and translate the arriving model to fit a bounding volume
      // that is mostly muzzle flash. At zero the sprite contributes only its own
      // position, which is inside the box already. Every visible frame sets a
      // real scale before drawing.
      s.scale.set(0, 0, 1);
      group.add(s);
      return { sprite: s, material };
    };

    const halo = sprite(this.flashTexture, FLASH_HALO, 0.55, true);
    const core = sprite(this.flashTexture, FLASH_CORE, 0.95, true);
    const smoke = sprite(this.smokeTexture, FLASH_SMOKE, 0.22, false);
    this.flashHalo = halo.sprite;
    this.flashCore = core.sprite;
    this.flashSmoke = smoke.sprite;
    this.placeFlash(muzzle);
    return [halo.material, core.material, smoke.material];
  }

  /** Move the three sprites to a muzzle — a new model, or a prop that just landed. */
  private placeFlash(muzzle: [number, number, number]): void {
    // Just clear of the barrel: at the muzzle exactly, half the sprite is inside
    // the gun.
    const at: [number, number, number] = [muzzle[0], muzzle[1], muzzle[2] - 0.06];
    this.flashHalo?.position.set(at[0], at[1], at[2]);
    this.flashCore?.position.set(at[0], at[1], at[2]);
    this.flashSmoke?.position.set(at[0], at[1], at[2]);
    this.smokeOrigin = at;
  }

  dispose(): void {
    this.release();
    this.camera.remove(this.pivot);
    for (const mat of [this.metal, this.dark, this.grip, this.accent]) mat.dispose();
    this.grain?.dispose();
    this.grain = null;
    // Owned here rather than by `release`: one pair per view model, shared by
    // every weapon it ever holds, so a loadout cycle must not free them.
    this.flashTexture?.dispose();
    this.flashTexture = null;
    this.smokeTexture?.dispose();
    this.smokeTexture = null;
    this.arms?.dispose();
    this.arms = null;
  }

  /**
   * Rebuild the four materials from a skin, or from the default palette.
   *
   * Ownership is unchanged from before skins existed: this object owns the
   * palette and `release` owns the geometry, so a weapon swap frees only that
   * model. The palette is simply rebuilt when the *skin* changes rather than
   * created once — and the old one is disposed here, since a player cycling
   * through an inventory would otherwise leak four materials per preview.
   */
  private setPalette(skin: WeaponSkin | null): void {
    for (const mat of [this.metal, this.dark, this.grip, this.accent]) mat?.dispose();
    const palette = paletteFor(skin);
    // One tile, shared by the four materials and built once per view model. Box
    // and cylinder geometries carry their own UVs, so the grain lands per *face*
    // rather than per cube — which is the right scale here: a receiver is a
    // third of a cube long, and world-scale grain would put a quarter of one
    // noise cell across the whole gun.
    if (!this.grain) {
      this.grain = createDetailTexture(this.three);
      this.grain.repeat.set(2, 2);
    }
    const grain = this.grain;

    // **Phong, not Lambert, and this is most of what makes the models read as
    // objects.** Lambert has no specular term at all, so a steel barrel and a
    // polymer grip painted the same colour are the same surface — every part of
    // every gun was equally matte, and the only thing separating them was hue.
    // A highlight that travels along a barrel as you turn is what says "this is
    // metal and it is round", and it costs one extra term per fragment.
    const wear = skin ? Math.max(0, Math.min(1, skin.floatValue)) : 0.25;
    const isSpecial =
      skin !== null &&
      (skin.patternType === 'fade' ||
        skin.patternType === 'anodized' ||
        skin.patternType === 'patina');
    // A worn gun is a dull gun: the float already dulls the colour, and letting
    // it dull the shine too is the difference between a Factory New that looks
    // new and one that is merely brighter. Rare/special finishes keep high polish.
    const polish = isSpecial
      ? Math.max(0.25, 1.35 - wear * 1.1)
      : Math.max(0.12, 1.0 - wear * 0.85);

    const make = (color: number, specular: number, shininess: number) =>
      new this.three.MeshPhongMaterial({
        color,
        specular: new this.three.Color(specular).multiplyScalar(polish),
        shininess: shininess * polish,
        map: grain,
      });

    // Machined metal: the brightest highlight and the tightest.
    const metalSpec = isSpecial ? 0x9ca3af : 0x6b7280;
    const metalShine = isSpecial ? 65 : 34;
    this.metal = make(palette.body, metalSpec, metalShine);
    // Anodised or blued: darker, still metal, softer highlight.
    const darkSpec = isSpecial ? 0x52525b : 0x3f4650;
    const darkShine = isSpecial ? 32 : 18;
    this.dark = make(palette.dark, darkSpec, darkShine);
    // Polymer and rubber: almost none, and broad. A grip that glints reads as
    // wet plastic, which is the one thing furniture must not look like.
    this.grip = make(palette.grip, 0x1d2026, 6);
    // Hardware and trim: the shiniest thing on the gun, which is what makes
    // sights and bolts catch the eye at all.
    const accentSpec = isSpecial ? 0xc7d2fe : 0x8a94a3;
    const accentShine = isSpecial ? 85 : 52;
    this.accent = make(palette.accent, accentSpec, accentShine);
  }

  /** Drop the current model and its resources. Swapping weapons calls this, so a
   * player cycling their loadout does not leak a rifle every time. */
  private release(): void {
    const built = this.built;
    this.built = null;
    this.prop = null;
    this.flashCore = null;
    this.flashHalo = null;
    this.flashSmoke = null;
    if (this.weaponMixer) {
      this.weaponMixer.stopAllAction();
      this.weaponMixer = null;
      this.weaponReloadAction = null;
    }
    // Any prop still in flight now belongs to nothing. Cleared rather than
    // cancelled because a fetch cannot be un-sent — the arrival checks this.
    this.propToken = '';
    if (!built) return;
    this.pivot.remove(built.group);
    for (const geo of built.geometries) geo.dispose();
    for (const mat of built.materials) mat.dispose();
    // A prop's geometry belongs to the clone, not to `building`, so it is not in
    // `geometries`. Walked here instead: a clone shares its prototype's buffers
    // in three, so this disposes the *instance's* meshes only when they are its
    // own — which `BufferGeometry.dispose` is safe to call for either way, since
    // the prototype is never rendered.
    built.group.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (mesh.isMesh && mesh.geometry) mesh.geometry.dispose();
    });
  }

  // ---- the models -----------------------------------------------------------

  /** A box, in cube units, at a position in the model's own space. */
  private box(
    size: [number, number, number],
    at: [number, number, number],
    material: THREE.Material,
    rotation: [number, number, number] = [0, 0, 0],
  ): THREE.Mesh {
    const geo = new this.three.BoxGeometry(size[0], size[1], size[2]);
    this.building.push(geo);
    const mesh = new this.three.Mesh(geo, material);
    mesh.position.set(at[0], at[1], at[2]);
    mesh.rotation.set(rotation[0], rotation[1], rotation[2]);
    return mesh;
  }

  /**
   * A cylinder lying along -Z, tapering from `radius` to `far` at the muzzle end.
   *
   * The taper is what a barrel actually does, and it is the cheapest thing that
   * stops a gun reading as a bundle of pipes: a straight tube has no direction,
   * while one that narrows tells you which end the round leaves from before you
   * find the sights.
   */
  protected cone(
    radius: number,
    far: number,
    length: number,
    at: [number, number, number],
    material: THREE.Material,
  ): THREE.Mesh {
    // `CylinderGeometry(top, bottom, ...)` and the cylinder is then rotated so
    // its +Y runs to -Z, which puts `top` at the muzzle. Getting these the wrong
    // way round yields a barrel that flares at the breech, which looks like a
    // modelling mistake rather than a taper.
    const geo = new this.three.CylinderGeometry(far, radius, length, 14);
    this.building.push(geo);
    const mesh = new this.three.Mesh(geo, material);
    mesh.rotation.x = Math.PI / 2;
    mesh.position.set(at[0], at[1], at[2]);
    return mesh;
  }

  /** A cylinder lying along -Z, which is the direction every barrel points. */
  private tube(
    radius: number,
    length: number,
    at: [number, number, number],
    material: THREE.Material,
  ): THREE.Mesh {
    // 14 sides rather than 10: with a specular highlight on it now, a coarse
    // cylinder shows its facets as a row of hard bands down the barrel.
    const geo = new this.three.CylinderGeometry(radius, radius, length, 14);
    this.building.push(geo);
    const mesh = new this.three.Mesh(geo, material);
    // Cylinders are built along +Y; stand this one up along the barrel axis.
    mesh.rotation.x = Math.PI / 2;
    mesh.position.set(at[0], at[1], at[2]);
    return mesh;
  }

  private buildKarambit(): Shape {
    const group = new this.three.Group();
    // Ergonomic curved handle: 3 contoured angled segments
    group.add(this.box([0.12, 0.16, 0.22], [0, 0.02, 0.02], this.grip, [0.14, 0, 0]));
    group.add(this.box([0.14, 0.18, 0.24], [0, 0.06, 0.22], this.grip, [0.28, 0, 0]));
    group.add(this.box([0.13, 0.16, 0.2], [0, 0.14, 0.42], this.dark, [0.42, 0, 0]));
    // Contoured finger index notches
    group.add(this.box([0.145, 0.04, 0.04], [0, -0.06, 0.12], this.dark));
    group.add(this.box([0.145, 0.04, 0.04], [0, -0.04, 0.24], this.dark));

    // Pommel retention ring: circular loop with an open inner hole
    group.add(this.box([0.14, 0.14, 0.08], [0, 0.22, 0.54], this.metal, [0.55, 0, 0]));
    // Open ring frame
    group.add(this.box([0.1, 0.04, 0.1], [0, 0.36, 0.62], this.metal, [0.65, 0, 0]));
    group.add(this.box([0.1, 0.04, 0.1], [0, 0.16, 0.68], this.metal, [0.65, 0, 0]));
    group.add(this.box([0.04, 0.16, 0.1], [0.06, 0.26, 0.65], this.metal, [0.65, 0, 0]));
    group.add(this.box([0.04, 0.16, 0.1], [-0.06, 0.26, 0.65], this.metal, [0.65, 0, 0]));

    // Claw/Talon blade: curves forward and sweeps down into an aggressive talon
    group.add(this.box([0.06, 0.16, 0.16], [0, -0.01, -0.16], this.dark, [-0.1, 0, 0]));
    group.add(this.box([0.045, 0.18, 0.32], [0, -0.05, -0.38], this.metal, [-0.22, 0, 0]));
    group.add(this.box([0.04, 0.17, 0.3], [0, -0.14, -0.66], this.metal, [-0.44, 0, 0]));
    group.add(this.box([0.035, 0.15, 0.26], [0, -0.29, -0.9], this.metal, [-0.7, 0, 0]));
    // Razor ground inside bevel
    group.add(this.box([0.026, 0.09, 0.36], [0, -0.12, -0.52], this.accent, [-0.32, 0, 0]));
    // Sharp talon beak point
    group.add(this.box([0.028, 0.1, 0.22], [0, -0.48, -1.06], this.accent, [-0.98, 0, 0]));
    // Thumb ramp jimping notches on spine
    for (let i = 0; i < 3; i += 1) {
      group.add(this.box([0.055, 0.04, 0.04], [0, 0.08, -0.14 - i * 0.08], this.accent));
    }
    return { group, muzzle: [0, -0.48, -1.1], rest: [0.1, -0.36, 0.28] };
  }

  private buildButterfly(): Shape {
    const group = new this.three.Group();
    // Dual skeletonized handles: Safe handle (left) and Bite handle (right)
    // Left handle rails
    group.add(this.box([0.035, 0.13, 0.65], [-0.07, 0.0, 0.12], this.grip));
    group.add(this.box([0.035, 0.13, 0.65], [-0.015, 0.0, 0.12], this.grip));
    group.add(this.box([0.065, 0.11, 0.05], [-0.042, 0.0, -0.16], this.dark));
    group.add(this.box([0.065, 0.11, 0.05], [-0.042, 0.0, 0.12], this.dark));
    group.add(this.box([0.065, 0.11, 0.05], [-0.042, 0.0, 0.4], this.dark));

    // Right handle rails
    group.add(this.box([0.035, 0.13, 0.65], [0.015, 0.0, 0.12], this.grip));
    group.add(this.box([0.035, 0.13, 0.65], [0.07, 0.0, 0.12], this.grip));
    group.add(this.box([0.065, 0.11, 0.05], [0.042, 0.0, -0.16], this.dark));
    group.add(this.box([0.065, 0.11, 0.05], [0.042, 0.0, 0.12], this.dark));
    group.add(this.box([0.065, 0.11, 0.05], [0.042, 0.0, 0.4], this.dark));

    // Latch mechanism at the base of the bite handle
    group.add(this.box([0.03, 0.05, 0.11], [0.042, 0.0, 0.48], this.metal));
    group.add(this.box([0.04, 0.06, 0.04], [0.042, 0.0, 0.54], this.accent));

    // Dual pivot screws and tang horns
    group.add(this.box([0.05, 0.04, 0.04], [-0.042, 0.0, -0.22], this.accent));
    group.add(this.box([0.05, 0.04, 0.04], [0.042, 0.0, -0.22], this.accent));
    group.add(this.box([0.048, 0.15, 0.16], [0, 0, -0.28], this.metal));
    group.add(this.box([0.15, 0.06, 0.05], [0, 0, -0.26], this.dark));

    // Symmetrical spear-point blade with fuller
    group.add(this.box([0.042, 0.17, 0.88], [0, 0.01, -0.76], this.metal));
    group.add(this.box([0.028, 0.12, 0.84], [0, -0.06, -0.76], this.accent));
    group.add(this.box([0.03, 0.08, 0.6], [0, 0.07, -0.8], this.accent));
    group.add(this.box([0.048, 0.04, 0.52], [0, 0.01, -0.68], this.dark));
    group.add(this.box([0.032, 0.13, 0.26], [0, 0.0, -1.28], this.accent));

    return { group, muzzle: [0, 0.0, -1.42], rest: [0.05, -0.25, 0.18] };
  }

  private buildBayonet(): Shape {
    const group = new this.three.Group();
    // Heavy ribbed combat grip with finger grooves
    group.add(this.box([0.13, 0.17, 0.54], [0, 0, 0.12], this.grip));
    group.add(this.box([0.16, 0.2, 0.1], [0, 0, 0.41], this.metal));
    group.add(this.box([0.08, 0.08, 0.06], [0, 0, 0.48], this.dark));
    group.add(this.box([0.05, 0.05, 0.05], [0, -0.09, 0.44], this.dark));
    for (let i = 0; i < 4; i += 1) {
      group.add(this.box([0.145, 0.185, 0.04], [0, 0, -0.06 + i * 0.11], this.dark));
    }

    // Steel crossguard with barrel attachment ring
    group.add(this.box([0.22, 0.24, 0.08], [0, 0.02, -0.18], this.metal));
    group.add(this.box([0.06, 0.1, 0.07], [0, 0.14, -0.18], this.metal));
    group.add(this.tube(0.06, 0.07, [0, 0.21, -0.18], this.metal));
    group.add(this.box([0.06, 0.08, 0.06], [0, -0.12, -0.18], this.metal, [-0.2, 0, 0]));

    // Clip-point blade with sawback serrations and fuller
    group.add(this.box([0.065, 0.16, 0.16], [0, 0.02, -0.3], this.metal));
    group.add(this.box([0.052, 0.2, 1.05], [0, 0.07, -0.88], this.metal));
    group.add(this.box([0.035, 0.15, 1.02], [0, -0.06, -0.87], this.accent));
    group.add(this.box([0.058, 0.04, 0.65], [0, 0.03, -0.78], this.dark));
    group.add(this.box([0.042, 0.16, 0.32], [0, 0.0, -1.48], this.accent, [0.18, 0, 0]));
    for (let i = 0; i < 5; i += 1) {
      group.add(this.box([0.058, 0.05, 0.05], [0, 0.18, -0.48 - i * 0.1], this.dark));
    }

    return { group, muzzle: [0, 0.02, -1.62], rest: [0.06, -0.32, 0.22] };
  }

  private buildTacticalKnife(): Shape {
    const group = new this.three.Group();
    // Handle in three segments rather than one box, so it has a swell in the
    // middle and a pommel at the end
    group.add(this.box([0.13, 0.16, 0.5], [0, 0, 0.14], this.grip));
    group.add(this.box([0.15, 0.185, 0.24], [0, 0, 0.06], this.grip));
    group.add(this.box([0.16, 0.2, 0.09], [0, 0, 0.38], this.dark));
    group.add(this.tube(0.075, 0.06, [0, 0, 0.44], this.accent));
    group.add(this.box([0.17, 0.05, 0.05], [0, 0.02, 0.38], this.metal));
    group.add(this.box([0.2, 0.2, 0.07], [0, 0.01, -0.2], this.dark));
    group.add(this.box([0.06, 0.13, 0.14], [0, 0.03, -0.31], this.metal));

    const spine = this.box([0.05, 0.2, 0.95], [0, 0.08, -0.85], this.metal);
    spine.scale.set(1, 0.85, 1);
    group.add(spine);
    group.add(this.box([0.035, 0.14, 0.92], [0, -0.035, -0.84], this.accent));
    const tip = this.box([0.04, 0.17, 0.3], [0, 0.02, -1.42], this.accent);
    tip.scale.set(0.7, 0.45, 1);
    group.add(tip);
    for (let i = 0; i < 3; i += 1) {
      group.add(this.box([0.055, 0.05, 0.05], [0, 0.15, -0.5 - i * 0.13], this.dark));
    }
    return { group, muzzle: [0, 0.03, -1.5], rest: [0.06, -0.32, 0.22] };
  }

  private buildSkeletonKnife(): Shape {
    const group = new this.three.Group();
    // Drop-point blade with recurve belly and razor edge
    group.add(this.box([0.042, 0.16, 0.82], [0, 0.04, -0.74], this.metal));
    group.add(this.box([0.03, 0.12, 0.78], [0, -0.05, -0.74], this.accent));
    group.add(this.box([0.035, 0.14, 0.26], [0, 0.01, -1.22], this.accent));

    // Large center finger hole at ricasso transition for twirling
    group.add(this.tube(0.08, 0.06, [0, 0, -0.22], this.accent));
    group.add(this.box([0.14, 0.14, 0.05], [0, 0, -0.22], this.dark));

    // Skeletal handle frame
    group.add(this.box([0.045, 0.06, 0.62], [0, 0.05, 0.18], this.metal));
    group.add(this.box([0.045, 0.06, 0.62], [0, -0.05, 0.18], this.metal));
    group.add(this.box([0.05, 0.12, 0.08], [0, 0, 0.48], this.dark));

    // Woven paracord grip lashing
    for (let i = 0; i < 6; i += 1) {
      group.add(this.box([0.062, 0.145, 0.05], [0, 0, -0.06 + i * 0.09], this.grip));
    }

    return { group, muzzle: [0, 0.01, -1.35], rest: [0.06, -0.28, 0.2] };
  }

  private buildHuntsman(): Shape {
    const group = new this.three.Group();
    // Heavy textured G10 contoured grip
    group.add(this.box([0.14, 0.18, 0.58], [0, 0, 0.15], this.grip));
    group.add(this.box([0.16, 0.2, 0.12], [0, 0, 0.45], this.dark));
    group.add(this.box([0.08, 0.08, 0.06], [0, 0, 0.52], this.accent));

    // Ergonomic finger grooves
    for (let i = 0; i < 3; i += 1) {
      group.add(this.box([0.15, 0.05, 0.05], [0, -0.08, 0.02 + i * 0.14], this.dark));
    }

    // Heavy crossguard and gut choil
    group.add(this.box([0.22, 0.22, 0.08], [0, 0.02, -0.18], this.dark));
    group.add(this.tube(0.06, 0.05, [0, -0.1, -0.22], this.accent));

    // Heavy recurve tanto blade
    group.add(this.box([0.065, 0.22, 0.98], [0, 0.06, -0.8], this.metal));
    group.add(this.box([0.04, 0.16, 0.95], [0, -0.06, -0.8], this.accent));
    group.add(this.box([0.048, 0.18, 0.34], [0, 0.02, -1.38], this.accent));

    // Double row sawback spine teeth
    for (let i = 0; i < 6; i += 1) {
      group.add(this.box([0.068, 0.06, 0.06], [0, 0.18, -0.42 - i * 0.1], this.metal));
    }

    return { group, muzzle: [0, 0.02, -1.52], rest: [0.07, -0.34, 0.24] };
  }

  private buildTacticalPistol(): Shape {
    const group = new this.three.Group();
    // Slide assembly with chamfered profile
    group.add(this.box([0.21, 0.22, 1.15], [0, 0.04, -0.52], this.metal));
    group.add(this.box([0.04, 0.04, 1.15], [-0.1, 0.14, -0.52], this.metal, [0, 0, 0.78]));
    group.add(this.box([0.04, 0.04, 1.15], [0.1, 0.14, -0.52], this.metal, [0, 0, -0.78]));
    // Ejection port and extractor claw
    group.add(this.box([0.04, 0.12, 0.32], [0.1, 0.05, -0.42], this.dark));
    group.add(this.box([0.02, 0.04, 0.12], [0.115, 0.05, -0.28], this.accent));
    group.add(this.box([0.2, 0.18, 0.04], [0, 0.04, 0.06], this.dark));
    // Match-grade crowned barrel and recoil spring plug
    group.add(this.tube(0.052, 0.28, [0, 0, -1.16], this.accent));
    group.add(this.tube(0.038, 0.06, [0, 0, -1.3], this.dark));
    group.add(this.tube(0.046, 0.06, [0, -0.1, -1.12], this.dark));
    // Frame, dust cover & Picatinny rail
    group.add(this.box([0.19, 0.13, 0.95], [0, -0.12, -0.48], this.dark));
    group.add(this.box([0.02, 0.04, 0.14], [-0.11, -0.04, -0.32], this.accent));
    // Grip frame, stippled backstrap, and mag baseplate
    group.add(this.box([0.2, 0.62, 0.32], [0, -0.42, -0.02], this.grip, [0.3, 0, 0]));
    group.add(this.box([0.08, 0.52, 0.08], [0, -0.4, 0.14], this.dark, [0.3, 0, 0]));
    group.add(this.box([0.21, 0.1, 0.34], [0, -0.72, 0.08], this.dark, [0.3, 0, 0]));
    group.add(this.box([0.04, 0.06, 0.06], [-0.11, -0.28, -0.12], this.accent));
    // Beavertail and skeletonized hammer
    group.add(this.box([0.14, 0.06, 0.16], [0, -0.06, 0.12], this.dark, [-0.25, 0, 0]));
    group.add(this.box([0.06, 0.12, 0.08], [0, 0.02, 0.14], this.accent, [-0.4, 0, 0]));
    // Trigger guard loop and trigger shoe with safety blade
    group.add(this.box([0.09, 0.05, 0.34], [0, -0.28, -0.36], this.dark));
    group.add(this.box([0.09, 0.14, 0.05], [0, -0.21, -0.52], this.dark));
    group.add(this.box([0.05, 0.14, 0.05], [0, -0.2, -0.28], this.accent, [0.25, 0, 0]));
    // 3-Dot Combat Sights
    group.add(this.box([0.14, 0.06, 0.06], [0, 0.17, -0.05], this.dark));
    group.add(this.box([0.04, 0.07, 0.06], [0, 0.17, -1.02], this.dark));
    // Front and rear slide serrations
    for (let i = 0; i < 4; i += 1) {
      group.add(this.box([0.222, 0.18, 0.03], [0, 0.04, -0.06 - i * 0.08], this.dark));
    }
    for (let i = 0; i < 3; i += 1) {
      group.add(this.box([0.222, 0.18, 0.03], [0, 0.04, -0.8 - i * 0.08], this.dark));
    }
    return { group, muzzle: [0, -0.01, -1.32], rest: [0, -0.05, 0] };
  }

  private buildTacticalShotgun(): Shape {
    const group = new this.three.Group();
    // Over-and-under twin barrels
    group.add(this.tube(0.078, 2.2, [0, 0.09, -1.5], this.metal));
    group.add(this.tube(0.076, 2.05, [0, -0.05, -1.42], this.metal));
    // Ventilated barrel rib with brass bead sight
    group.add(this.box([0.04, 0.06, 2.0], [0, 0.17, -1.45], this.dark));
    group.add(this.box([0.05, 0.06, 0.06], [0, 0.2, -2.48], this.accent));
    // Breacher standoff choke with aggressive muzzle teeth
    group.add(this.tube(0.095, 0.14, [0, 0.09, -2.58], this.dark));
    group.add(this.tube(0.092, 0.12, [0, -0.05, -2.48], this.dark));
    // Magazine tube clamp and sling swivel
    group.add(this.box([0.18, 0.24, 0.08], [0, 0.02, -2.15], this.dark));
    // Ribbed ergonomic forend pump with dual action bars
    group.add(this.box([0.3, 0.24, 0.62], [0, -0.19, -1.2], this.grip));
    group.add(this.box([0.04, 0.04, 0.75], [-0.1, -0.02, -0.8], this.metal));
    group.add(this.box([0.04, 0.04, 0.75], [0.1, -0.02, -0.8], this.metal));
    // Milled tactical receiver with ejection port and shell lifter
    group.add(this.box([0.32, 0.38, 0.85], [0, -0.04, -0.3], this.dark));
    group.add(this.box([0.34, 0.42, 0.12], [0, -0.04, 0.14], this.metal));
    group.add(this.box([0.04, 0.16, 0.36], [0.16, 0.02, -0.28], this.dark));
    group.add(this.box([0.18, 0.04, 0.42], [0, -0.21, -0.32], this.accent));
    group.add(this.box([0.09, 0.05, 0.3], [0, -0.26, -0.16], this.dark));
    group.add(this.box([0.05, 0.11, 0.05], [0, -0.2, -0.2], this.accent, [0.2, 0, 0]));
    // Stock: contoured wrist, comb, and ventilated recoil pad
    group.add(this.box([0.22, 0.3, 0.5], [0, -0.14, 0.42], this.grip, [-0.12, 0, 0]));
    group.add(this.box([0.2, 0.34, 0.6], [0, -0.24, 0.9], this.grip, [-0.08, 0, 0]));
    group.add(this.box([0.21, 0.36, 0.1], [0, -0.28, 1.22], this.dark, [-0.08, 0, 0]));
    // Pump grip ridges
    for (let i = 0; i < 5; i += 1) {
      group.add(this.box([0.315, 0.055, 0.05], [0, -0.19, -1.42 + i * 0.11], this.dark));
    }
    return { group, muzzle: [0, 0.02, -2.62], rest: [0, -0.04, 0] };
  }

  private buildTacticalSniper(): Shape {
    const group = new this.three.Group();
    // Heavy match-grade fluted barrel
    group.add(this.tube(0.078, 1.1, [0, 0.02, -1.05], this.metal));
    group.add(this.tube(0.052, 1.6, [0, 0.02, -2.35], this.metal));
    // Dual-port tactical muzzle brake
    group.add(this.tube(0.085, 0.28, [0, 0.02, -3.24], this.dark));
    group.add(this.box([0.2, 0.05, 0.05], [0, 0.08, -3.2], this.accent));
    group.add(this.box([0.2, 0.05, 0.05], [0, 0.08, -3.3], this.accent));
    // Receiver & chassis forend
    group.add(this.box([0.26, 0.34, 1.2], [0, -0.04, -0.5], this.dark));
    group.add(this.box([0.22, 0.24, 1.05], [0, -0.06, -1.6], this.grip));
    // 34mm Tactical Optic Scope
    group.add(this.tube(0.11, 0.95, [0, 0.34, -0.85], this.dark));
    group.add(this.tube(0.145, 0.24, [0, 0.34, -1.36], this.dark));
    group.add(this.tube(0.135, 0.08, [0, 0.34, -1.5], this.accent));
    group.add(this.tube(0.125, 0.2, [0, 0.34, -0.33], this.dark));
    group.add(this.tube(0.12, 0.06, [0, 0.34, -0.2], this.dark));
    // Scope target turrets
    group.add(this.box([0.09, 0.12, 0.16], [0, 0.47, -0.9], this.accent));
    group.add(this.box([0.16, 0.09, 0.14], [0.12, 0.34, -0.9], this.accent));
    group.add(this.box([0.1, 0.2, 0.1], [0, 0.19, -0.55], this.metal));
    group.add(this.box([0.1, 0.2, 0.1], [0, 0.19, -1.18], this.metal));
    // Fluted bolt and tactical knob
    group.add(this.tube(0.045, 0.4, [0.12, 0.06, -0.12], this.metal));
    group.add(this.box([0.3, 0.06, 0.06], [0.24, 0.04, -0.04], this.metal));
    group.add(this.box([0.08, 0.08, 0.08], [0.38, 0.0, -0.04], this.accent));
    // Detachable box magazine and paddle release
    group.add(this.box([0.19, 0.44, 0.34], [0, -0.36, -0.5], this.dark));
    group.add(this.box([0.21, 0.06, 0.36], [0, -0.57, -0.5], this.metal));
    group.add(this.box([0.06, 0.08, 0.04], [0, -0.22, -0.34], this.accent));
    // Ergonomic sniper pistol grip
    group.add(this.box([0.18, 0.46, 0.26], [0, -0.32, -0.06], this.grip, [0.26, 0, 0]));
    // Skeletonized marksman stock with cheek riser
    group.add(this.box([0.2, 0.09, 0.95], [0, 0.06, 0.55], this.dark));
    group.add(this.box([0.2, 0.09, 0.8], [0, -0.28, 0.5], this.dark));
    group.add(this.box([0.22, 0.16, 0.4], [0, 0.19, 0.55], this.grip));
    group.add(this.box([0.24, 0.44, 0.1], [0, -0.06, 1.0], this.dark));
    // Barrel fluting grooves
    for (let i = 0; i < 4; i += 1) {
      const angle = (i / 4) * Math.PI * 2;
      group.add(
        this.box(
          [0.02, 0.02, 0.85],
          [Math.cos(angle) * 0.07, 0.02 + Math.sin(angle) * 0.07, -1.05],
          this.dark,
        ),
      );
    }
    // Forend M-LOK slots
    for (let i = 0; i < 3; i += 1) {
      group.add(this.box([0.235, 0.07, 0.12], [0, -0.06, -1.25 - i * 0.3], this.dark));
    }
    return { group, muzzle: [0, 0.02, -3.4], rest: [0, -0.03, 0] };
  }

  private buildTacticalAssault(): Shape {
    const group = new this.three.Group();
    // Split upper and lower forged receiver
    group.add(this.box([0.24, 0.22, 1.5], [0, 0.08, -0.75], this.dark));
    group.add(this.box([0.23, 0.2, 0.95], [0, -0.11, -0.55], this.metal));
    // Ejection port, bolt carrier group & hinged dust cover
    group.add(this.box([0.03, 0.12, 0.32], [0.115, 0.08, -0.45], this.accent));
    group.add(this.box([0.06, 0.09, 0.09], [0.11, 0.0, -0.3], this.metal));
    group.add(this.box([0.16, 0.05, 0.12], [0, 0.17, 0.02], this.accent));
    // Modular railed handguard
    group.add(this.box([0.21, 0.22, 1.0], [0, 0.04, -1.65], this.grip));
    // Stepped chrome-moly barrel and gas block
    group.add(this.tube(0.048, 0.75, [0, 0.04, -2.4], this.metal));
    group.add(this.box([0.13, 0.16, 0.16], [0, 0.09, -2.2], this.dark));
    group.add(this.box([0.06, 0.2, 0.06], [0, 0.24, -2.2], this.accent));
    // A2 birdcage compensator with radial vents
    group.add(this.tube(0.072, 0.24, [0, 0.04, -2.78], this.dark));
    group.add(this.box([0.15, 0.05, 0.05], [0, 0.1, -2.74], this.accent));
    // Flattop Picatinny optic rail with aperture rear sight
    group.add(this.box([0.14, 0.06, 1.5], [0, 0.21, -0.85], this.metal));
    group.add(this.box([0.12, 0.14, 0.07], [0, 0.29, -0.2], this.accent));
    // Curved STANAG magazine in two segments with floorplate
    group.add(this.box([0.19, 0.36, 0.3], [0, -0.36, -0.79], this.metal, [-0.1, 0, 0]));
    group.add(this.box([0.18, 0.34, 0.29], [0, -0.66, -0.72], this.metal, [-0.26, 0, 0]));
    group.add(this.box([0.2, 0.06, 0.31], [0, -0.83, -0.68], this.dark, [-0.26, 0, 0]));
    group.add(this.box([0.08, 0.05, 0.34], [0, -0.28, -0.28], this.dark));
    group.add(this.box([0.08, 0.12, 0.05], [0, -0.24, -0.44], this.dark));
    group.add(this.box([0.05, 0.12, 0.05], [0, -0.2, -0.22], this.accent));
    // Ergonomic A2 pistol grip
    group.add(this.box([0.18, 0.44, 0.26], [0, -0.3, -0.02], this.grip, [0.3, 0, 0]));
    // Buffer tube, CTR stock, and buttpad
    group.add(this.tube(0.09, 0.7, [0, 0.02, 0.42], this.metal));
    group.add(this.box([0.22, 0.3, 0.6], [0, -0.04, 0.5], this.dark, [-0.04, 0, 0]));
    group.add(this.box([0.23, 0.34, 0.09], [0, -0.06, 0.82], this.dark));
    // Handguard vent slots
    for (let i = 0; i < 3; i += 1) {
      group.add(this.box([0.225, 0.06, 0.14], [0, 0.04, -1.35 - i * 0.28], this.dark));
    }
    // Top rail cross ribs
    for (let i = 0; i < 5; i += 1) {
      group.add(this.box([0.15, 0.09, 0.05], [0, 0.21, -0.35 - i * 0.16], this.dark));
    }
    return { group, muzzle: [0, 0.04, -2.92], rest: [0, -0.04, 0] };
  }

  /**
   * The weapon, by id.
   *
   * Ids are the backend's (`weapons.py`): knife, pistol, assault, shotgun,
   * sniper. An unknown id gets the rifle rather than nothing — a new weapon
   * should look wrong, not invisible.
   */
  private build(id: string, skin: WeaponSkin | null = null): Shape {
    switch (id) {
      case 'knife': {
        switch (knifeArchetype(skin?.id ?? '', skin?.name ?? '')) {
          case 'knife-karambit':
            return this.buildKarambit();
          case 'knife-butterfly':
            return this.buildButterfly();
          case 'knife-bayonet':
            return this.buildBayonet();
          case 'knife-skeleton':
            return this.buildSkeletonKnife();
          case 'knife-huntsman':
            return this.buildHuntsman();
          default:
            return this.buildTacticalKnife();
        }
      }

      case 'pistol':
        return this.buildTacticalPistol();

      case 'shotgun':
        return this.buildTacticalShotgun();

      case 'sniper':
        return this.buildTacticalSniper();

      default:
        // Assault rifle, and the fallback for anything new.
        return this.buildTacticalAssault();
    }
  }
}

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}
