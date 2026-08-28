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

import { PROP_ENV_INTENSITY, fitWeaponModel, loadWeaponModel } from './models/weapons';

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
const HOME = { x: 0.92, y: -0.86, z: -1.35 };

/** How long the muzzle flash stays lit. Two frames at 60 fps. */
const FLASH_LIFE = 0.055;

/** Recoil decay and reload-dip rates, per second. */
const KICK_DECAY = 11;
const RELOAD_RATE = 6;

export interface ViewModelFrame {
  /** Horizontal speed in cubes per second, for the walk cycle. */
  speed: number;
  onGround: boolean;
  reloading: boolean;
  /** View angles, so the weapon can lag a turn slightly instead of being welded on. */
  yaw: number;
  pitch: number;
  /** False while dead, spectating, or before deploying. */
  visible: boolean;
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
    definition?: { weaponId: string; baseColor: string; accentColor: string; patternType: string };
  }[],
): Record<string, WeaponSkin> {
  const out: Record<string, WeaponSkin> = {};
  for (const item of inventory) {
    if (!item.isEquipped || !item.definition) continue;
    out[item.definition.weaponId] = {
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
 * How long one inspect takes, in seconds.
 *
 * Long enough to read the weapon, short enough to be over before it costs a
 * gunfight — and it is interruptible anyway, so this is a maximum rather than a
 * commitment. Shared with the native client's `INSPECT_DURATION`, which runs the
 * same pose: the two clients drawing the same weapon differently is the drift
 * this module's shape exists to avoid.
 */
export const INSPECT_DURATION = 1.35;

/**
 * The inspect pose's weight over its own duration: ease in, hold, ease out.
 *
 * Smoothstepped at both ends rather than linear. A linear ramp reverses
 * direction instantly at the hold, which reads as the animation being cut off
 * and restarted — the one thing a "look at this weapon" flourish must not do.
 */
export function inspectEnvelope(t: number): number {
  const RISE = 0.28;
  const FALL = 0.42;
  const out = INSPECT_DURATION - FALL;
  const x = clamp(t < RISE ? t / RISE : t > out ? 1 - (t - out) / FALL : 1, 0, 1);
  return x * x * (3 - 2 * x);
}

export class WeaponViewModel {
  /** The pivot everything hangs off: animation moves this, never the model. */
  private readonly pivot: THREE.Group;
  private built: Built | null = null;
  private weaponId = '';
  /** The skin the current model was built with, so a change of skin rebuilds it
   * and an unchanged one does not. */
  private skinKey = '';
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

  private flash: THREE.Mesh | null = null;
  private flashAge = FLASH_LIFE;
  private kick = 0;
  private bobPhase = 0;
  private reloadT = 0;
  private lastYaw: number | null = null;
  private lastPitch = 0;
  private swayX = 0;
  private swayY = 0;
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
      ? `${skin.baseColor}|${skin.accentColor}|${skin.patternType}|${skin.floatValue}`
      : '';
    if (id === this.weaponId && skinKey === this.skinKey) return;
    this.weaponId = id;
    this.skinKey = skinKey;
    this.release();
    if (!id) return;
    this.setPalette(skin);

    this.building = [];
    const shape = this.build(id);
    this.pivot.add(shape.group);
    shape.group.rotation.set(shape.rest[0], shape.rest[1], shape.rest[2]);

    // The flash lives on the model, not the pivot: it belongs at the end of
    // *this* barrel, and a shared one would sit at the wrong place after a swap.
    const flashGeo = new this.three.ConeGeometry(0.16, 0.42, 5);
    const flashMat = new this.three.MeshBasicMaterial({
      color: 0xffd27a,
      transparent: true,
      opacity: 0.9,
    });
    this.building.push(flashGeo);
    const flash = new this.three.Mesh(flashGeo, flashMat);
    // Cone points +Y by default; lay it along -Z, pointing away from the shooter.
    flash.rotation.x = -Math.PI / 2;
    flash.position.set(shape.muzzle[0], shape.muzzle[1], shape.muzzle[2] - 0.2);
    flash.visible = false;
    shape.group.add(flash);
    this.flash = flash;
    this.built = {
      ...shape,
      geometries: this.building,
      materials: [flashMat],
    };
    this.building = [];

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
    void loadWeaponModel(id)
      .then((asset) => {
        // Three ways to be stale, and they are all the same check: the weapon
        // changed, the skin changed, or the view model was disposed while the
        // fetch was in flight.
        if (!asset || this.propToken !== token || !this.built) return;
        const built = this.built;
        const { model, muzzle } = fitWeaponModel(this.three, asset.prototype, built.group);

        // The skin tints the prop rather than repainting it: these materials
        // carry real texture maps, and `color` multiplies the base colour map.
        // White is not a special case here — it is the identity, which is
        // exactly what "no skin" should mean.
        const tint = skin ? paletteFor(skin).body : 0xffffff;
        const materials: THREE.Material[] = [];
        model.traverse((obj) => {
          const mesh = obj as THREE.Mesh;
          if (!mesh.isMesh) return;
          for (const mat of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
            const tinted = mat as THREE.MeshStandardMaterial;
            tinted.color?.setHex(tint);
            // Without this the weapon is a silhouette: a metal has no diffuse
            // term, so analytic lights alone leave it with nothing to return.
            tinted.envMap = this.environment;
            tinted.envMapIntensity = PROP_ENV_INTENSITY;
            tinted.needsUpdate = true;
            materials.push(mat);
          }
        });

        // The boxes go, the flash stays: it belongs to *this* barrel and its
        // position is recomputed from the prop's own muzzle. Removing the
        // children rather than the group keeps the group's rest rotation and
        // everything hanging off it, including the flash.
        for (const child of [...built.group.children]) {
          if (child !== this.flash) built.group.remove(child);
        }
        for (const geo of built.geometries) geo.dispose();
        built.geometries = [];
        built.group.add(model);
        // The prop is exported already oriented, so it needs none of the box
        // model's resting rotation — that was a property of how the boxes were
        // built, not of how a weapon is held.
        built.group.rotation.set(0, 0, 0);
        built.muzzle = muzzle;
        if (this.flash) this.flash.position.set(muzzle[0], muzzle[1], muzzle[2] - 0.2);
        // Tracked so `release` frees them: a clone owns its own materials.
        built.materials.push(...materials);
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

  /** A shot left the barrel this frame: kick the model and light the muzzle. */
  fire(): void {
    // Additive but capped: holding down an assault rifle should climb to a steady
    // shake, not to a weapon behind the player's ear.
    this.kick = Math.min(1, this.kick + 0.8);
    this.flashAge = 0;
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
   * the key means "show me the gun", and it should answer every press.
   */
  inspect(): void {
    if (this.built) this.inspectT = 0;
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
      return;
    }

    const target = Math.min(1, Math.max(0, frame.speed / MOVE_SPEED));
    this.walk += (target - this.walk) * Math.min(1, dt * 8);
    const walk = this.walk;
    this.bobPhase += dt * (4.5 + walk * 7.5);
    // Airborne, the weapon settles: bobbing in mid-air reads as a bug.
    const bobAmount = frame.onGround ? walk : walk * 0.15;

    // Turning drags the weapon behind the view for a fraction of a second, which
    // is the difference between a held object and a decal on the screen.
    const yawDelta = this.lastYaw === null ? 0 : frame.yaw - this.lastYaw;
    const pitchDelta = frame.pitch - this.lastPitch;
    this.lastYaw = frame.yaw;
    this.lastPitch = frame.pitch;
    const settle = Math.min(1, dt * 9);
    this.swayX += (clamp(-yawDelta * 2.2, -0.22, 0.22) - this.swayX) * settle;
    this.swayY += (clamp(-pitchDelta * 1.6, -0.18, 0.18) - this.swayY) * settle;

    this.kick -= this.kick * Math.min(1, dt * KICK_DECAY);
    const reloadTarget = frame.reloading ? 1 : 0;
    this.reloadT += (reloadTarget - this.reloadT) * Math.min(1, dt * RELOAD_RATE);

    // A reload takes the weapon away for an animation of its own, and two poses
    // fighting over one pivot is a weapon that looks broken. The reload wins,
    // because it is the one the *server* is actually doing.
    if (frame.reloading) this.inspectT = null;
    // Advanced before it is read, so the frame it completes on is the frame the
    // weapon is back at rest rather than one after.
    let inspect = 0;
    if (this.inspectT !== null) {
      const t = this.inspectT + dt;
      if (t >= INSPECT_DURATION) {
        this.inspectT = null;
      } else {
        this.inspectT = t;
        inspect = inspectEnvelope(t);
      }
    }

    const bobX = Math.cos(this.bobPhase * 0.5) * 0.05 * bobAmount;
    const bobY = Math.abs(Math.sin(this.bobPhase)) * -0.055 * bobAmount;

    // Where the inspect pose takes the weapon: in towards the centre of the
    // screen, up, and rolled most of the way over so the side of the receiver —
    // which is where a skin's pattern lives — faces the camera. A pose that only
    // lifted the gun would show the face it already shows.
    this.pivot.position.set(
      HOME.x + bobX + this.swayX - inspect * 0.3,
      HOME.y + bobY + this.swayY - this.reloadT * 0.55 + inspect * 0.16,
      // Recoil is mostly backwards: a gun that only rotates looks hinged.
      HOME.z + this.kick * 0.28 + inspect * 0.2,
    );
    this.pivot.rotation.set(
      this.kick * -0.16 + this.reloadT * 0.7 + bobY * 0.4 + inspect * 0.34,
      this.swayX * 0.7 + this.reloadT * 0.25 - inspect * 0.95,
      this.swayX * 0.5 + bobX * 0.6 + inspect * 2.15,
    );

    if (this.flash) {
      this.flashAge += dt;
      const lit = this.flashAge < FLASH_LIFE;
      this.flash.visible = lit;
      if (lit) {
        // A different size every frame it is lit, so two shots never flash
        // identically — cheaper and more convincing than a fade.
        const scale = 0.85 + Math.random() * 0.5;
        this.flash.scale.set(scale, 1, scale);
      }
    }
  }

  dispose(): void {
    this.release();
    this.camera.remove(this.pivot);
    for (const mat of [this.metal, this.dark, this.grip, this.accent]) mat.dispose();
    this.grain?.dispose();
    this.grain = null;
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
    // A worn gun is a dull gun: the float already dulls the colour, and letting
    // it dull the shine too is the difference between a Factory New that looks
    // new and one that is merely brighter.
    const polish = 1 - wear * 0.75;

    const make = (color: number, specular: number, shininess: number) =>
      new this.three.MeshPhongMaterial({
        color,
        specular: new this.three.Color(specular).multiplyScalar(polish),
        shininess: shininess * polish,
        map: grain,
      });

    // Machined metal: the brightest highlight and the tightest.
    this.metal = make(palette.body, 0x6b7280, 34);
    // Anodised or blued: darker, still metal, softer highlight.
    this.dark = make(palette.dark, 0x3f4650, 18);
    // Polymer and rubber: almost none, and broad. A grip that glints reads as
    // wet plastic, which is the one thing furniture must not look like.
    this.grip = make(palette.grip, 0x1d2026, 6);
    // Hardware and trim: the shiniest thing on the gun, which is what makes
    // sights and bolts catch the eye at all.
    this.accent = make(palette.accent, 0x8a94a3, 52);
  }

  /** Drop the current model and its resources. Swapping weapons calls this, so a
   * player cycling their loadout does not leak a rifle every time. */
  private release(): void {
    const built = this.built;
    this.built = null;
    this.flash = null;
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
  private cone(
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

  /**
   * The weapon, by id.
   *
   * Ids are the backend's (`weapons.py`): knife, pistol, assault, shotgun,
   * sniper. An unknown id gets the rifle rather than nothing — a new weapon
   * should look wrong, not invisible.
   */
  private build(id: string): Shape {
    const group = new this.three.Group();
    switch (id) {
      case 'knife': {
        // Handle in three segments rather than one box, so it has a swell in the
        // middle and a pommel at the end — the difference between a knife and a
        // stick with a blade on it.
        group.add(this.box([0.13, 0.16, 0.5], [0, 0, 0.14], this.grip));
        group.add(this.box([0.15, 0.185, 0.24], [0, 0, 0.06], this.grip));
        group.add(this.box([0.16, 0.2, 0.09], [0, 0, 0.38], this.dark));
        // A rounded butt cap, so the handle ends in something rather than
        // stopping square.
        group.add(this.tube(0.075, 0.06, [0, 0, 0.44], this.accent));
        // Lanyard hole, as a notch through the pommel.
        group.add(this.box([0.17, 0.05, 0.05], [0, 0.02, 0.38], this.metal));
        // Guard and ricasso.
        group.add(this.box([0.2, 0.2, 0.07], [0, 0.01, -0.2], this.dark));
        group.add(this.box([0.06, 0.13, 0.14], [0, 0.03, -0.31], this.metal));

        // Blade in two layers: a spine at full thickness with a flat ground
        // bevel under it, which is what catches the light differently along an
        // edge. One box has no edge, only a thickness.
        const spine = this.box([0.05, 0.2, 0.95], [0, 0.08, -0.85], this.metal);
        spine.scale.set(1, 0.85, 1);
        group.add(spine);
        group.add(this.box([0.035, 0.14, 0.92], [0, -0.035, -0.84], this.accent));
        // Tip: a short tapered section, so the blade comes to a point instead of
        // stopping square.
        const tip = this.box([0.04, 0.17, 0.3], [0, 0.02, -1.42], this.accent);
        tip.scale.set(0.7, 0.45, 1);
        group.add(tip);
        // Serrations on the spine, three teeth near the guard.
        for (let i = 0; i < 3; i += 1) {
          group.add(this.box([0.055, 0.05, 0.05], [0, 0.15, -0.5 - i * 0.13], this.dark));
        }
        return { group, muzzle: [0, 0.03, -1.5], rest: [0.06, -0.32, 0.22] };
      }

      case 'pistol': {
        // A slide riding a frame, as two stacked boxes with a seam between them.
        group.add(this.box([0.21, 0.24, 1.15], [0, 0.03, -0.52], this.metal));
        group.add(this.box([0.19, 0.12, 0.95], [0, -0.13, -0.45], this.dark));
        // Ejection port, inset on the right of the slide.
        group.add(this.box([0.03, 0.13, 0.34], [0.1, 0.05, -0.42], this.dark));
        // Slide serrations: four ribs at the rear. Small, but they are what make
        // the top of a pistol read as machined rather than moulded.
        for (let i = 0; i < 4; i += 1) {
          group.add(this.box([0.225, 0.2, 0.035], [0, 0.03, -0.04 - i * 0.09], this.dark));
        }
        // Tapered, so the muzzle end is visibly the narrow one.
        group.add(this.cone(0.052, 0.042, 0.26, [0, -0.01, -1.16], this.accent));
        group.add(this.tube(0.064, 0.06, [0, -0.01, -1.27], this.dark));
        // The recoil spring plug under the barrel — a small round face at the
        // front of the slide, and the thing that stops the muzzle end being one
        // flat rectangle.
        group.add(this.tube(0.05, 0.05, [0, -0.11, -1.05], this.dark));
        // Grip, with a backstrap and a magazine baseplate under it.
        group.add(this.box([0.2, 0.62, 0.32], [0, -0.42, -0.02], this.grip, [0.3, 0, 0]));
        group.add(this.box([0.21, 0.1, 0.3], [0, -0.7, 0.08], this.dark, [0.3, 0, 0]));
        group.add(this.box([0.06, 0.5, 0.08], [0, -0.4, 0.14], this.dark, [0.3, 0, 0]));
        // Trigger guard as three bars, so it is a loop with a hole in it.
        group.add(this.box([0.09, 0.05, 0.34], [0, -0.28, -0.36], this.dark));
        group.add(this.box([0.09, 0.13, 0.05], [0, -0.22, -0.52], this.dark));
        group.add(this.box([0.05, 0.13, 0.05], [0, -0.2, -0.28], this.accent));
        // Sights: a notch at the back and a blade at the front. They are what the
        // eye follows down the top of the gun, so the weapon has a direction.
        group.add(this.box([0.14, 0.06, 0.05], [0, 0.17, -0.05], this.accent));
        group.add(this.box([0.05, 0.07, 0.05], [0, 0.18, -1.02], this.accent));
        return { group, muzzle: [0, -0.01, -1.32], rest: [0, -0.05, 0] };
      }

      case 'shotgun': {
        // Over-and-under rather than side-by-side: stacked barrels read as a
        // shotgun from the shooter's eye, where two tubes abreast look merely wide.
        group.add(this.cone(0.078, 0.068, 2.2, [0, 0.09, -1.5], this.metal));
        group.add(this.cone(0.078, 0.068, 2.2, [0, -0.05, -1.5], this.metal));
        // Rib joining them, with a bead sight at the end of it.
        group.add(this.box([0.05, 0.16, 2.0], [0, 0.02, -1.5], this.dark));
        group.add(this.box([0.06, 0.06, 0.06], [0, 0.19, -2.45], this.accent));
        // A wider ring at each muzzle, so the bore has a mouth.
        group.add(this.tube(0.095, 0.12, [0, 0.09, -2.55], this.dark));
        group.add(this.tube(0.095, 0.12, [0, -0.05, -2.55], this.dark));
        // Receiver, deeper than the barrels and squared off at the breech.
        group.add(this.box([0.32, 0.38, 0.85], [0, -0.04, -0.3], this.dark));
        group.add(this.box([0.34, 0.42, 0.12], [0, -0.04, 0.14], this.metal));
        group.add(this.box([0.2, 0.06, 0.5], [0, -0.24, -0.3], this.accent));
        // Pump, ribbed, forward under the barrels, with the action bar running
        // back to the receiver — the part that moves when it is worked.
        group.add(this.box([0.3, 0.24, 0.62], [0, -0.19, -1.2], this.grip));
        for (let i = 0; i < 4; i += 1) {
          group.add(this.box([0.315, 0.055, 0.05], [0, -0.19, -1.42 + i * 0.14], this.dark));
        }
        group.add(this.box([0.06, 0.05, 0.75], [0.1, -0.24, -0.82], this.metal));
        group.add(this.box([0.09, 0.05, 0.3], [0, -0.26, -0.16], this.dark));
        group.add(this.box([0.05, 0.11, 0.05], [0, -0.2, -0.2], this.accent));
        // Stock: a wrist that drops away, a comb, and a recoil pad.
        group.add(this.box([0.22, 0.3, 0.5], [0, -0.14, 0.42], this.grip, [-0.12, 0, 0]));
        group.add(this.box([0.2, 0.34, 0.6], [0, -0.24, 0.9], this.grip, [-0.08, 0, 0]));
        group.add(this.box([0.21, 0.36, 0.09], [0, -0.28, 1.22], this.dark, [-0.08, 0, 0]));
        return { group, muzzle: [0, 0.02, -2.62], rest: [0, -0.04, 0] };
      }

      case 'sniper': {
        // A heavy section out of the receiver stepping down to a thin one: the
        // step is what gives a sniper its length rather than the length alone.
        // The step from a heavy chamber section to a thin barrel, drawn as a
        // taper rather than two pipes meeting at a shoulder.
        group.add(this.cone(0.078, 0.055, 1.1, [0, 0.02, -1.05], this.metal));
        group.add(this.cone(0.052, 0.045, 1.6, [0, 0.02, -2.35], this.metal));
        // Fluting: four shallow grooves along the heavy section, which is the
        // detail that says "target rifle" at a glance.
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
        // Muzzle brake, ported.
        group.add(this.tube(0.085, 0.28, [0, 0.02, -3.24], this.dark));
        group.add(this.box([0.2, 0.05, 0.05], [0, 0.08, -3.2], this.accent));
        group.add(this.box([0.2, 0.05, 0.05], [0, 0.08, -3.3], this.accent));

        group.add(this.box([0.26, 0.34, 1.2], [0, -0.04, -0.5], this.dark));
        group.add(this.box([0.22, 0.24, 1.0], [0, -0.06, -1.6], this.grip));
        for (let i = 0; i < 3; i += 1) {
          group.add(this.box([0.235, 0.07, 0.12], [0, -0.06, -1.25 - i * 0.3], this.dark));
        }
        // Scope: a body, two bells, turrets, and mounts under it. The bells are
        // what stop a scope reading as a length of pipe.
        group.add(this.tube(0.11, 0.95, [0, 0.34, -0.85], this.dark));
        group.add(this.tube(0.145, 0.22, [0, 0.34, -1.36], this.dark));
        group.add(this.tube(0.125, 0.18, [0, 0.34, -0.33], this.dark));
        group.add(this.tube(0.135, 0.04, [0, 0.34, -1.47], this.accent));
        group.add(this.box([0.09, 0.11, 0.16], [0, 0.47, -0.9], this.accent));
        group.add(this.box([0.16, 0.09, 0.14], [0.12, 0.34, -0.9], this.accent));
        group.add(this.box([0.1, 0.2, 0.1], [0, 0.19, -0.55], this.metal));
        group.add(this.box([0.1, 0.2, 0.1], [0, 0.19, -1.18], this.metal));
        // Bolt: a body along the receiver, its handle turned down at the end.
        group.add(this.tube(0.045, 0.4, [0.12, 0.06, -0.12], this.metal));
        group.add(this.box([0.3, 0.06, 0.06], [0.24, 0.04, -0.04], this.metal));
        group.add(this.box([0.07, 0.07, 0.07], [0.38, 0.0, -0.04], this.accent));
        // Magazine, straight and boxy the way a bolt gun's is — the shape that
        // tells it apart from the rifle's curve at a glance.
        group.add(this.box([0.19, 0.44, 0.34], [0, -0.36, -0.5], this.dark));
        group.add(this.box([0.21, 0.06, 0.36], [0, -0.57, -0.5], this.metal));
        group.add(this.box([0.18, 0.46, 0.26], [0, -0.32, -0.06], this.grip, [0.26, 0, 0]));
        // Stock, skeletonised: a top rail and a bottom rail with a gap between
        // them, then a cheek riser and a butt pad.
        group.add(this.box([0.2, 0.09, 0.95], [0, 0.06, 0.55], this.dark));
        group.add(this.box([0.2, 0.09, 0.8], [0, -0.28, 0.5], this.dark));
        group.add(this.box([0.22, 0.16, 0.4], [0, 0.19, 0.55], this.grip));
        group.add(this.box([0.24, 0.44, 0.1], [0, -0.06, 1.0], this.dark));
        return { group, muzzle: [0, 0.02, -3.4], rest: [0, -0.03, 0] };
      }

      default: {
        // Assault rifle, and the fallback for anything new.
        // Upper and lower receiver as separate boxes, with a visible seam.
        group.add(this.box([0.24, 0.22, 1.5], [0, 0.08, -0.75], this.dark));
        group.add(this.box([0.23, 0.2, 0.95], [0, -0.11, -0.55], this.metal));
        group.add(this.box([0.03, 0.12, 0.3], [0.115, 0.08, -0.45], this.accent));
        group.add(this.box([0.06, 0.09, 0.09], [0.11, 0.0, -0.3], this.metal));
        group.add(this.box([0.16, 0.05, 0.12], [0, 0.17, 0.02], this.accent));

        // Slotted handguard, then the barrel and a birdcage muzzle device.
        group.add(this.box([0.21, 0.22, 1.0], [0, 0.04, -1.65], this.grip));
        for (let i = 0; i < 3; i += 1) {
          group.add(this.box([0.225, 0.06, 0.14], [0, 0.04, -1.35 - i * 0.28], this.dark));
        }
        group.add(this.cone(0.048, 0.04, 0.75, [0, 0.04, -2.4], this.metal));
        // Birdcage: a wider ring with slots cut in it, rather than a plain cap.
        group.add(this.tube(0.072, 0.24, [0, 0.04, -2.78], this.dark));
        for (let i = 0; i < 3; i += 1) {
          const angle = Math.PI * (0.25 + i * 0.25);
          group.add(
            this.box(
              [0.03, 0.09, 0.1],
              [Math.cos(angle) * 0.06, 0.04 + Math.sin(angle) * 0.06, -2.78],
              this.metal,
            ),
          );
        }
        group.add(this.box([0.15, 0.05, 0.05], [0, 0.1, -2.74], this.accent));
        // Gas block and front sight post.
        group.add(this.box([0.13, 0.16, 0.16], [0, 0.09, -2.2], this.dark));
        group.add(this.box([0.06, 0.2, 0.06], [0, 0.24, -2.2], this.accent));
        // Top rail, ribbed, with a rear aperture sight.
        group.add(this.box([0.14, 0.06, 1.5], [0, 0.21, -0.85], this.metal));
        for (let i = 0; i < 5; i += 1) {
          group.add(this.box([0.15, 0.09, 0.05], [0, 0.21, -0.35 - i * 0.16], this.dark));
        }
        group.add(this.box([0.12, 0.14, 0.07], [0, 0.29, -0.2], this.accent));

        // Magazine in two raked segments, so the curve is drawn rather than
        // implied by one tilted box.
        group.add(this.box([0.19, 0.36, 0.3], [0, -0.36, -0.79], this.metal, [-0.1, 0, 0]));
        group.add(this.box([0.18, 0.34, 0.29], [0, -0.66, -0.72], this.metal, [-0.26, 0, 0]));
        group.add(this.box([0.2, 0.06, 0.31], [0, -0.83, -0.68], this.dark, [-0.26, 0, 0]));
        group.add(this.box([0.08, 0.05, 0.34], [0, -0.28, -0.28], this.dark));
        group.add(this.box([0.08, 0.12, 0.05], [0, -0.24, -0.44], this.dark));
        group.add(this.box([0.05, 0.12, 0.05], [0, -0.2, -0.22], this.accent));
        // Pistol grip, and a buffer-tube stock with a cheek weld and a butt pad.
        group.add(this.box([0.18, 0.44, 0.26], [0, -0.3, -0.02], this.grip, [0.3, 0, 0]));
        group.add(this.tube(0.09, 0.7, [0, 0.02, 0.42], this.metal));
        group.add(this.box([0.22, 0.3, 0.6], [0, -0.04, 0.5], this.dark, [-0.04, 0, 0]));
        group.add(this.box([0.23, 0.34, 0.09], [0, -0.06, 0.82], this.dark));
        return { group, muzzle: [0, 0.04, -2.92], rest: [0, -0.04, 0] };
      }
    }
  }
}

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}
