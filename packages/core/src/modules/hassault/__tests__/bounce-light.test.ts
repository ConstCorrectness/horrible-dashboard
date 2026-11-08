/**
 * Baked bounce light (`bounce-light.ts`): the GLB's COLOR_1, decoded once at
 * load into irradiance and carried through the world's batching to the shader.
 *
 * The encoding and its coverage on the shipped maps are pinned in
 * `map-geometry.test.ts`; the shader side in `surface-breakup.test.ts`; the
 * native decode against this one in `browser_parity.rs`.
 */
import * as THREE from 'three';
import { describe, expect, it } from 'vitest';

import {
  BOUNCE_ATTRIBUTE,
  decodeBounce,
  installBounceAttribute,
  readBounce,
} from '../bounce-light';
import type { MapInfo } from '../api';
import { buildWorld3DFromGLTF } from '../world3d';

const SHIPPED = { attribute: 'COLOR_1', scale: 4, curve: 'square' };

describe('readBounce', () => {
  it('reads the encoding the bake writes', () => {
    expect(readBounce({ extras: { hassaultBounce: SHIPPED } })).toEqual({ scale: 4 });
  });

  it('draws nothing it cannot decode', () => {
    expect(readBounce(undefined)).toBeNull();
    expect(readBounce({ version: '2.0' })).toBeNull();
    for (const odd of [
      { ...SHIPPED, curve: 'linear' },
      { ...SHIPPED, attribute: 'COLOR_2' },
      { ...SHIPPED, scale: 0 },
      { ...SHIPPED, scale: '4' },
    ]) {
      expect(readBounce({ extras: { hassaultBounce: odd } })).toBeNull();
    }
  });
});

/** COLOR_1 as GLTFLoader hands it over: normalised bytes, VEC4. */
function color1(bytes: number[]): THREE.BufferAttribute {
  return new THREE.BufferAttribute(new Uint8Array(bytes), 4, true);
}

describe('installBounceAttribute', () => {
  it('decodes scale · c² from the normalised bytes, never the raw ones', () => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('color_1', color1([255, 128, 0, 255, 51, 51, 51, 255]));
    installBounceAttribute(THREE, g, { scale: 4 });
    expect(g.getAttribute('color_1')).toBeUndefined();
    const b = g.getAttribute(BOUNCE_ATTRIBUTE);
    expect(b.itemSize).toBe(3);
    expect(b.getX(0)).toBeCloseTo(4, 6);
    expect(b.getY(0)).toBeCloseTo(4 * (128 / 255) ** 2, 6);
    expect(b.getZ(0)).toBe(0);
    expect(b.getX(1)).toBeCloseTo(decodeBounce({ scale: 4 }, 0.2), 6);
  });

  it('drops COLOR_1 when the map declares no encoding', () => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('color_1', color1([255, 255, 255, 255]));
    installBounceAttribute(THREE, g, null);
    expect(g.getAttribute('color_1')).toBeUndefined();
    expect(g.getAttribute(BOUNCE_ATTRIBUTE)).toBeUndefined();
  });
});

describe('buildWorld3DFromGLTF', () => {
  /** A floor triangle at `x`, with one bounce byte for all three corners. */
  function floor(x: number, byte: number, material: THREE.Material): THREE.Mesh {
    const g = new THREE.BufferGeometry();
    g.setAttribute(
      'position',
      new THREE.BufferAttribute(new Float32Array([x, 0, 0, x + 1, 0, 0, x, 0, 1]), 3),
    );
    g.setAttribute(
      'normal',
      new THREE.BufferAttribute(new Float32Array([0, 1, 0, 0, 1, 0, 0, 1, 0]), 3),
    );
    g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(6), 2));
    g.setAttribute(
      'color_1',
      color1([byte, byte, byte, 255, byte, byte, byte, 255, byte, byte, byte, 255]),
    );
    const mesh = new THREE.Mesh(g, material);
    mesh.name = `Floor_${x}`;
    return mesh;
  }

  it('carries the decoded bounce through a merged batch, per vertex', () => {
    const material = new THREE.MeshStandardMaterial({ name: 'dust2_floor' });
    const scene = new THREE.Group();
    scene.add(floor(0, 255, material), floor(10, 0, material));
    const world = buildWorld3DFromGLTF(THREE, { name: 'hd_dust2' } as unknown as MapInfo, scene, {
      scale: 4,
    });
    const meshes: THREE.Mesh[] = [];
    world.scene.traverse((o) => {
      if (o instanceof THREE.Mesh) meshes.push(o);
    });
    expect(meshes).toHaveLength(1);
    const g = meshes[0]!.geometry as THREE.BufferGeometry;
    const pos = g.getAttribute('position');
    const bounce = g.getAttribute(BOUNCE_ATTRIBUTE);
    expect(bounce?.count).toBe(pos.count);
    // Each corner kept its own triangle's light: the merge welds on bounce too.
    for (let i = 0; i < pos.count; i++) {
      expect(bounce.getX(i)).toBeCloseTo(pos.getX(i) < 5 ? 4 : 0, 6);
    }
    world.dispose();
  });
});
