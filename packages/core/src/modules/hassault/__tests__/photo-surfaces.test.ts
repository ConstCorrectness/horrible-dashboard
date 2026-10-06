/**
 * The photographed surfaces: which materials get them, that every image the table
 * promises exists, and how the loader dresses a material.
 *
 * The loader takes a fetch function, so none of this touches the network or a
 * canvas: a texture here is an empty `THREE.Texture`, which is all the loader
 * sets properties on.
 */
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import * as THREE from 'three';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  classifySurface,
  isPhotoKind,
  PHOTO_FALLBACK,
  PHOTO_KINDS,
  PHOTO_NORMAL_SCALE,
  photoTexturePath,
} from '../glb-surfaces';
import { applyPhotoMaps, loadPhotoMaps, upgradePhotoSurfaces } from '../photo-surfaces';
import { drawGlbSurfaceTile } from '../textures3d';
import vectors from './glb-surface-vectors.json';

const PUBLIC = fileURLToPath(new URL('../../../../../../apps/web/public', import.meta.url));

const fakeFetch = async () => new THREE.Texture();

function material(name: string): THREE.MeshStandardMaterial {
  const m = new THREE.MeshStandardMaterial({ name, roughness: 0.55 });
  return m;
}

function world(...names: string[]): THREE.Group {
  const root = new THREE.Group();
  for (const n of names) root.add(new THREE.Mesh(new THREE.BoxGeometry(), material(n)));
  return root;
}

afterEach(() => vi.restoreAllMocks());

describe('photo surface kinds', () => {
  it('are exactly the nine promised, each with a stand-in', () => {
    expect(PHOTO_KINDS).toHaveLength(9);
    for (const kind of PHOTO_KINDS) {
      expect(isPhotoKind(kind)).toBe(true);
      expect(PHOTO_FALLBACK[kind], kind).toBeTruthy();
      expect(PHOTO_NORMAL_SCALE[kind], kind).toBeGreaterThan(0);
      // The stand-in has to draw: it is what the Low tier shows.
      expect(drawGlbSurfaceTile(kind), kind).not.toBeNull();
    }
    expect(isPhotoKind('masonry')).toBe(false);
  });

  it('have all three images on disk', () => {
    for (const kind of PHOTO_KINDS) {
      for (const channel of ['c', 'n', 'r'] as const) {
        expect(
          existsSync(`${PUBLIC}${photoTexturePath(kind, channel)}`),
          `${kind} ${channel}`,
        ).toBe(true);
      }
    }
  });

  it("match Dust II's and Mirage's own materials and no other map's", () => {
    expect(classifySurface('mat_dust2_sandstone_ochre')).toBe('sandstone');
    expect(classifySurface('mat_dust2_sandstone_light')).toBe('limewash');
    expect(classifySurface('mat_dust2_limestone_paving')).toBe('paving');
    expect(classifySurface('mat_dust2_desert_sand')).toBe('dunesand');
    expect(classifySurface('mat_dust2_wood_cedar_weathered')).toBe('cedar');
    expect(classifySurface('mat_dust2_wood_crate')).toBe('souk_crate');
    expect(classifySurface('mat_dust2_metal_iron_rusted')).toBe('rust_iron');
    expect(classifySurface('mat_dust2_sack_burlap')).toBe('burlap');
    expect(classifySurface('mat_dust2_moorish_tile_gold')).toBe('glaze');
    // Mirage's own, by its own infix.
    expect(classifySurface('mat_mirage_sandstone_ochre')).toBe('sandstone');
    expect(classifySurface('mat_mirage_sandstone_light')).toBe('limewash');
    expect(classifySurface('mat_mirage_moorish_plaster_warm')).toBe('limewash');
    expect(classifySurface('mat_mirage_sandstone_paving')).toBe('paving');
    expect(classifySurface('mat_mirage_cedar_wood')).toBe('cedar');
    expect(classifySurface('mat_mirage_wood_crate')).toBe('souk_crate');
    expect(classifySurface('mat_mirage_canopy_indigo')).toBe('burlap');
    expect(classifySurface('mat_mirage_mosaic_tile_blue')).toBe('glaze');
    expect(isPhotoKind(classifySurface('mat_mirage_palm_fronds'))).toBe(false);
    // Mirage and the rest reuse these names, minus the infix, and must not change.
    for (const other of [
      'mat_sandstone_ochre',
      'mat_wood_crate',
      'mat_canopy_crimson',
      'mat_palm_bark',
    ]) {
      expect(isPhotoKind(classifySurface(other)), other).toBe(false);
    }
  });

  it('are given to no material in any other bundled map', () => {
    const rows = (vectors as { classify?: Array<{ name: string; kind: string }> }).classify ?? [];
    expect(rows.length).toBeGreaterThan(100);
    for (const row of rows) {
      if (isPhotoKind(row.kind)) {
        const lower = row.name.toLowerCase();
        expect(lower.includes('dust2_') || lower.includes('mirage_'), row.name).toBe(true);
      }
    }
  });
});

