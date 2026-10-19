/**
 * The pane manifest against the served settings schema.
 *
 * `settings-vectors.json` is `backend/modules/hassault/settings_schema.py`,
 * written out by `scripts/gen_hassault_settings_vectors.py`. Every key there that
 * is a value (not a document like the control map) must have a manifest row with
 * the same type and default, and the manifest must declare no `hassault.*` value
 * the schema does not know — so the Settings page cannot show a default the game
 * does not use, and account sync (which keys on the schema's `scope`) cannot miss
 * a row the page writes.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import { hassaultModule } from '../index';

interface SchemaRow {
  key: string;
  type: 'number' | 'boolean' | 'string';
  default: string | number | boolean;
  scope: 'account' | 'machine';
  min?: number;
  max?: number;
  enum?: string[];
  document?: boolean;
  aliases?: string[];
}

const schema = JSON.parse(
  readFileSync(new URL('./settings-vectors.json', import.meta.url), 'utf-8'),
) as { settings: SchemaRow[] };

const manifest = new Map((hassaultModule.settings ?? []).map((s) => [s.key, s]));

describe('settings schema', () => {
  it('has a manifest row for every value, with the same type and default', () => {
    for (const row of schema.settings) {
      if (row.document) continue;
      const decl = manifest.get(row.key);
      expect(decl, `${row.key} has no manifest row`).toBeDefined();
      expect(decl!.type, row.key).toBe(row.type);
      expect(decl!.default, row.key).toBe(row.default);
    }
  });

  it('declares no hassault row the schema does not know', () => {
    const known = new Set(schema.settings.map((r) => r.key));
    for (const key of manifest.keys()) {
      if (key.startsWith('hassault.'))
        expect(known.has(key), `${key} is not in the schema`).toBe(true);
    }
  });

  it('keeps the crosshair, aim and FOV on the account and the GPU on the machine', () => {
    const scope = new Map(schema.settings.map((r) => [r.key, r.scope]));
    expect(scope.get('hassault.crosshair.color')).toBe('account');
    expect(scope.get('hassault.video.fov')).toBe('account');
    expect(scope.get('hassault.sensitivity')).toBe('account');
    expect(scope.get('hassault.video.adapter')).toBe('machine');
    expect(scope.get('hassault.video.monitor')).toBe('machine');
    expect(scope.get('hassault.nativeBinaryPath')).toBe('machine');
  });
});
