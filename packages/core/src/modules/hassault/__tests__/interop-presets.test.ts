import { describe, expect, it } from 'vitest';

import { seedFromPreset } from '../../../layout/presets';
import type { AreaNode, LayoutNode, SplitNode } from '../../../layout/types';
import { hassaultModule } from '../index';

const HASSAULT_VIEWS = new Set((hassaultModule.panels ?? []).map((p) => p.id));

function areasOf(node: LayoutNode): AreaNode[] {
  if (node.kind === 'area') return [node];
  return node.children.flatMap(areasOf);
}

function splitsOf(node: LayoutNode): SplitNode[] {
  if (node.kind === 'split') {
    return [node, ...node.children.flatMap(splitsOf)];
  }
  return [];
}

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

describe('hAssault presets', () => {
  it('defines the two presets', () => {
    expect(hassaultModule.frames?.map((f) => f.id)).toEqual(['hassault_dev', 'hassault_play']);
  });

  it('hassault_dev puts the game beside the dev tools, with no docks', () => {
    const preset = hassaultModule.frames!.find((f) => f.id === 'hassault_dev')!;
    const frame = seedFromPreset(preset, { knownViews: HASSAULT_VIEWS });
    const centerViews = areasOf(frame.center).flatMap((a) => a.tabs.map((t) => t.viewId));
    expect(centerViews).toEqual(['hassault.play', 'hassault.studio']);
    const splits = splitsOf(frame.center);
    expect(splits).toHaveLength(1);
    expect(splits[0].sizes).toEqual([0.55, 0.45]);
    expect(frame.docks.right.tools).toEqual([]);
    expect(frame.docks.bottom.tools).toEqual([]);
  });

  it('hassault_play is the game alone', () => {
    const preset = hassaultModule.frames!.find((f) => f.id === 'hassault_play')!;
    const frame = seedFromPreset(preset, { knownViews: HASSAULT_VIEWS });
    const centerViews = areasOf(frame.center).flatMap((a) => a.tabs.map((t) => t.viewId));
    expect(centerViews).toEqual(['hassault.play']);
  });
});