describe('loading and applying', () => {
  it('sets colour on the albedo only, and takes roughness from the map', async () => {
    const maps = await loadPhotoMaps(THREE, 'sandstone', fakeFetch);
    expect(maps.map.colorSpace).toBe(THREE.SRGBColorSpace);
    // Data, not colour: decoded as sRGB these would brighten every normal.
    expect(maps.normalMap.colorSpace).toBe(THREE.NoColorSpace);
    expect(maps.roughnessMap.colorSpace).toBe(THREE.NoColorSpace);
    for (const t of [maps.map, maps.normalMap, maps.roughnessMap]) {
      expect(t.wrapS).toBe(THREE.RepeatWrapping);
      expect(t.wrapT).toBe(THREE.RepeatWrapping);
    }
    const m = material('mat_dust2_sandstone_ochre');
    applyPhotoMaps(m, 'sandstone', maps, 8);
    expect(m.map).toBe(maps.map);
    expect(m.normalMap).toBe(maps.normalMap);
    expect(m.roughnessMap).toBe(maps.roughnessMap);
    // three multiplies the map by `roughness`: 1 means "the map says".
    expect(m.roughness).toBe(1);
    expect(m.normalScale.x).toBe(PHOTO_NORMAL_SCALE.sandstone);
    expect(maps.map.anisotropy).toBe(8);
  });

  it('fetches a kind once however many materials use it', async () => {
    const fetchTexture = vi.fn(fakeFetch);
    // Cedar: no other test loads it, and the loader caches per kind.
    const root = world('mat_dust2_wood_cedar_weathered', 'mat_dust2_palm_bark');
    expect(await upgradePhotoSurfaces(THREE, root, 4, fetchTexture)).toBe(2);
    // Three images for one kind, not six.
    expect(fetchTexture).toHaveBeenCalledTimes(3);
  });

  it('leaves other maps and the generated surfaces alone', async () => {
    const plain = material('mat_wood_crate');
    const root = new THREE.Group();
    root.add(new THREE.Mesh(new THREE.BoxGeometry(), plain));
    const fetchTexture = vi.fn(fakeFetch);
    expect(await upgradePhotoSurfaces(THREE, root, 4, fetchTexture)).toBe(0);
    expect(fetchTexture).not.toHaveBeenCalled();
    expect(plain.normalMap).toBeNull();
    expect(plain.roughness).toBe(0.55);
  });

  it('upgrades a material once', async () => {
    const root = world('mat_dust2_wood_crate');
    expect(await upgradePhotoSurfaces(THREE, root, 4, fakeFetch)).toBe(1);
    expect(await upgradePhotoSurfaces(THREE, root, 4, fakeFetch)).toBe(0);
  });

  it('keeps the generated tile when the images fail, and tries again later', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const root = world('mat_dust2_metal_iron_rusted');
    const m = (root.children[0] as THREE.Mesh).material as THREE.MeshStandardMaterial;
    const before = m.map;
    const failing = async () => {
      throw new Error('404');
    };
    expect(await upgradePhotoSurfaces(THREE, root, 4, failing)).toBe(0);
    expect(m.map).toBe(before);
    expect(m.normalMap).toBeNull();
    expect(m.roughness).toBe(0.55);
    // The failure was not cached.
    expect(await upgradePhotoSurfaces(THREE, root, 4, fakeFetch)).toBe(1);
  });
});
