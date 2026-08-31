/**
 * The muzzle flash: a billboard, not a cone.
 *
 * ## What was wrong, precisely
 *
 * The flash used to be `ConeGeometry(0.16, 0.42, 5)` in an unlit
 * `MeshBasicMaterial` at opacity 0.9, sitting on the view model's pivot with
 * `renderOrder = 2` so it drew over everything. A cone has a direction, and the
 * direction it was given was **down the barrel — which points away from the
 * camera**. So the shape the player saw was that cone end-on: a five-sided
 * polygon, filling a chunk of the view, one thirtieth of a second at a time.
 * "The pistol shows a simple square when I shoot" was a literal description of a
 * pentagon, and it read as the *screen* flashing rather than the *gun*.
 *
 * A sprite cannot fail that way by construction: it is always square-on to the
 * camera, so there is no orientation left to get wrong. That is the reason for
 * the change — not that a soft texture is prettier.
 *
 * ## What is deliberately *not* here
 *
 * **No light.** The obvious way to make a gun feel punchier is a `PointLight` at
 * the muzzle, and it is exactly the thing being asked against: firing must not
 * change the lighting of the viewport. Nothing in this file touches the scene's
 * lights, the renderer's exposure, or tone mapping, and nothing that does should
 * be added to it.
 *
 * The flash is also **screen-size clamped** (`clampFlashScale`) rather than
 * simply made smaller. A fixed size in cube units is a different fraction of the
 * screen on every FOV, and the sniper divides its FOV by the magnification — so
 * an un-clamped flash that looked right hipfiring would fill a scoped view.
 *
 * Procedural, like `surfaces.ts`, and for the same two reasons: nothing is
 * bundled in this module, and a `DataTexture` computed from a function can be
 * tested headless where a canvas or an image could not.
 */
import type * as THREE from 'three';

/**
 * How long the core is lit. Two frames at 60 fps.
 *
 * Short on purpose: a flash you can *look at* is a flash that is in the way. It
 * is the transient that sells the shot, and the tracer plus the crosshair kick
 * carry the rest.
 */
export const FLASH_LIFE = 0.055;

/** How long the smoke wisp lingers after the core has gone. */
export const FLASH_SMOKE_LIFE = 0.35;

/**
 * The largest fraction of the viewport's height one flash may cover.
 *
 * The number that stops this being a screen effect again. At the default FOV a
 * pistol flash lands well under it; scoped, where the FOV is a quarter as wide
 * and everything is four times bigger, this is what bites.
 */
export const FLASH_MAX_SCREEN_FRACTION = 0.16;

/** The hot centre, and the wider fringe around it. */
export const FLASH_CORE = 0xfff0c8;
export const FLASH_HALO = 0xffb257;
/** The wisp that follows. Grey-warm, so it reads as smoke and not as a second flash. */
export const FLASH_SMOKE = 0x8d8377;

/** Tile edge, in pixels. Small deliberately — this is a blur, not a picture. */
const SIZE = 64;

/**
 * The base radius of one weapon's flash, in cube units, and how elongated it is.
 *
 * Kept per weapon because it is a real cue: a shotgun's bloom is wide and short,
 * a sniper's is narrow and long, and at the far end of a corridor that shape is
 * often the only thing that says which of the two just fired at you. The native
 * client carries its own copy of this table (`viewmodel.rs`, `flash_shape`) and
 * `browser_parity.rs` pins the two together — the same bargain the weapon voices
 * make.
 */
const SHAPES: Record<string, { radius: number; stretch: number }> = {
  pistol: { radius: 0.13, stretch: 1.15 },
  assault: { radius: 0.16, stretch: 1.35 },
  shotgun: { radius: 0.26, stretch: 0.85 },
  sniper: { radius: 0.15, stretch: 1.9 },
};
/** The rifle, and anything the server has grown since this build — a new weapon
 * should look ordinary, not invisible. */
