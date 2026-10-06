/**
 * Photographed surfaces for a modelled map: colour, normal and roughness maps on
 * the materials whose kind is a photo kind (see `glb-surfaces.ts`).
 *
 * `applyGlbSurface` (world3d.ts) has already given every such material its
 * generated stand-in tile, so the map is complete and correct the moment it
 * loads. This replaces the stand-in once the images arrive, and is never called
 * on the Low tier, which swaps to Lambert (no normal or roughness there) and
 * would pay the bandwidth for nothing.
 *
 * The albedo is normalised the way the generated tiles are (mean `detailMean` in
 * linear light, so `DETAIL_GAIN` brings a surface back to its authored colour), so
 * it replaces the tile one for one and the colour the map authored is untouched.
 *
 * Takes the three namespace as an argument, like `surface-quality.ts`, so this
 * file never pulls three into the bundle.
 */
import type * as THREE from 'three';

import {
  classifySurface,
  isPhotoKind,
  PHOTO_NORMAL_SCALE,
  photoTexturePath,
  type PhotoKind,
} from './glb-surfaces';
import { getCachedAssetUrl } from './models/assetCache';

export interface PhotoMaps {
  map: THREE.Texture;
  normalMap: THREE.Texture;
  roughnessMap: THREE.Texture;
}

export type TextureFetch = (path: string) => Promise<THREE.Texture>;

const loads = new WeakMap<typeof THREE, Map<PhotoKind, Promise<PhotoMaps>>>();
const upgraded = new WeakSet<THREE.Material>();

function defaultFetch(three: typeof THREE): TextureFetch {
  const loader = new three.TextureLoader();
  return async (path) => loader.loadAsync(await getCachedAssetUrl(path));
}

/** A kind's three maps, fetched once per three instance. A failed load is retried next call. */
export function loadPhotoMaps(
  three: typeof THREE,
  kind: PhotoKind,
  fetchTexture: TextureFetch = defaultFetch(three),
): Promise<PhotoMaps> {
  let byKind = loads.get(three);
  if (!byKind) {
    byKind = new Map();
    loads.set(three, byKind);
  }
  const existing = byKind.get(kind);
  if (existing) return existing;
  const task = (async (): Promise<PhotoMaps> => {
    const [map, normalMap, roughnessMap] = await Promise.all([
      fetchTexture(photoTexturePath(kind, 'c')),
      fetchTexture(photoTexturePath(kind, 'n')),
      fetchTexture(photoTexturePath(kind, 'r')),
    ]);
    for (const t of [map, normalMap, roughnessMap]) {
      t.wrapS = t.wrapT = three.RepeatWrapping;
      t.generateMipmaps = true;
      t.minFilter = three.LinearMipmapLinearFilter;
    }
    map.colorSpace = three.SRGBColorSpace;
    // Data, not colour: decoding these as sRGB would brighten every normal.
    normalMap.colorSpace = three.NoColorSpace;
    roughnessMap.colorSpace = three.NoColorSpace;
    return { map, normalMap, roughnessMap };
  })();
  byKind.set(kind, task);
  // A failed fetch must not poison the cache: the next tier change tries again.
  task.catch(() => byKind!.delete(kind));
  return task;
}

/** Put a kind's maps on a material, in place of its stand-in tile. */
export function applyPhotoMaps(
  m: THREE.MeshStandardMaterial,
  kind: PhotoKind,
  maps: PhotoMaps,
  anisotropy: number,
): void {
  for (const t of [maps.map, maps.normalMap, maps.roughnessMap]) {
    if (t.anisotropy !== anisotropy) {
      t.anisotropy = anisotropy;
      t.needsUpdate = true;
    }
  }
  m.map = maps.map;
  m.normalMap = maps.normalMap;
  m.normalScale.set(PHOTO_NORMAL_SCALE[kind], PHOTO_NORMAL_SCALE[kind]);
  m.roughnessMap = maps.roughnessMap;
  // three multiplies the map's green channel by `roughness`: 1 is "the map says".
  m.roughness = 1;
  m.needsUpdate = true;
}

/**
 * Upgrade every photo-kind material under `root`. Resolves with how many
 * materials changed; a kind whose images fail to load keeps its stand-in tile.
 */
export async function upgradePhotoSurfaces(
  three: typeof THREE,
  root: THREE.Object3D | null,
  anisotropy: number,
  fetchTexture?: TextureFetch,
): Promise<number> {
  if (!root) return 0;
  const wanted = new Map<PhotoKind, THREE.MeshStandardMaterial[]>();
  root.traverse((child) => {
    const mesh = child as THREE.Mesh;
    if (!mesh.isMesh) return;
    const list = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const m of list) {
      const std = m as THREE.MeshStandardMaterial;
      if (!std || !std.isMeshStandardMaterial || upgraded.has(std)) continue;
      const kind = classifySurface(std.name || '');
      if (!isPhotoKind(kind)) continue;
      const bucket = wanted.get(kind) ?? [];
      if (!bucket.includes(std)) bucket.push(std);
      wanted.set(kind, bucket);
    }
  });
  let changed = 0;
  await Promise.all(
    [...wanted].map(async ([kind, materials]) => {
      try {
        const maps = await loadPhotoMaps(three, kind, fetchTexture);
        for (const m of materials) {
          applyPhotoMaps(m, kind, maps, anisotropy);
          upgraded.add(m);
          changed++;
        }
      } catch (e) {
        console.warn(`hassault: ${kind} photo maps failed to load, keeping the generated tile`, e);
      }
    }),
  );
  return changed;
}
