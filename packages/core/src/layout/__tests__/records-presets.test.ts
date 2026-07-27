import { describe, expect, it } from 'vitest';

import { recordsModule } from '../../modules/records';
import { seedFromPreset } from '../presets';
import type { AreaNode, LayoutNode } from '../types';

const VIEWS = new Set([
  'records.grid',
  'records.form',
  'records.board',
  'records.list',
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

describe('crm frame preset', () => {
  const preset = presetFor('crm');

  it('opens as the crm agent', () => {
    expect(preset.agent).toBe('crm');
  });

  it('seeds board + record + activity log, with the board active', () => {
    const frame = seedFromPreset(preset, { knownViews: VIEWS });
    const areas = areasOf(frame.center);
    expect(areas.flatMap((a) => a.tabs.map((t) => t.viewId))).toEqual([
      'records.board',
      'records.grid',
      'records.form',
      'records.grid',
    ]);
    expect(areas[0].tabs[areas[0].activeTab].viewId).toBe('records.board');

    // The activity log is pinned to its own schema — without the params it would
    // be a second view of whatever table the rail is on.
    const activityGrid = areas[areas.length - 1].tabs[0];
    expect(activityGrid.params).toEqual({ schemaId: 'activities' });
    // …and the pipeline grid must NOT be pinned (it follows the selection).
    expect(areas[0].tabs[1].params).toBeUndefined();

    expect(frame.docks.left.tools.map((t) => t.viewId)).toEqual(['records.list']);
    expect(frame.docks.right.tools.map((t) => t.viewId)).toEqual(['agent.chat']);
  });
});

describe('data entry frame preset', () => {
  const preset = presetFor('intake');

  it('opens as the intake agent', () => {
    expect(preset.agent).toBe('intake');
  });

  it('seeds the source document beside the form, half and half', () => {
    const frame = seedFromPreset(preset, { knownViews: VIEWS });
    const areas = areasOf(frame.center);
    expect(areas.flatMap((a) => a.tabs.map((t) => t.viewId))).toEqual([
      'research.pdfViewer',
      'browser.view',
      'records.form',
    ]);
    expect(frame.center.kind === 'split' && frame.center.sizes).toEqual([0.5, 0.5]);
    expect(frame.docks.right.tools.map((t) => t.viewId)).toEqual(['agent.chat']);
    expect(frame.docks.bottom.visible).toBe(false);
  });

  it('degrades to just the form when the research module is absent', () => {
    const frame = seedFromPreset(preset, {
      knownViews: new Set(['records.form', 'records.list']),
    });
    expect(areasOf(frame.center).flatMap((a) => a.tabs.map((t) => t.viewId))).toEqual([
      'records.form',
    ]);
  });
});