const DEFAULT_SHAPE = { radius: 0.15, stretch: 1.2 };

/**
 * How this weapon's flash is shaped, or `null` for a weapon with no muzzle.
 *
 * **The knife has none, and that is a rule rather than an omission.** A swing is
 * resolved as a `Shot` like everything else, so it reaches here — and a flare on
 * it would light up the one weapon whose entire value is that carrying it gives
 * nothing away. The native client has always refused it (`viewmodel.rs`,
 * `flash_shape`); the browser used to flash for it, which is the kind of
 * divergence only a parity test finds.
 */
export function flashShape(weaponId: string): { radius: number; stretch: number } | null {
  if (weaponId === 'knife') return null;
  return SHAPES[weaponId] ?? DEFAULT_SHAPE;
}

/**
 * Deterministic hash in [0, 1). Lifted from `surfaces.ts` rather than imported,
 * because that one is a *lattice* hash taking a cell — this takes an index.
 */
function hash(n: number): number {
  const x = Math.sin(n * 127.1 + 311.7) * 43758.5453123;
  return x - Math.floor(x);
}

/**
 * One radial flash tile: bright at the centre, transparent at the rim, with a
 * few spokes so it is not a perfect disc.
 *
 * **The alpha must reach zero at the rim.** A tile that does not is a hard-edged
 * bright circle, which is the pentagon's failure wearing a different shape — the
 * whole reason this is a texture and not a mesh is the soft edge.
 */
export function drawFlashTile(size = SIZE): Uint8Array {
  const out = new Uint8Array(size * size * 4);
  const half = size / 2;
  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      // +0.5 samples the pixel centre; without it the tile is half a pixel off
      // and the spokes are lopsided.
      const dx = (px + 0.5 - half) / half;
      const dy = (py + 0.5 - half) / half;
      const r = Math.hypot(dx, dy);
      // Steep falloff: most of the energy in the middle sixth, and nothing left
      // by the rim. `1 - r` alone is a cone again, in a texture.
      let a = Math.max(0, 1 - r);
      a = a * a * a;
      // Six soft spokes, so a still frame reads as a burst rather than a dot.
      const angle = Math.atan2(dy, dx);
      a *= 0.72 + 0.28 * Math.abs(Math.cos(angle * 3));
      // Hard zero at and beyond the rim, so the clamp above cannot be defeated
      // by the spoke term.
      if (r >= 1) a = 0;
      const i = (py * size + px) * 4;
      out[i] = 255;
      out[i + 1] = 255;
      out[i + 2] = 255;
      out[i + 3] = Math.round(Math.max(0, Math.min(1, a)) * 255);
    }
  }
  return out;
}

/**
 * One puff of smoke: the same radial falloff, roughened so it does not read as a
 * second, dimmer flash.
 */
export function drawSmokeTile(size = SIZE): Uint8Array {
  const out = new Uint8Array(size * size * 4);
  const half = size / 2;
  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      const dx = (px + 0.5 - half) / half;
      const dy = (py + 0.5 - half) / half;
      const r = Math.hypot(dx, dy);
      let a = Math.max(0, 1 - r);
      // Gentler than the flash: smoke has a body, where a flash is all centre.
      a = a * a;
      // Lumpy, from a hash of the cell rather than of the angle, so the puff has
      // no symmetry to spot.
      a *= 0.6 + 0.4 * hash(px * 31 + py * 17);
      if (r >= 1) a = 0;
      const i = (py * size + px) * 4;
      out[i] = 255;
      out[i + 1] = 255;
      out[i + 2] = 255;
      out[i + 3] = Math.round(Math.max(0, Math.min(1, a)) * 255);
    }
  }
  return out;
}

