import { describe, expect, it } from 'vitest';

import {
  blendCurl,
  clipFor,
  INSPECT_CLIPS,
  inspectClipFor,
  knifeArchetype,
  knifePropId,
  sampleInspect,
  type Curl,
} from '../inspects';
import goldenData from '../models/inspects.golden.json';

interface GoldenPoint {
  t: number;
  pos: [number, number, number];
  rot: [number, number, number];
  spin: number;
}

const golden = goldenData as unknown as Record<string, GoldenPoint[]>;
const TAU = Math.PI * 2;

describe('inspects sampler', () => {
  it('has a clip for every weapon and knife archetype', () => {
    for (const id of [
      'knife-tactical',
      'knife-karambit',
      'knife-butterfly',
      'knife-bayonet',
      'knife-skeleton',
      'knife-huntsman',
      'pistol',
      'assault',
      'fal',
      'shotgun',
      'sniper',
      'nade',
    ]) {
      const clip = INSPECT_CLIPS[id];
      expect(clip, id).toBeDefined();
      expect(clip.duration).toBeGreaterThan(0);
      expect(clip.keys[0].t).toBe(0);
      expect(clip.keys[clip.keys.length - 1].t).toBe(1);
    }
  });

  it('starts and ends every clip at rest, spin on a whole turn', () => {
    for (const id of Object.keys(INSPECT_CLIPS)) {
      for (const t of [0, 1]) {
        const s = sampleInspect(id, t);
        for (let i = 0; i < 3; i++) {
          expect(s.pos[i], `${id} @${t} pos[${i}]`).toBeCloseTo(0, 6);
          expect(s.rot[i], `${id} @${t} rot[${i}]`).toBeCloseTo(0, 6);
        }
        const turns = s.spin / TAU;
        expect(Math.abs(turns - Math.round(turns)), `${id} @${t} spin`).toBeLessThan(1e-4);
      }
    }
  });

  it('matches the reference sampler’s golden vectors', () => {
    for (const [id, points] of Object.entries(golden)) {
      for (const p of points) {
        const s = sampleInspect(id, p.t);
        for (let i = 0; i < 3; i++) {
          expect(Math.abs(s.pos[i] - p.pos[i]), `${id} @${p.t} pos[${i}]`).toBeLessThan(1e-4);
          expect(Math.abs(s.rot[i] - p.rot[i]), `${id} @${p.t} rot[${i}]`).toBeLessThan(1e-4);
        }
        expect(Math.abs(s.spin - p.spin), `${id} @${p.t} spin`).toBeLessThan(1e-4);
      }
    }
  });

  it('gives every weapon a choreography of its own', () => {
    // Sampled at the same moments, no two clips trace the same path — the
    // complaint was eleven variations of one lift-and-roll.
    const signature = (id: string) =>
      [0.2, 0.4, 0.6, 0.8].flatMap((t) => {
        const s = sampleInspect(id, t);
        return [...s.pos, ...s.rot, s.spin, ...s.support, ...s.primary];
      });
    const ids = Object.keys(INSPECT_CLIPS);
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = signature(ids[i]);
        const b = signature(ids[j]);
        const distance = Math.hypot(...a.map((v, k) => v - b[k]));
        expect(distance, `${ids[i]} vs ${ids[j]}`).toBeGreaterThan(0.5);
      }
    }
  });

  it('eases the fingers in from the grip rather than snapping them', () => {
    const grip: Curl = [0.9, 1, 1, 1, 1];
    // Just after the first key, which has no fingers, and the second, which
    // does: the curl should still be almost the grip's.
    const clip = clipFor('knife-bayonet');
    const t = clip.keys[1].t * 0.02;
    const early = blendCurl(grip, sampleInspect('knife-bayonet', t).primaryFingers);
    early.forEach((v, i) => expect(Math.abs(v - grip[i])).toBeLessThan(0.05));
  });

  it('swings the butterfly handles', () => {
    const s = sampleInspect('knife-butterfly', 0.28);
    expect(Math.abs(s.nodes.handle_bite[0])).toBeGreaterThan(2.5);
  });

  it('twirls the karambit about its ring and not about the hand', () => {
    const clip = clipFor('knife-karambit');
    expect(clip.spinPivot).toBe('spin_origin');
    expect(sampleInspect('knife-karambit', 0.48).spin).toBeCloseTo(2 * TAU, 3);
  });

  it('names knives from the skin id and the name together', () => {
    expect(knifeArchetype('k_1042', 'Karambit | Fade')).toBe('knife-karambit');
    expect(knifeArchetype('butterfly-marble')).toBe('knife-butterfly');
    expect(knifeArchetype('knife-lore')).toBe('knife-bayonet');
    expect(knifeArchetype('skeleton-crimson')).toBe('knife-skeleton');
    expect(knifeArchetype('', 'Huntsman Case Hardened')).toBe('knife-huntsman');
    expect(knifeArchetype('')).toBe('knife-tactical');
    expect(knifePropId('knife-tactical')).toBe('knife');
    expect(knifePropId('knife-karambit')).toBe('knife_karambit');
  });

  it('maps weapons to clips', () => {
    expect(inspectClipFor('knife', 'x', 'Karambit')).toBe('knife-karambit');
    expect(inspectClipFor('pistol')).toBe('pistol');
    expect(inspectClipFor('fal')).toBe('fal');
    expect(inspectClipFor('grenade-smoke')).toBe('nade');
    expect(inspectClipFor('railgun')).toBe('assault');
  });
});
