import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import {
  drawAsphaltTile,
  drawMarbleTile,
  drawHazardTile,
  drawSiteDecalTile,
  createPBRMaterialLibrary,
  generateDetailNormalAndRoughness,
  glbSurfacePbrMaps,
} from '../textures3d';

describe('textures3d procedural engine', () => {
  it('generates valid asphalt tile data', () => {
    const data = drawAsphaltTile(128, 128);
    expect(data.length).toBe(128 * 128 * 4);
    // Check non-zero pixels
    let sum = 0;
    for (let i = 0; i < data.length; i += 4) {
      sum += data[i];
    }
    expect(sum).toBeGreaterThan(0);
  });

  it('generates valid marble tile data with distinct grout and veining', () => {
    const data = drawMarbleTile(128, 128);
    expect(data.length).toBe(128 * 128 * 4);
    const minVal = Math.min(...data.subarray(0, 1000));
    const maxVal = Math.max(...data.subarray(0, 1000));
    expect(maxVal).toBeGreaterThan(minVal);
  });

  it('generates hazard warning stripes with alternating bands', () => {
    const data = drawHazardTile(64, 64);
    expect(data.length).toBe(64 * 64 * 4);
  });

  it('generates bomb site spray decals for A and B', () => {
    const dataA = drawSiteDecalTile('A', 64, 64);
    const dataB = drawSiteDecalTile('B', 64, 64);
    expect(dataA.length).toBe(64 * 64 * 4);
    expect(dataB.length).toBe(64 * 64 * 4);
  });

  it('synthesizes wrap-around Sobel normal and micro-roughness maps for procedural tiles', () => {
    const raw = drawMarbleTile(64, 64);
    const { normalData, roughnessData } = generateDetailNormalAndRoughness(raw, 64, 64);
    expect(normalData.length).toBe(64 * 64 * 4);
    expect(roughnessData.length).toBe(64 * 64 * 4);

    const pbr = glbSurfacePbrMaps(THREE, 'masonry', 0.45);
    expect(pbr).not.toBeNull();
    expect(pbr?.normalMap).toBeDefined();
    expect(pbr?.roughnessMap).toBeDefined();
    expect(pbr?.spec.normalScale).toBeGreaterThan(0);
  });

  it('creates complete PBR material library with normal & roughness maps and disposes cleanly', () => {
    const lib = createPBRMaterialLibrary(THREE);
    expect(lib.materials.asphalt).toBeDefined();
    expect(lib.materials.asphalt.normalMap).toBeDefined();
    expect(lib.materials.asphalt.roughnessMap).toBeDefined();
    expect(lib.materials.marble).toBeDefined();
    expect(lib.materials.marble.normalMap).toBeDefined();
    expect(lib.materials.concrete).toBeDefined();
    expect(lib.materials.vault_steel).toBeDefined();
    expect(lib.materials.wood).toBeDefined();
    expect(lib.materials.glass).toBeDefined();
    expect(lib.materials.hazard).toBeDefined();
    expect(lib.materials.gold).toBeDefined();

    expect(() => lib.dispose()).not.toThrow();
  });
});