/** Wrap a tile as a texture. White RGB throughout — the colour is the material's. */
export function createFlashTexture(
  three: typeof THREE,
  data: Uint8Array,
  size = SIZE,
): THREE.DataTexture {
  const texture = new three.DataTexture(data, size, size, three.RGBAFormat);
  texture.magFilter = three.LinearFilter;
  texture.minFilter = three.LinearFilter;
  // No mipmaps: this is drawn at a handful of sizes a few centimetres from the
  // camera and never minified, so a chain would be memory spent on nothing.
  texture.generateMipmaps = false;
  texture.colorSpace = three.SRGBColorSpace;
  texture.needsUpdate = true;
  return texture;
}

/**
 * How big this weapon's flash is, `t` seconds into its life.
 *
 * Pulled out of the render loop so it can be tested. It used to be
 * `0.85 + Math.random() * 0.5` applied every frame the flash was lit, which made
 * the flash *flicker in size* — two frames, two different sizes, no relationship
 * between them. The randomness is kept (two shots should not look identical) but
 * it is seeded per shot by the caller and the *shape* over the flash's life is a
 * decay, so one shot is one event.
 */
export function flashScale(weaponId: string, t: number, seed = 0): number {
  const shape = flashShape(weaponId);
  if (shape === null) return 0;
  const life = Math.max(0, Math.min(1, t / FLASH_LIFE));
  // Opens instantly and shrinks away; a flash that grows reads as an explosion.
  const decay = 1 - life * life * 0.45;
  return shape.radius * (0.85 + hash(seed) * 0.45) * decay;
}

/**
 * How much wider the halo is than the core, before the weapon's own stretch.
 *
 * The halo is the only place `stretch` is expressed. A sprite faces the camera,
 * so "long" has no direction to point in — but a longer flash *does* throw more
 * light sideways, and a wider, dimmer fringe is what that looks like from
 * behind the gun.
 */
export const FLASH_HALO_SCALE = 2.1;

/** The size of the wisp, relative to the core, at the end of its life. */
export const FLASH_SMOKE_SCALE = 2.6;

/** How far the wisp drifts up, in cube units, over its whole life. */
export const FLASH_SMOKE_RISE = 0.22;

/** The halo's size for one weapon, given its core size. */
export function haloScale(weaponId: string, coreScale: number): number {
  const shape = flashShape(weaponId);
  return shape === null ? 0 : coreScale * FLASH_HALO_SCALE * shape.stretch;
}

/**
 * The wisp, `t` seconds in: how big, and how opaque.
 *
 * Grows and fades together — smoke expands as it cools, and a puff that held its
 * size while fading would read as a light going out rather than as smoke.
 */
export function smokePuff(
  weaponId: string,
  t: number,
): { scale: number; opacity: number } {
  const life = Math.max(0, Math.min(1, t / FLASH_SMOKE_LIFE));
  const shape = flashShape(weaponId);
  if (shape === null) return { scale: 0, opacity: 0 };
  const grow = 0.6 + (FLASH_SMOKE_SCALE - 0.6) * life;
  return {
    scale: shape.radius * grow,
    // Fades from a low starting opacity: this is a hint that the barrel is hot,
    // not a smoke grenade. Squared so most of the life is nearly gone.
    opacity: 0.22 * (1 - life) * (1 - life),
  };
}

/**
 * Cap a flash so it can never take over the view.
 *
 * `fovRadians` is the camera's FOV **as it is right now**, not the base one.
 * Scoping divides the FOV by the magnification, so a cap computed against the
 * base value would be correct hipfiring and four times too generous at 4× —
 * which is precisely the case where a flash in the middle of the screen is least
 * welcome.
 */
export function clampFlashScale(
  scale: number,
  distance: number,
  fovRadians: number,
  maxFraction = FLASH_MAX_SCREEN_FRACTION,
): number {
  if (!(distance > 0) || !(fovRadians > 0)) return scale;
  // Half the visible height at that distance, in world units. A sprite of
  // `scale` covers `scale / (2 * halfHeight)` of the viewport's height.
  const halfHeight = Math.tan(fovRadians / 2) * distance;
  const limit = maxFraction * halfHeight * 2;
  return Math.min(scale, limit);
}
