import { describe, expect, it } from 'vitest';

import { layoutsModule } from '../../modules/layouts';
import { trainingModule } from '../../modules/training';
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

describe('fine-tuning frame preset', () => {
  const FINETUNE_VIEWS = new Set([
    'evals.hub',
    'llamacpp.server',
    'training.metrics',
    'localtrack.workspace',
    'training.projects',
    'explorer.home',
    'agent.chat',
    'observability.io',
  ]);

  const preset = (() => {
    const found = trainingModule.frames?.find((f) => f.id === 'training');
    expect(found, 'preset training is declared').toBeDefined();
    return found!;
  })();

  it('opens as the fine-tuning agent', () => {
    expect(preset.agent).toBe('trainer');
  });

  it('puts the eval scoreboard under the document area, and the run beside it', () => {
    const frame = seedFromPreset(preset, { knownViews: FINETUNE_VIEWS });
    const areas = areasOf(frame.center);
    const views = areas.map((a) => a.tabs.map((t) => t.viewId));

    // The document area is empty on purpose: `training.notebook`/`training.recipe`
    // are params-bound and a preset's `tabs` carry none, so seeding them would open
    // two panes reading "No project". They arrive from the Projects pane instead.
    expect(views[0]).toEqual([]);
    // The pairing the whole layout exists for: a failing eval row names a case, and
    // the code that produced it is one pane up.
    expect(views[1]).toEqual(['evals.hub', 'llamacpp.server']);
    expect(views[2]).toEqual(['training.metrics', 'localtrack.workspace']);
  });

  it('docks the projects pane first — it is the entry point to the empty area', () => {
    const frame = seedFromPreset(preset, { knownViews: FINETUNE_VIEWS });
    expect(frame.docks.left.tools.map((t) => t.viewId)).toEqual([
      'training.projects',
      'explorer.home',
    ]);
    expect(frame.docks.right.tools.map((t) => t.viewId)).toEqual(['agent.chat']);
    // Present but closed: wanted during a fine-tune, not tailing under the charts.
    expect(frame.docks.bottom.tools.map((t) => t.viewId)).toEqual(['observability.io']);
    expect(frame.docks.bottom.visible).toBe(false);
  });

  it('names only views that exist, since an unknown one is skipped in silence', () => {
    // `seedFromPreset` drops a view it does not know without a word, so a typo in a
    // preset is a pane that simply never appears. Seeding against the real ids and
    // against none at all must differ — if they matched, every id would be wrong.
    const real = seedFromPreset(preset, { knownViews: FINETUNE_VIEWS });
    const none = seedFromPreset(preset, { knownViews: new Set<string>() });
    expect(areasOf(real.center).flatMap((a) => a.tabs).length).toBe(4);
    expect(areasOf(none.center).flatMap((a) => a.tabs).length).toBe(0);
  });
});
