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
  | 'brick';

interface SurfaceTable {
  default: SurfaceKind;
  rules: { kind: SurfaceKind; match: string[] }[];
  tileScale: Record<string, number>;
  detailMean: number;
  detailGain: number;
}

const TABLE = table as SurfaceTable;

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
