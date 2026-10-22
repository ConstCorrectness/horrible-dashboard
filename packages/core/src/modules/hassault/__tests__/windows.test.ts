import { describe, expect, it } from 'vitest';

import { hassaultModule } from '../index';

const HASSAULT_VIEWS = new Set((hassaultModule.panels ?? []).map((p) => p.id));

const panels = hassaultModule.panels ?? [];
const panel = (id: string) => panels.find((p) => p.id === id);

describe('hAssault windows', () => {
  it('launches exactly three windows: game, dev tools, server', () => {
    const launchable = panels.filter((p) => !p.embedded).map((p) => p.id);
    expect(launchable).toEqual(['hassault.play', 'hassault.studio', 'hassault.server']);
  });

  it('keeps the companions as strips, never as windows of their own', () => {
    for (const id of [
      'hassault.console',
      'hassault.companion',
      'hassault.radar',
      'hassault.voice',
    ]) {
      expect(panel(id)?.embedded, id).toBe(true);
    }
    const playStrips = panel('hassault.play')?.regions?.map((r) => r.id);
    expect(playStrips).toEqual([
      'hassault.companion',
      'hassault.radar',
      'hassault.voice',
      'hassault.console',
    ]);
    expect(panel('hassault.studio')?.regions?.map((r) => r.id)).toContain('hassault.console');
  });

  it('every strip names a registered view', () => {
    for (const host of panels) {
      for (const r of host.regions ?? []) expect(HASSAULT_VIEWS.has(r.id), r.id).toBe(true);
    }
  });

  it('keeps the retired openers as commands', () => {
    const commandIds = hassaultModule.commands?.map((c) => c.id) ?? [];
    for (const id of [
      'hassault.open',
      'hassault.openServer',
      'hassault.openStudio',
      'hassault.openModelViewer',
      'hassault.openModelEditor',
      'hassault.openAnimEditor',
      'hassault.openConsole',
      'hassault.openCompanion',
      'hassault.openRadar',
      'hassault.openVoice',
    ]) {
      expect(commandIds).toContain(id);
    }
  });
});
