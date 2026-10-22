/**
 * The settings page is a document pane: two columns (category list + settings)
 * that wrap every row onto three lines at a companion's width.
 */
import { beforeAll, describe, expect, it } from 'vitest';

import { settingsModule } from '../modules/settings';
import { registry } from '../registry';

describe('settings pane placement', () => {
  beforeAll(() => {
    registry.register(settingsModule);
  });

  it('opens as a single-instance document pane', () => {
    const decl = registry.panels.find((p) => p.id === 'settings.home');
    expect(decl?.role).toBe('document');
    expect(decl?.singleton).toBe(true);
  });
});
