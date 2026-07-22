import { describe, expect, it } from 'vitest';

import { layoutsModule } from '../../modules/layouts';
import { seedFromPreset } from '../presets';
import type { AreaNode, LayoutNode } from '../types';

const RESEARCH_VIEWS = new Set([
  'research.console',
  'research.arxiv',
  'research.pdfViewer',
  'research.pageViewer',
  'browser.view',
  'library.panel',
  'agent.chat',
  'observability.io',
]);

function areasOf(node: LayoutNode): AreaNode[] {
  if (node.kind === 'area') return [node];
  return node.children.flatMap(areasOf);
}

describe('research frame preset', () => {
  const preset = layoutsModule.frames?.find((f) => f.id === 'research');

  it('is declared in the layouts module tab strip', () => {
    expect(preset).toBeDefined();
    expect(preset?.name).toBe('Research');
  });

  it('seeds the discovery/reading split with the expected panes', () => {
    const frame = seedFromPreset(preset!, { knownViews: RESEARCH_VIEWS });
    const views = areasOf(frame.center).flatMap((a) => a.tabs.map((t) => t.viewId));
    expect(views).toEqual([
      'research.console',
      'research.arxiv',
      'browser.view',
      'research.pdfViewer',
      'research.pageViewer',
      'library.panel',
    ]);
    // Console is the active tab of the discovery area.
    const discovery = areasOf(frame.center)[0];
    expect(discovery.tabs[discovery.activeTab].viewId).toBe('research.console');
    // Agent chat docked right and visible; observability hidden in the bottom dock.
    expect(frame.docks.right.tools.map((t) => t.viewId)).toContain('agent.chat');
    expect(frame.docks.right.visible).toBe(true);
    expect(frame.docks.bottom.visible).toBe(false);
  });

  it('survives a disabled module: unknown views are skipped, not fatal', () => {
    const partial = new Set(['research.console', 'library.panel']);
    const frame = seedFromPreset(preset!, { knownViews: partial });
    const views = areasOf(frame.center).flatMap((a) => a.tabs.map((t) => t.viewId));
    expect(views).toEqual(['research.console', 'library.panel']);
  });
});
