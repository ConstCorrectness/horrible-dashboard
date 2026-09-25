import { describe, expect, it } from 'vitest';

import { evalsModule } from '../../modules/evals';
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
    'training.notebook',
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

    // The notebook is seeded with no params and falls back to the project you
    // were last in (`lastProjectId`), which is what lets a preset seed it at all.
    expect(views[0]).toEqual(['training.notebook']);
    // The pairing the whole layout exists for: a failing eval row names a case, and
    // the code that produced it is one pane up.
    expect(views[1]).toEqual(['evals.hub', 'llamacpp.server']);
    expect(views[2]).toEqual(['training.metrics', 'localtrack.workspace']);
  });

  it('docks one browser, not the same list twice', () => {
    const frame = seedFromPreset(preset, { knownViews: FINETUNE_VIEWS });
    // Explorer alone. `training.projects` used to be docked above it, and
    // Explorer's **Projects section is that same pane** — so the dock opened the
    // identical list twice, one over the other, in 280px. It is still reachable:
    // as that Explorer section, and as the notebook's own left region strip.
    expect(frame.docks.left.tools.map((t) => t.viewId)).toEqual(['explorer.home']);
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
    expect(areasOf(real.center).flatMap((a) => a.tabs).length).toBe(5);
    expect(areasOf(none.center).flatMap((a) => a.tabs).length).toBe(0);
  });
});

describe('evals frame preset', () => {
  const preset = (() => {
    const found = evalsModule.frames?.find((f) => f.id === 'evals');
    expect(found, 'preset evals is declared').toBeDefined();
    return found!;
  })();

  it('opens as the fine-tuning agent rather than one invented for this pane', () => {
    expect(preset.agent).toBe('trainer');
  });

  it('docks the two panes that are variables of the experiment', () => {
    // Not decoration: an enabled skill rides every turn and a connected MCP server
    // contributes a whole tool group, so both change the catalog under test — and
    // the run records which ones were on. The switches belong beside the results.
    expect(preset.frame.docks?.left?.tools).toEqual([
      'explorer.home',
      'mcp.servers',
      'skills.library',
    ]);
  });
});
