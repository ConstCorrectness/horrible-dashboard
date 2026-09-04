import { describe, expect, it } from 'vitest';
import manifest from '../assets.manifest.json';
import { getCachedAssetUrl } from '../models/assetCache';

describe('HorribleAssault Assets Manifest', () => {
  it('has valid manifest version and baseUrl', () => {
    expect(manifest.version).toBe(1);
    expect(manifest.baseUrl).toMatch(/^https:\/\//);
  });

  it('declares all required weapon and operator assets', () => {
    const keys = Object.keys(manifest.assets);
    expect(keys).toContain('hassault-arms');
    expect(keys).toContain('hassault-operator');
    expect(keys).toContain('hassault-clips');
    expect(keys).toContain('hassault-weapon-assault');
    expect(keys).toContain('hassault-weapon-fal');
    expect(keys).toContain('hassault-weapon-pistol');
    expect(keys).toContain('hassault-weapon-shotgun');
    expect(keys).toContain('hassault-weapon-sniper');
  });

  it('contains valid 64-char hex sha256 checksums and positive sizes', () => {
    for (const [key, asset] of Object.entries(manifest.assets)) {
      expect(asset.filename, `${key} filename`).toMatch(/\.glb$/);
      expect(asset.sha256, `${key} sha256`).toMatch(/^[a-f0-9]{64}$/);
      expect(asset.size, `${key} size`).toBeGreaterThan(100_000);
      expect(asset.destination, `${key} destination`).toMatch(/^apps\/web\/public\//);
    }
  });

  it('getCachedAssetUrl resolves url cleanly in node environment', async () => {
    const sample = '/hassault-weapon-fal.glb';
    const resolved = await getCachedAssetUrl(sample);
    expect(resolved).toBe(sample);
  });
});
