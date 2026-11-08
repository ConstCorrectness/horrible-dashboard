/**
 * The surface breakup and the water surface are `onBeforeCompile` patches, and
 * a patch whose `#include` anchor is missing does nothing at all — no error,
 * just a plain material. These run each patch over three's own shader sources
 * and check that every anchor was found and every piece landed, so a three
 * upgrade that renames a chunk fails here rather than quietly in the game.
 *
 * The numbers themselves are pinned against native in `browser_parity.rs`.
 */
import * as THREE from 'three';
import { describe, expect, it } from 'vitest';

import { BOUNCE_ATTRIBUTE } from '../bounce-light';
import {
  breakupFor,
  installLowTierBreakup,
  installSurfaceBreakup,
  setBreakupDetail,
} from '../surface-breakup';
import { createWater, WATER_CLOCK_WRAP } from '../water';
import type { World } from '../world';

interface Patched {
  vertexShader: string;
  fragmentShader: string;
  uniforms: Record<string, { value: unknown }>;
}

function compile(
  material: THREE.Material,
  lib: { vertexShader: string; fragmentShader: string },
): Patched {
  const shader = {
    vertexShader: lib.vertexShader,
    fragmentShader: lib.fragmentShader,
    uniforms: {},
  };
  material.onBeforeCompile(
    shader as unknown as THREE.WebGLProgramParametersWithUniforms,
    null as never,
  );
  return shader;
}

/**
 * The baked bounce (`bounce-light.ts`): read from the decoded attribute, and
 * added to `irradiance` after three's light-map chunk and before the chunk that
 * spends it (`lights_fragment_end`, `RE_IndirectDiffuse`).
 */
function expectBounce(s: Patched): void {
  expect(s.vertexShader).toContain(`attribute vec3 ${BOUNCE_ATTRIBUTE};`);
  expect(s.vertexShader).toContain(`vHdBounce = ${BOUNCE_ATTRIBUTE};`);
  const maps = s.fragmentShader.indexOf('#include <lights_fragment_maps>');
  const add = s.fragmentShader.indexOf('irradiance += vHdBounce;');
  const end = s.fragmentShader.indexOf('#include <lights_fragment_end>');
  expect(maps).toBeGreaterThan(0);
  expect(add).toBeGreaterThan(maps);
  expect(end).toBeGreaterThan(add);
}

describe('breakupFor', () => {
  it('leaves glass alone, never offsets a site letter, and breaks up the rest', () => {
    expect(breakupFor('glass')).toEqual({ antiTile: false, breakup: false });
    expect(breakupFor('site_a')).toEqual({ antiTile: false, breakup: true });
    expect(breakupFor('site_b')).toEqual({ antiTile: false, breakup: true });
    expect(breakupFor('concrete')).toEqual({ antiTile: true, breakup: true });
  });
});

