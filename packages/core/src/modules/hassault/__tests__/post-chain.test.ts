/**
 * The browser's post chain (`post-chain.ts`): when it runs, and how big its
 * bloom levels are. The shader numbers themselves are pinned against the
 * native client's by `apps/native-fps/tests/browser_parity.rs`.
 */
import * as THREE from 'three';
import { describe, expect, it } from 'vitest';

import {
  aces,
  bloomShare,
  chainActive,
  inverseAces,
  LEVELS,
  mipSizes,
  sceneColoursForChain,
} from '../post-chain';

describe('the post chain', () => {
  it('is skipped entirely while bloom and sharpening are both off', () => {
    expect(chainActive({ bloom: 0, sharpen: 0 })).toBe(false);
    expect(chainActive({ bloom: 0.5, sharpen: 0 })).toBe(true);
    expect(chainActive({ bloom: 0, sharpen: 0.3 })).toBe(true);
  });

  it('builds the bloom chain from half resolution down, never below a pixel', () => {
    const sizes = mipSizes(1920, 1080);
    expect(sizes).toHaveLength(LEVELS + 1);
    expect(sizes[0]).toEqual([960, 540]);
    expect(sizes[1]).toEqual([480, 270]);
    expect(sizes.at(-1)).toEqual([30, 16]);
    expect(mipSizes(3, 3).every(([w, h]) => w >= 1 && h >= 1)).toBe(true);
  });

  it('blooms what is brighter than white, not a sunlit wall', () => {
    // Linear, exposed light. A sunlit Dust II floor is about 0.95: in the
    // knee's toe, a fraction of a percent, which rounds to nothing on screen.
    expect(bloomShare(0.95)).toBeLessThan(0.002);
    expect(bloomShare(0.5)).toBe(0);
    // A lamp's emissive (strength 6 on Bank's sconces) keeps most of itself.
    expect(bloomShare(6)).toBeGreaterThan(0.75);
    // And more light glows more: the share rises with the luminance.
    expect(bloomShare(3)).toBeLessThan(bloomShare(6));
  });

  it('pre-distorts the clear colour so tone mapping gives the canvas colour back', () => {
    // three never tone maps the background on the canvas; the chain tone maps
    // its whole target. Verified pixel-exact in a browser: sky and wall match.
    for (const sky of [
      [0.32, 0.45, 0.62],
      [0.05, 0.06, 0.08],
      [0.8, 0.7, 0.55],
    ] as [number, number, number][]) {
      for (const exposure of [0.9, 1.15, 1.6]) {
        const back = aces(inverseAces(sky, exposure), exposure);
        for (let i = 0; i < 3; i += 1) expect(back[i]).toBeCloseTo(sky[i], 4);
      }
    }
  });

  it('pre-distorts the fog colour too, and puts both back after the render', () => {
    // Inside the chain's target three mixes fog in linear light, before the
    // curve: unswapped, a fully fogged wall would come back as the curve's
    // lift of the fog colour, not the map's hex. Native does the same
    // (`display_hex_to_scene`), so both clients fog in one order.
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x9cbde8);
    scene.fog = new THREE.FogExp2(0x2a3040, 0.02);
    const sky = scene.background.clone();
    const fog = scene.fog.color.clone();

    const restore = sceneColoursForChain(THREE, scene, 1.15);
    const inChain = (scene.fog as THREE.FogExp2).color;
    const back = aces([inChain.r, inChain.g, inChain.b], 1.15);
    for (const [i, c] of [fog.r, fog.g, fog.b].entries()) expect(back[i]).toBeCloseTo(c, 4);
    expect((scene.background as THREE.Color).equals(sky)).toBe(false);

    restore();
    expect((scene.background as THREE.Color).equals(sky)).toBe(true);
    expect((scene.fog as THREE.FogExp2).color.equals(fog)).toBe(true);
  });

  it('leaves a scene with no fog and a textured background alone', () => {
    const scene = new THREE.Scene();
    const texture = new THREE.Texture();
    scene.background = texture;
    sceneColoursForChain(THREE, scene, 1.15)();
    expect(scene.background).toBe(texture);
    expect(scene.fog).toBeNull();
  });
});
