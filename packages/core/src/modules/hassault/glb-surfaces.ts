/**
 * How a modelled (`hd_*.glb`) map's materials become textured surfaces.
 *
 * The GLBs the Blender generators write carry a base colour per material and
 * nothing else — no images, and no `TEXCOORD_0`. The second is the one that
 * mattered: a renderer that reads UVs off the mesh and falls back to `(0, 0)`
 * samples one texel for every triangle in the map, so each surface is a single
 * flat colour (and in the native client, whatever colour sits in the corner of
 * its tile — a concrete seam, which is why dust2's sandstone drew near black).
 *
 * So the UVs are derived here, from the world position, projected along the
 * dominant axis of the normal — the same projection the shader's grain uses —
 * and the texture is picked from the material's *name* by the shared rules in
 * `glb-surfaces.json`, which `apps/native-fps/src/textures3d.rs` reads too.
 * Pure: no three import, so it runs in the headless suite.
 */
import table from './glb-surfaces.json';

export type SurfaceKind =
  | 'none'
  | 'asphalt'
  | 'marble'
  | 'concrete'
  | 'vault_steel'
  | 'hazard'
  | 'wood'
  | 'crate'
  | 'container'
  | 'glass'
  | 'gold'
  | 'site_a'
  | 'site_b'
  | 'masonry'
  | 'plaster'
  | 'cobblestone'
  | 'roof_tile'
  | 'carpet'
  | 'brick'
  | PhotoKind;

/**
 * Kinds backed by photographed CC0 maps (colour, normal, roughness) rather than a
 * generated tile. Matched only by a map's own infixed material names (`dust2_`,
 * `mirage_`, `inferno_`, `nuke_`, `office_`, `bank_`, `assault_`), so no other
 * map changes. See
 * `tools/hassault_process_textures.py`.
 */
export type PhotoKind =
  | 'limewash'
  | 'sandstone'
  | 'paving'
  | 'dunesand'
  | 'cedar'
  | 'souk_crate'
  | 'rust_iron'
  | 'burlap'
  | 'glaze'
  | 'cast_concrete'
  | 'slab_concrete'
  | 'corrugated'
  | 'painted_metal'
  | 'tread_plate'
  | 'yard_asphalt'
  | 'carpet_tile'
  | 'ceramic_tile'
  | 'polished_marble'
  | 'red_brick'
  | 'wall_plaster';

interface SurfaceTable {
  default: SurfaceKind;
  rules: { kind: SurfaceKind; match: string[] }[];
  tileScale: Record<string, number>;
  detailMean: number;
  detailGain: number;
  photoKinds: PhotoKind[];
  photoNormalScale: Record<PhotoKind, number>;
}

const TABLE = table as SurfaceTable;

export const PHOTO_KINDS: readonly PhotoKind[] = TABLE.photoKinds;

export function isPhotoKind(kind: string): kind is PhotoKind {
  return (PHOTO_KINDS as readonly string[]).includes(kind);
}

/**
 * The generated tile a photo kind stands in with: on the Low tier, which never
 * fetches the images, and for the moment before they arrive.
 */
export const PHOTO_FALLBACK: Record<PhotoKind, SurfaceKind> = {
  limewash: 'plaster',
  sandstone: 'masonry',
  paving: 'cobblestone',
  dunesand: 'plaster',
  cedar: 'wood',
  souk_crate: 'crate',
  rust_iron: 'vault_steel',
  burlap: 'carpet',
  glaze: 'marble',
  cast_concrete: 'concrete',
  slab_concrete: 'concrete',
  corrugated: 'container',
  painted_metal: 'vault_steel',
  tread_plate: 'vault_steel',
  yard_asphalt: 'asphalt',
  carpet_tile: 'carpet',
  ceramic_tile: 'marble',
  polished_marble: 'marble',
  red_brick: 'brick',
  wall_plaster: 'plaster',
};

/** Where a photo kind's map lives, relative to the web root. `c` albedo, `n` normal, `r` roughness. */
export function photoTexturePath(kind: PhotoKind, channel: 'c' | 'n' | 'r'): string {
  return `/maps/textures/${kind}_${channel}.webp`;
}

/**
 * How hard each kind's normal map bites (`photoNormalScale` in the JSON, so the
 * native client bakes the same numbers into its detail layers). Wood, rust and
 * cloth have shallow relief and want more; stucco read leathery at 1.
 */
export const PHOTO_NORMAL_SCALE: Record<PhotoKind, number> = TABLE.photoNormalScale;

/** Linear mean every detail tile is normalised to. */
export const DETAIL_MEAN = TABLE.detailMean;
/**
 * What the albedo is multiplied by alongside the tile. The native shader
 * hardcodes this as `* 1.5`; `detailMean * detailGain` ≈ 1 is what keeps a
 * surface's average at its authored colour.
 */
export const DETAIL_GAIN = TABLE.detailGain;

/** First rule whose substring appears in the lowercased material name. */
export function classifySurface(materialName: string): SurfaceKind {
  const lower = materialName.toLowerCase();
  for (const rule of TABLE.rules) {
    if (rule.match.some((m) => lower.includes(m))) return rule.kind;
  }
  return TABLE.default;
}

/** World units per repeat of a kind's tile. */
export function surfaceTileScale(kind: SurfaceKind): number {
  return TABLE.tileScale[kind] ?? 4;
}

/**
 * Planar UV for a point on a surface, in render space (x east, y up, z north).
 * Floors take `xz`, walls facing ±x take `zy`, the rest `xy` — ties broken the
 * way the shader's `detail_uv` breaks them, so grain and texture agree on which
 * face a vertex belongs to.
 */
export function planarUv(
  px: number,
  py: number,
  pz: number,
  nx: number,
  ny: number,
  nz: number,
  scale: number,
): [number, number] {
  const ax = Math.abs(nx);
  const ay = Math.abs(ny);
  const az = Math.abs(nz);
  if (ay >= ax && ay >= az) return [px / scale, pz / scale];
  if (ax >= az) return [pz / scale, py / scale];
  return [px / scale, py / scale];
}

/** The material fields `withoutTransmission` reads and writes. */
export interface TransmissiveLike {
  transmission: number;
  transparent: boolean;
  opacity: number;
  depthWrite: boolean;
}

/** The least a fully transmissive pane is drawn at: still glass you can see. */
export const GLASS_MIN_OPACITY = 0.25;

/**
 * Turn a `KHR_materials_transmission` pane into plain blended glass.
 *
 * three implements transmission by rendering **every opaque object a second
 * time**, into a multisampled half-float target with mips, each frame that any
 * transmissive mesh is in view — and that pass runs with tone mapping off, so
 * every opaque material swaps shader program twice a frame and three re-derives
 * its program parameters each time. Profiled on Bank at High, the program
 * lookups alone were a third of the frame's JavaScript. Six maps have such
 * panes; the refraction they buy is invisible at a window's thickness.
 *
 * Opacity falls with how transmissive the pane was, to `GLASS_MIN_OPACITY` for
 * full transmission. Still see-through: what a player can see through a window
 * is not a graphics setting, and this is not one either — it applies at every
 * tier. Depth writes off, so the glass never hides what is behind it from a
 * later draw.
 */
export function withoutTransmission(m: TransmissiveLike): boolean {
  if (!(m.transmission > 0)) return false;
  const t = Math.min(m.transmission, 1);
  m.opacity = Math.min(m.opacity, 1 - t * (1 - GLASS_MIN_OPACITY));
  m.transmission = 0;
  m.transparent = true;
  m.depthWrite = false;
  return true;
}
