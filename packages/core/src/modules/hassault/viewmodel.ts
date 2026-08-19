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

import { MOVE_SPEED } from './player';

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
    default:
      // `solid`, `patina`, `custom_art`: the base carries the weapon and the
      // accent picks out the barrel and the sights.
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
}

/**
 * One weapon in the hands, swapped by id.
 *
 * `setWeapon` is idempotent, so the render loop can call it every frame with
 * whatever the server last said we are holding and only pay for real changes.
 */
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

  // Built with the model rather than shared, because they now carry the skin:
  // two weapons in one match are two different guns, and a material shared
  // between them could only ever show one of them. Freed by `release`.
  private metal!: THREE.MeshLambertMaterial;
  private dark!: THREE.MeshLambertMaterial;
  private grip!: THREE.MeshLambertMaterial;
  private accent!: THREE.MeshLambertMaterial;

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
  }

  /** A shot left the barrel this frame: kick the model and light the muzzle. */
  fire(): void {
    // Additive but capped: holding down an assault rifle should climb to a steady
    // shake, not to a weapon behind the player's ear.
    this.kick = Math.min(1, this.kick + 0.8);
    this.flashAge = 0;
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

    const bobX = Math.cos(this.bobPhase * 0.5) * 0.05 * bobAmount;
    const bobY = Math.abs(Math.sin(this.bobPhase)) * -0.055 * bobAmount;

    this.pivot.position.set(
      HOME.x + bobX + this.swayX,
      HOME.y + bobY + this.swayY - this.reloadT * 0.55,
      // Recoil is mostly backwards: a gun that only rotates looks hinged.
      HOME.z + this.kick * 0.28,
    );
    this.pivot.rotation.set(
      this.kick * -0.16 + this.reloadT * 0.7 + bobY * 0.4,
      this.swayX * 0.7 + this.reloadT * 0.25,
      this.swayX * 0.5 + bobX * 0.6,
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
    this.metal = new this.three.MeshLambertMaterial({ color: palette.body });
    this.dark = new this.three.MeshLambertMaterial({ color: palette.dark });
    this.grip = new this.three.MeshLambertMaterial({ color: palette.grip });
    this.accent = new this.three.MeshLambertMaterial({ color: palette.accent });
  }

  /** Drop the current model and its resources. Swapping weapons calls this, so a
   * player cycling their loadout does not leak a rifle every time. */
  private release(): void {
    const built = this.built;
    this.built = null;
    this.flash = null;
    if (!built) return;
    this.pivot.remove(built.group);
    for (const geo of built.geometries) geo.dispose();
    for (const mat of built.materials) mat.dispose();
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

  /** A cylinder lying along -Z, which is the direction every barrel points. */
  private tube(
    radius: number,
    length: number,
    at: [number, number, number],
    material: THREE.Material,
  ): THREE.Mesh {
    const geo = new this.three.CylinderGeometry(radius, radius, length, 10);
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
        group.add(this.box([0.14, 0.17, 0.6], [0, 0, 0.1], this.grip));
        group.add(this.box([0.05, 0.05, 0.1], [0, 0, -0.24], this.accent));
        // Blade: a flat box, tapered by scaling the far end down.
        const blade = this.box([0.045, 0.26, 1.0], [0, 0.03, -0.8], this.accent);
        blade.scale.set(1, 0.7, 1);
        group.add(blade);
        return { group, muzzle: [0, 0.03, -1.3], rest: [0.06, -0.32, 0.22] };
      }

      case 'pistol': {
        group.add(this.box([0.22, 0.3, 1.05], [0, 0, -0.5], this.metal));
        group.add(this.tube(0.05, 0.3, [0, 0, -1.12], this.accent));
        group.add(this.box([0.2, 0.62, 0.34], [0, -0.42, -0.02], this.dark, [0.3, 0, 0]));
        // Trigger guard, as a bar under the receiver: small, but its absence is
        // what makes a box read as a box.
        group.add(this.box([0.1, 0.06, 0.3], [0, -0.24, -0.35], this.dark));
        group.add(this.box([0.06, 0.08, 0.05], [0, 0.19, -0.98], this.accent));
        return { group, muzzle: [0, 0, -1.3], rest: [0, -0.05, 0] };
      }

      case 'shotgun': {
        group.add(this.tube(0.08, 2.1, [-0.09, 0.02, -1.45], this.metal));
        group.add(this.tube(0.08, 2.1, [0.09, 0.02, -1.45], this.metal));
        group.add(this.box([0.34, 0.32, 0.8], [0, -0.02, -0.3], this.dark));
        // Pump, forward under the barrels.
        group.add(this.box([0.3, 0.2, 0.55], [0, -0.16, -1.15], this.grip));
        group.add(this.box([0.24, 0.36, 0.9], [0, -0.16, 0.5], this.grip, [-0.08, 0, 0]));
        return { group, muzzle: [0, 0.02, -2.5], rest: [0, -0.04, 0] };
      }

      case 'sniper': {
        group.add(this.tube(0.055, 2.5, [0, 0.02, -1.75], this.metal));
        group.add(this.box([0.26, 0.32, 1.1], [0, -0.04, -0.5], this.dark));
        // Scope on two mounts.
        group.add(this.tube(0.12, 0.9, [0, 0.32, -0.85], this.dark));
        group.add(this.box([0.08, 0.18, 0.08], [0, 0.18, -0.5], this.metal));
        group.add(this.box([0.08, 0.18, 0.08], [0, 0.18, -1.2], this.metal));
        // Bolt handle, sticking out to the right where you would work it.
        group.add(this.box([0.3, 0.07, 0.07], [0.18, 0.02, -0.15], this.accent));
        group.add(this.box([0.2, 0.5, 0.3], [0, -0.34, -0.35], this.dark, [0.18, 0, 0]));
        group.add(this.box([0.24, 0.4, 1.1], [0, -0.14, 0.55], this.grip, [-0.06, 0, 0]));
        return { group, muzzle: [0, 0.02, -3.0], rest: [0, -0.03, 0] };
      }

      default: {
        // Assault rifle, and the fallback for anything new.
        group.add(this.box([0.26, 0.36, 1.6], [0, 0, -0.8], this.dark));
        group.add(this.tube(0.055, 1.0, [0, 0.04, -2.0], this.metal));
        // Top rail and front sight.
        group.add(this.box([0.14, 0.09, 0.9], [0, 0.23, -0.7], this.metal));
        group.add(this.box([0.07, 0.16, 0.06], [0, 0.28, -2.35], this.accent));
        // Magazine, raked forward the way a curved one sits.
        group.add(this.box([0.2, 0.66, 0.32], [0, -0.46, -0.85], this.metal, [-0.14, 0, 0]));
        group.add(this.box([0.18, 0.46, 0.3], [0, -0.32, -0.2], this.dark, [0.34, 0, 0]));
        group.add(this.box([0.22, 0.32, 0.75], [0, -0.02, 0.35], this.dark, [-0.04, 0, 0]));
        return { group, muzzle: [0, 0.04, -2.5], rest: [0, -0.04, 0] };
      }
    }
  }
}

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}
