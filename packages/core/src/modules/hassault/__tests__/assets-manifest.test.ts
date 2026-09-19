import { describe, expect, it } from 'vitest';
import manifest from '../assets.manifest.json';
import { getCachedAssetUrl } from '../models/assetCache';

describe('HorribleAssault Assets Manifest', () => {
  it('has valid manifest version and baseUrl', () => {
    expect(manifest.version).toBe(3);
    expect(manifest.baseUrl).toMatch(/^https:\/\//);
  });

  it('points baseUrl at the release tag matching its version', () => {
    // The hashes describe the files on one release. Bumping `version` without
    // moving the tag (or the reverse) fetches the other release's bytes, and both
    // sync paths only warn on a hash mismatch.
    expect(manifest.baseUrl).toMatch(new RegExp(`/releases/download/assets-v${manifest.version}$`));
  });

  it('declares all required weapon, operator and grenade assets', () => {
    const keys = Object.keys(manifest.assets);
    expect(keys).toContain('hassault-arms');
    expect(keys).toContain('hassault-operator');
    expect(keys).toContain('hassault-operator-t');
    expect(keys).toContain('hassault-clips');
    expect(keys).toContain('hassault-weapon-assault');
    expect(keys).toContain('hassault-weapon-fal');
    expect(keys).toContain('hassault-weapon-knife');
    expect(keys).toContain('hassault-weapon-knife-karambit');
    expect(keys).toContain('hassault-weapon-knife-butterfly');
    expect(keys).toContain('hassault-weapon-knife-bayonet');
    expect(keys).toContain('hassault-weapon-knife-skeleton');
    expect(keys).toContain('hassault-weapon-knife-huntsman');
    expect(keys).toContain('hassault-weapon-pistol');
    expect(keys).toContain('hassault-weapon-shotgun');
    expect(keys).toContain('hassault-weapon-sniper');
    expect(keys).toContain('hassault-grenade-he');
    expect(keys).toContain('hassault-grenade-flash');
    expect(keys).toContain('hassault-grenade-smoke');
  });

  it('contains valid 64-char hex sha256 checksums and positive sizes', () => {
    for (const [key, asset] of Object.entries(manifest.assets)) {
      expect(asset.filename, `${key} filename`).toMatch(/\.glb$/);
      expect(asset.sha256, `${key} sha256`).toMatch(/^[a-f0-9]{64}$/);
      // A grenade prop is ~27 KB; this floor only catches an empty or stub export.
      expect(asset.size, `${key} size`).toBeGreaterThan(10_000);
      expect(asset.destination, `${key} destination`).toMatch(/^apps\/web\/public\//);
    }
  });

  it('getCachedAssetUrl resolves url cleanly in node environment', async () => {
    const sample = '/hassault-weapon-fal.glb';
    const resolved = await getCachedAssetUrl(sample);
    expect(resolved).toBe(sample);
  });
});
