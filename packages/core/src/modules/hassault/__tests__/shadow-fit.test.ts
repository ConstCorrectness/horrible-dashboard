import * as THREE from 'three';
import { describe, expect, it } from 'vitest';

import { drawnBounds, shadowSphere } from '../shadow-fit';

describe('the sun shadow fit', () => {
  it('is the sphere round the box, as native fit', () => {
    // Native: centre = (min + max) / 2, radius = |max - min| / 2, at least 1.
    const s = shadowSphere([0, -3, 0], [246, 45, 258]);
    expect(s.center).toEqual([123, 21, 129]);
    expect(s.radius).toBeCloseTo(Math.hypot(246, 48, 258) / 2, 6);
    expect(shadowSphere([5, 5, 5], [5, 5, 5]).radius).toBe(1);
  });

  it('bounds only what is drawn, even while the map itself is hidden', () => {
    const root = new THREE.Group();
    // A map is hidden until its programs have compiled; its bounds still count.
    root.visible = false;
    const box = (size: number, x: number) => {
      const m = new THREE.Mesh(new THREE.BoxGeometry(size, size, size));
      m.position.x = x;
      root.add(m);
      return m;
    };
    box(2, 0);
    box(2, 10);
    // A collision-only or clip node: hidden, so the shadow is not spent on it.
    box(100, 500).visible = false;
    root.updateMatrixWorld(true);
    const b = drawnBounds(THREE, root);
    expect(b).toEqual({ min: [-1, -1, -1], max: [11, 1, 1] });
    expect(drawnBounds(THREE, new THREE.Group())).toBeNull();
  });
});
