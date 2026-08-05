import { describe, expect, it } from 'vitest';

import { layoutsModule } from '../../modules/layouts';
import { seedFromPreset } from '../presets';
import type { AreaNode, LayoutNode } from '../types';

const ALL_VIEWS = new Set([
  'database.console',
  'library.panel',
  'browser.view',
  'research.pageViewer',
  'search.panel',
  'repl.console',
  'observability.io',
  'agent.chat',
]);

function areasOf(node: LayoutNode): AreaNode[] {
  if (node.kind === 'area') return [node];
  return node.children.flatMap(areasOf);
}

function presetFor(id: string) {
  const preset = layoutsModule.frames?.find((f) => f.id === id);
  expect(preset, `preset ${id} is declared`).toBeDefined();
  return preset!;
}

describe('data ops frame preset', () => {
  const preset = presetFor('dataops');

  it('opens as the database agent', () => {
    expect(preset.agent).toBe('dba');
  });

  it('seeds the console/library split with the REPL active in the bottom dock', () => {
    const frame = seedFromPreset(preset, { knownViews: ALL_VIEWS });
    const views = areasOf(frame.center).flatMap((a) => a.tabs.map((t) => t.viewId));
    expect(views).toEqual(['database.console', 'library.panel']);

    expect(frame.docks.right.tools.map((t) => t.viewId)).toEqual(['agent.chat']);
    expect(frame.docks.right.visible).toBe(true);

    const bottom = frame.docks.bottom;
    expect(bottom.tools.map((t) => t.viewId)).toEqual(['repl.console', 'observability.io']);
    expect(bottom.visible).toBe(true);
    // activeTool is an instance id — it must resolve to the REPL, not the I/O feed.
    expect(bottom.tools.find((t) => t.instanceId === bottom.activeTool)?.viewId).toBe(
      'repl.console',
    );
  });
});

describe('web ops frame preset', () => {
  const preset = presetFor('webops');

  it('opens as the researcher agent', () => {
    expect(preset.agent).toBe('researcher');
  });

  it('seeds the browser beside the library and saved-page viewer', () => {
    const frame = seedFromPreset(preset, { knownViews: ALL_VIEWS });
    const views = areasOf(frame.center).flatMap((a) => a.tabs.map((t) => t.viewId));
    expect(views).toEqual(['browser.view', 'library.panel', 'research.pageViewer']);

    expect(frame.docks.left.tools.map((t) => t.viewId)).toEqual(['search.panel']);
    expect(frame.docks.left.size).toBe(300);
    expect(frame.docks.right.tools.map((t) => t.viewId)).toEqual(['agent.chat']);
    // The I/O feed is seeded but folded away — available, not in the way.
    expect(frame.docks.bottom.tools.map((t) => t.viewId)).toEqual(['observability.io']);
    expect(frame.docks.bottom.visible).toBe(false);
  });

  it('survives a disabled module: unknown views are skipped, not fatal', () => {
    const frame = seedFromPreset(preset, { knownViews: new Set(['browser.view', 'agent.chat']) });
    const views = areasOf(frame.center).flatMap((a) => a.tabs.map((t) => t.viewId));
    expect(views).toEqual(['browser.view']);
    // A dock whose every tool vanished must not render as an empty strip.
    expect(frame.docks.left.visible).toBe(false);
    expect(frame.docks.right.visible).toBe(true);
  });
});

describe('preset agent bindings', () => {
  it('names only agents the backend roster can resolve', () => {
    // A typo here is silent at runtime (unknown ids fall back to `main`), so the
    // built-in roster ids are asserted rather than trusted.
    const builtins = new Set(['main', 'coder', 'dba', 'researcher', 'intake']);
    for (const frame of layoutsModule.frames ?? []) {
      if (frame.agent) expect(builtins, `preset ${frame.id}`).toContain(frame.agent);
    }
  });
});
