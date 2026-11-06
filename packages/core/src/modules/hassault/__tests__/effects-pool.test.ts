/**
 * `EffectsPool` reuses what it retires rather than building and disposing a
 * material per particle: disposing the last user of a program makes three delete
 * it, and the next shot re-linked it on the frame it was fired.
 */
import { describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';

import { EffectsPool } from '../effects';

function tracers(scene: THREE.Scene): THREE.Line[] {
  return scene.children.filter((c): c is THREE.Line => (c as THREE.Line).isLine);
}

describe('EffectsPool', () => {
  it('reuses a retired effect instead of building a new one', () => {
    const scene = new THREE.Scene();
    const pool = new EffectsPool(THREE, scene);
    pool.shot([0, 0, 0], [[4, 0, 0]], 0xff0000, false);
    const first = tracers(scene)[0];
    expect(first).toBeDefined();

    pool.update(1);
    expect(pool.size).toBe(0);
    expect(scene.children).toHaveLength(0);

    pool.shot([0, 0, 0], [[0, 9, 0]], 0x00ff00, false);
    const second = tracers(scene)[0];
    expect(second).toBe(first);
    // Fresh state, not the faded leftovers of the first shot.
    expect((second.material as THREE.LineBasicMaterial).opacity).toBeCloseTo(0.8, 6);
    expect((second.material as THREE.LineBasicMaterial).color.getHex()).toBe(0x00ff00);
    expect(second.scale.z).toBeCloseTo(9, 6);
  });

  it('resets a reused object’s growth, so it does not start at the last one’s size', () => {
    const scene = new THREE.Scene();
    const pool = new EffectsPool(THREE, scene);
    pool.blast([0, 0, 0], 8, 'smoke');
    pool.update(0.4);
    const shell = scene.children[0];
    expect(shell.scale.x).toBeGreaterThan(4);
    pool.update(1);
    pool.blast([0, 0, 0], 8, 'smoke');
    expect(scene.children[0]).toBe(shell);
    expect(shell.scale.x).toBe(1);
  });

  it('disposes materials only when the pool itself goes', () => {
    const scene = new THREE.Scene();
    const pool = new EffectsPool(THREE, scene);
    pool.impactSpatter([0, 0, 0], [0, 0, 1], 'metal');
    const materials = scene.children.map((c) => (c as THREE.Mesh).material as THREE.Material);
    const spies = materials.map((m) => vi.spyOn(m, 'dispose'));
    pool.update(1);
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    pool.dispose();
    for (const spy of spies) expect(spy).toHaveBeenCalled();
  });
});
