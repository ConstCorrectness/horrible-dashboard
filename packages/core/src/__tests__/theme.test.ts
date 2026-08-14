// @vitest-environment happy-dom
/**
 * The theme switch. Three of these pin behaviour that fails *silently* if it
 * regresses — a wrong theme still renders a complete-looking app, so nothing here
 * would ever throw.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { registry } from '../registry';
import { settingsModule } from '../modules/settings';
import { setSetting } from '../settings';
import {
  applyTheme,
  currentThemeId,
  DEFAULT_THEME,
  initTheme,
  isKnownTheme,
  readThemeTokens,
  THEMES,
  THEME_SETTING_KEY,
} from '../theme';

// setSetting persists through the API; the store updates optimistically first, so
// the assertions here don't need the request to succeed.
vi.mock('../api', () => ({
  apiGet: vi.fn().mockResolvedValue({ values: {} }),
  apiPut: vi.fn().mockResolvedValue({}),
  apiDelete: vi.fn().mockResolvedValue({}),
}));

describe('theme', () => {
  beforeEach(() => {
    delete document.documentElement.dataset.theme;
  });

  it('declares the default theme, so :root and the setting cannot disagree', () => {
    expect(isKnownTheme(DEFAULT_THEME)).toBe(true);
  });

  it('exposes every theme through the settings enum', () => {
    registry.register(settingsModule);
    const decl = registry.settings.find((s) => s.key === THEME_SETTING_KEY);
    // A theme missing from `enumValues` is unreachable from the settings page —
    // it exists, it just cannot be chosen.
    expect(decl?.enumValues).toEqual(THEMES.map((t) => t.id));
    expect(decl?.default).toBe(DEFAULT_THEME);
  });

  it('writes the theme id onto the document element', () => {
    applyTheme('studio');
    expect(document.documentElement.dataset.theme).toBe('studio');
  });

  it('falls back to the default rather than writing an unknown theme through', () => {
    // A layout restored from a machine that had a plugin theme installed, or a
    // theme we since removed: `data-theme` naming a block that does not exist
    // would leave the app on :root's values while Settings claimed otherwise.
    applyTheme('theme-from-a-plugin-we-do-not-have');
    expect(document.documentElement.dataset.theme).toBe(DEFAULT_THEME);
  });

  it('reapplies when the setting changes', async () => {
    initTheme();
    expect(document.documentElement.dataset.theme).toBe(DEFAULT_THEME);

    await setSetting(THEME_SETTING_KEY, 'studio');
    expect(currentThemeId()).toBe('studio');
    expect(document.documentElement.dataset.theme).toBe('studio');
  });

  it('reads tokens with the leading dashes stripped', () => {
    document.documentElement.style.setProperty('--accent', '#6ea8fe');
    expect(readThemeTokens(['accent'])).toEqual({ accent: '#6ea8fe' });
    document.documentElement.style.removeProperty('--accent');
  });
});
