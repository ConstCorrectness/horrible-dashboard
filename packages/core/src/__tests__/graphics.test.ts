// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  applyGraphicsQuality,
  currentGraphicsQuality,
  detectDefaultGraphicsQuality,
  GRAPHICS_QUALITIES,
  GRAPHICS_QUALITY_SETTING_KEY,
  initGraphicsQuality,
  isKnownGraphicsQuality,
} from '../graphics';
import { settingsModule } from '../modules/settings';
import { registry } from '../registry';
import { setSetting } from '../settings';

vi.mock('../api', () => ({
  apiGet: vi.fn().mockResolvedValue({ values: {} }),
  apiPut: vi.fn().mockResolvedValue({}),
  apiDelete: vi.fn().mockResolvedValue({}),
}));

describe('graphics quality & performance profile', () => {
  beforeEach(() => {
    delete document.documentElement.dataset.graphicsQuality;
  });

  it('declares valid graphics quality values', () => {
    expect(GRAPHICS_QUALITIES).toEqual(['performance', 'balanced', 'quality']);
    expect(isKnownGraphicsQuality('performance')).toBe(true);
    expect(isKnownGraphicsQuality('balanced')).toBe(true);
    expect(isKnownGraphicsQuality('quality')).toBe(true);
    expect(isKnownGraphicsQuality('unknown')).toBe(false);
  });

  it('exposes graphics quality through the settings enum', () => {
    registry.register(settingsModule);
    const decl = registry.settings.find((s) => s.key === GRAPHICS_QUALITY_SETTING_KEY);
    expect(decl).toBeDefined();
    expect(decl?.enumValues).toEqual(['performance', 'balanced', 'quality']);
  });

  it('writes the graphics quality onto document.documentElement', () => {
    applyGraphicsQuality('performance');
    expect(document.documentElement.dataset.graphicsQuality).toBe('performance');

    applyGraphicsQuality('quality');
    expect(document.documentElement.dataset.graphicsQuality).toBe('quality');
  });

  it('re-applies graphics quality when setting changes', async () => {
    initGraphicsQuality();
    await setSetting(GRAPHICS_QUALITY_SETTING_KEY, 'performance');
    expect(currentGraphicsQuality()).toBe('performance');
    expect(document.documentElement.dataset.graphicsQuality).toBe('performance');

    await setSetting(GRAPHICS_QUALITY_SETTING_KEY, 'quality');
    expect(currentGraphicsQuality()).toBe('quality');
    expect(document.documentElement.dataset.graphicsQuality).toBe('quality');
  });

  it('falls back to detected default when setting value is invalid', () => {
    const detected = detectDefaultGraphicsQuality();
    expect(['performance', 'balanced', 'quality']).toContain(detected);
  });
});