describe('installSurfaceBreakup', () => {
  it('patches a standard material at every anchor', () => {
    const m = new THREE.MeshStandardMaterial();
    installSurfaceBreakup(THREE, m, 'concrete');
    expect(m.customProgramCacheKey()).toBe('hd-breakup-3-tile-tint-grime');
    const s = compile(m, THREE.ShaderLib.standard);
    expect(s.vertexShader).toContain('vHdWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;');
    expect(s.fragmentShader).toContain('#define HD_ANTI_TILE');
    expect(s.fragmentShader).toContain('#define HD_MACRO');
    expect(s.fragmentShader).toContain('#define HD_BREAKUP');
    expect(s.fragmentShader).toContain('hdTileW = smoothstep(');
    expect(s.fragmentShader).toContain('hdSurfaceBreakup(diffuseColor.rgb, vHdWorld, vColor.r)');
    // The anchors were consumed (each replaced chunk inlined instead)…
    for (const anchor of ['map_fragment', 'normal_fragment_maps', 'roughnessmap_fragment']) {
      expect(s.fragmentShader).not.toContain(`#include <${anchor}>`);
    }
    // …except the vertex colour, kept so the breakup runs on an albedo that
    // already carries its AO, as native's does.
    const color = s.fragmentShader.indexOf('#include <color_fragment>');
    expect(color).toBeGreaterThan(0);
    expect(s.fragmentShader.indexOf('hdSurfaceBreakup(diffuseColor.rgb')).toBeGreaterThan(color);
    // The normal and roughness maps sample at the same offsets as the colour.
    expect(s.fragmentShader).toContain('HD_SAMPLE( normalMap, vNormalMapUv )');
    expect(s.fragmentShader).toContain('HD_SAMPLE( roughnessMap, vRoughnessMapUv )');
    expect(s.fragmentShader).not.toContain('texture2D( normalMap, vNormalMapUv )');
    expectBounce(s);
  });

  it('drops to the grime at Medium and comes back at High', () => {
    const m = new THREE.MeshStandardMaterial();
    installSurfaceBreakup(THREE, m, 'concrete');
    setBreakupDetail(THREE, m, false);
    expect(m.customProgramCacheKey()).toBe('hd-breakup-3-grime');
    const s = compile(m, THREE.ShaderLib.standard);
    expect(s.fragmentShader).not.toContain('#define HD_ANTI_TILE');
    expect(s.fragmentShader).not.toContain('#define HD_MACRO');
    expect(s.fragmentShader).toContain('#define HD_BREAKUP');
    setBreakupDetail(THREE, m, true);
    expect(m.customProgramCacheKey()).toBe('hd-breakup-3-tile-tint-grime');
    // A material the breakup never touched stays untouched.
    const plain = new THREE.MeshStandardMaterial();
    const before = plain.customProgramCacheKey();
    setBreakupDetail(THREE, plain, false);
    expect(plain.customProgramCacheKey()).toBe(before);
  });

  it('weathers a site marking without offsetting it, and gives glass the bounce only', () => {
    const site = new THREE.MeshStandardMaterial();
    installSurfaceBreakup(THREE, site, 'site_a');
    expect(site.customProgramCacheKey()).toBe('hd-breakup-3-tint-grime');
    const s = compile(site, THREE.ShaderLib.standard);
    expect(s.fragmentShader).not.toContain('#define HD_ANTI_TILE');
    expect(s.fragmentShader).toContain('#define HD_BREAKUP');

    const glass = new THREE.MeshStandardMaterial();
    installSurfaceBreakup(THREE, glass, 'glass');
    expect(glass.customProgramCacheKey()).toBe('hd-breakup-3');
    const g = compile(glass, THREE.ShaderLib.standard);
    expect(g.fragmentShader).not.toContain('#define HD_ANTI_TILE');
    expect(g.fragmentShader).not.toContain('#define HD_BREAKUP');
    expectBounce(g);
  });

  it('gives a Low (Lambert) twin the grime only: the darkening is fair, the rest is detail', () => {
    const m = new THREE.MeshStandardMaterial();
    installSurfaceBreakup(THREE, m, 'plaster');
    const twin = new THREE.MeshLambertMaterial();
    installLowTierBreakup(THREE, twin, m);
    expect(twin.customProgramCacheKey()).toBe('hd-breakup-3-grime');
    const s = compile(twin, THREE.ShaderLib.lambert);
    expect(s.fragmentShader).toContain('hdSurfaceBreakup(diffuseColor.rgb, vHdWorld, vColor.r)');
    expect(s.fragmentShader).toContain('#define HD_BREAKUP');
    expect(s.fragmentShader).not.toContain('#define HD_ANTI_TILE');
    expect(s.fragmentShader).not.toContain('#define HD_MACRO');
    // Bounce is content: Low's Lambert gathers it like every other tier.
    expectBounce(s);

    // Glass has no grime, so its twin gets the bounce alone.
    const glass = new THREE.MeshStandardMaterial();
    installSurfaceBreakup(THREE, glass, 'glass');
    const glassTwin = new THREE.MeshLambertMaterial();
    installLowTierBreakup(THREE, glassTwin, glass);
    expect(glassTwin.customProgramCacheKey()).toBe('hd-breakup-3');
    expect(compile(glassTwin, THREE.ShaderLib.lambert).fragmentShader).not.toContain(
      '#define HD_BREAKUP',
    );
  });
});

describe('createWater', () => {
  const world = {
    ssize: 8,
    waterlevel: 2,
    info: { watercolor: [20, 60, 80, 160] },
    isSolid: () => false,
    floorAt: () => 0,
  } as unknown as World;
  const atmosphere = { skyZenith: 0x2e6cc7, skyHorizon: 0xd6dbe0, hemiGround: 0xb58c5c };

  it('reflects the map environment by fresnel on a moving normal', () => {
    const scene = new THREE.Scene();
    const water = createWater(THREE, scene, world, atmosphere);
    expect(water).not.toBeNull();
    const mesh = scene.children[0] as THREE.Mesh<THREE.BufferGeometry, THREE.MeshLambertMaterial>;
    expect(mesh.material.customProgramCacheKey()).toBe('hd-water-1');
    const s = compile(mesh.material, THREE.ShaderLib.lambert);
    for (const anchor of ['normal_fragment_maps', 'opaque_fragment']) {
      // Kept, with the water's code beside it.
      expect(s.fragmentShader).toContain(`#include <${anchor}>`);
    }
    expect(s.fragmentShader).toContain('vec2 hdSlope = hdWaveSlope(vHdWorld.xz, hdWaterTime);');
    expect(s.fragmentShader).toContain(
      'outgoingLight = mix(outgoingLight, hdEnvironment(reflect(-hdV, hdN)), hdF);',
    );
    expect(s.vertexShader).toContain('vHdWorld = (modelMatrix');

    // The served sky, linearised: sRGB 0xd6 is about 0.67 linear, not 0.84.
    const horizon = s.uniforms.hdSkyHorizon.value as THREE.Vector3;
    expect(horizon.x).toBeCloseTo(0.672, 2);

    // The clock wraps at 64 s, and never goes negative.
    const time = s.uniforms.hdWaterTime as { value: number };
    water!.update(WATER_CLOCK_WRAP + 3);
    expect(time.value).toBeCloseTo(3, 5);
    water!.update(-1);
    expect(time.value).toBeCloseTo(WATER_CLOCK_WRAP - 1, 5);
    water!.dispose();
  });
});
