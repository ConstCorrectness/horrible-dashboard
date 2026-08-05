import { describe, expect, it } from 'vitest';

import { recordsModule } from '../../modules/records';
import { seedFromPreset } from '../presets';
import type { AreaNode, LayoutNode } from '../types';

const VIEWS = new Set([
  'records.grid',
  'records.form',
  'records.board',
  'explorer.home',
  'research.pdfViewer',
  'browser.view',
  'agent.chat',
  'observability.io',
]);

function areasOf(node: LayoutNode): AreaNode[] {
  if (node.kind === 'area') return [node];
  return node.children.flatMap(areasOf);
}

function presetFor(id: string) {
  const preset = recordsModule.frames?.find((f) => f.id === id);
  expect(preset, `preset ${id} is declared`).toBeDefined();
  return preset!;
}

describe('records frame presets', () => {
  // The `crm` preset is deliberately gone: a contacts/deals pipeline presumed a
  // sales workflow the substrate never required, and it was the reason a generic
  // table store read as CRM software. The review surface lives in the workspaces
  // where the reading happens instead — see the layouts module's own test.
  it('contributes Data Entry and nothing else', () => {
    expect(recordsModule.frames?.map((f) => f.id)).toEqual(['intake']);
  });
});

describe('data entry frame preset', () => {
  const preset = presetFor('intake');

  it('opens as the intake agent', () => {
    expect(preset.agent).toBe('intake');
  });

  it('seeds the source document beside the review surface, half and half', () => {
    const frame = seedFromPreset(preset, { knownViews: VIEWS });
    const areas = areasOf(frame.center);
    expect(areas.flatMap((a) => a.tabs.map((t) => t.viewId))).toEqual([
      'research.pdfViewer',
      'browser.view',
      'records.form',
      'records.grid',
    ]);
    // Review is what this workspace is for; the rows sit behind it as a tab.
    const right = areas[areas.length - 1];
    expect(right.tabs[right.activeTab].viewId).toBe('records.form');
    expect(frame.center.kind === 'split' && frame.center.sizes).toEqual([0.5, 0.5]);
    expect(frame.docks.right.tools.map((t) => t.viewId)).toEqual(['agent.chat']);
    expect(frame.docks.bottom.visible).toBe(false);
  });

  it('degrades to just the form when the research module is absent', () => {
    const frame = seedFromPreset(preset, {
      knownViews: new Set(['records.form', 'explorer.home']),
    });
    expect(areasOf(frame.center).flatMap((a) => a.tabs.map((t) => t.viewId))).toEqual([
      'records.form',
    ]);
  });
});
