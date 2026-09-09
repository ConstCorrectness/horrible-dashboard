import { describe, expect, it } from 'vitest';

import { seedFromPreset } from '../../../layout/presets';
import type { AreaNode, LayoutNode, SplitNode } from '../../../layout/types';
import { hassaultModule } from '../index';

const HASSAULT_VIEWS = new Set([
  'hassault.play',
  'hassault.console',
  'hassault.studio',
  'hassault.modelViewer',
  'hassault.modelEditor',
  'hassault.animEditor',
  'hassault.armory',
  'hassault.companion',
  'hassault.radar',
  'hassault.voice',
]);

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

describe('hAssault Module Panels & Registration', () => {
  it('registers all first-class dockable panels', () => {
    const panelIds = hassaultModule.panels?.map((p) => p.id) ?? [];
    expect(panelIds).toContain('hassault.play');
    expect(panelIds).toContain('hassault.console');
    expect(panelIds).toContain('hassault.studio');
    expect(panelIds).toContain('hassault.modelViewer');
    expect(panelIds).toContain('hassault.modelEditor');
    expect(panelIds).toContain('hassault.animEditor');
    expect(panelIds).toContain('hassault.armory');
    expect(panelIds).toContain('hassault.companion');
    expect(panelIds).toContain('hassault.radar');
    expect(panelIds).toContain('hassault.voice');
  });

  it('registers commands for cross-pane opening and actions', () => {
    const commandIds = hassaultModule.commands?.map((c) => c.id) ?? [];
    expect(commandIds).toContain('hassault.open');
    expect(commandIds).toContain('hassault.openStudio');
    expect(commandIds).toContain('hassault.openConsole');
    expect(commandIds).toContain('hassault.openArmory');
    expect(commandIds).toContain('hassault.openCompanion');
    expect(commandIds).toContain('hassault.openRadar');
    expect(commandIds).toContain('hassault.openVoice');
  });
});

describe('hAssault Workflow Presets (FramePreset)', () => {
  it('defines the four core interoperability presets', () => {
    const frameIds = hassaultModule.frames?.map((f) => f.id) ?? [];
    expect(frameIds).toContain('hassault_dev');
    expect(frameIds).toContain('hassault_mapmaker');
    expect(frameIds).toContain('hassault_armory_studio');
    expect(frameIds).toContain('hassault_play');
  });

  describe('hassault_dev: Game Dev & Live Testing Preset', () => {
    const preset = (hassaultModule.frames ?? []).find((f) => f.id === 'hassault_dev')!;

    it('materializes side-by-side center split of Play and 3D Studio', () => {
      const frame = seedFromPreset(preset, { knownViews: HASSAULT_VIEWS });
      const areas = areasOf(frame.center);
      const centerViews = areas.flatMap((a) => a.tabs.map((t) => t.viewId));
      expect(centerViews).toEqual(['hassault.play', 'hassault.studio']);

      const splits = splitsOf(frame.center);
      expect(splits).toHaveLength(1);
      expect(splits[0].orientation).toBe('row');
      expect(splits[0].sizes).toEqual([0.55, 0.45]);
    });

    it('docks Developer Console at bottom and Companion/Radar at right', () => {
      const frame = seedFromPreset(preset, { knownViews: HASSAULT_VIEWS });

      const bottom = frame.docks.bottom;
      expect(bottom.visible).toBe(true);
      expect(bottom.tools.map((t) => t.viewId)).toEqual(['hassault.console']);

      const right = frame.docks.right;
      expect(right.visible).toBe(true);
      expect(right.tools.map((t) => t.viewId)).toEqual(['hassault.companion', 'hassault.radar']);
    });
  });

  describe('hassault_mapmaker: Level Design & Arena Architect', () => {
    const preset = (hassaultModule.frames ?? []).find((f) => f.id === 'hassault_mapmaker')!;

    it('materializes studio focused with instant playtest split', () => {
      const frame = seedFromPreset(preset, { knownViews: HASSAULT_VIEWS });
      const areas = areasOf(frame.center);
      const centerViews = areas.flatMap((a) => a.tabs.map((t) => t.viewId));
      expect(centerViews).toEqual(['hassault.studio', 'hassault.play']);

      const splits = splitsOf(frame.center);
      expect(splits[0].orientation).toBe('row');
      expect(splits[0].sizes).toEqual([0.68, 0.32]);
    });

    it('docks Developer Console at bottom and Tactical Radar at right', () => {
      const frame = seedFromPreset(preset, { knownViews: HASSAULT_VIEWS });
      expect(frame.docks.bottom.tools.map((t) => t.viewId)).toEqual(['hassault.console']);
      expect(frame.docks.right.tools.map((t) => t.viewId)).toEqual([
        'hassault.radar',
        'hassault.companion',
      ]);
    });
  });

  describe('hassault_armory_studio: Armory & 3D Skin Studio', () => {
    const preset = (hassaultModule.frames ?? []).find((f) => f.id === 'hassault_armory_studio')!;

    it('materializes Armory and Model Studio side by side', () => {
      const frame = seedFromPreset(preset, { knownViews: HASSAULT_VIEWS });
      const areas = areasOf(frame.center);
      const centerViews = areas.flatMap((a) => a.tabs.map((t) => t.viewId));
      expect(centerViews).toEqual(['hassault.armory', 'hassault.studio']);
    });
  });

  describe('hassault_play: Focused Arena Match & Companion Sidecars', () => {
    const preset = (hassaultModule.frames ?? []).find((f) => f.id === 'hassault_play')!;

    it('materializes Fullscreen Arena with Companion, Radar, and Voice right dock', () => {
      const frame = seedFromPreset(preset, { knownViews: HASSAULT_VIEWS });
      const areas = areasOf(frame.center);
      const centerViews = areas.flatMap((a) => a.tabs.map((t) => t.viewId));
      expect(centerViews).toEqual(['hassault.play']);

      expect(frame.docks.right.visible).toBe(true);
      expect(frame.docks.right.tools.map((t) => t.viewId)).toEqual([
        'hassault.companion',
        'hassault.radar',
        'hassault.voice',
      ]);
    });
  });
});
