/**
 * Flow module: an n8n-style canvas for composing runnable multi-agent
 * orchestrations. Agent nodes reuse the orchestrator loop and the existing tool +
 * permission surface; only the canvas and the graph executor are new. See
 * docs/modules/flow-canvas.md.
 */
import './canvas/flow.css';

import { registry, type ModuleManifest } from '../../registry';
import { FlowEditorPanel } from './panels/FlowEditorPanel';
import { FlowLibraryPanel, openFlow } from './panels/FlowLibraryPanel';
import { createFlow } from './flows';

export const flowModule: ModuleManifest = {
  id: 'flow',
  title: 'Flow',
  panels: [
    {
      // The canvas is the document (inverted from the old flow.studio group,
      // where the library was the primary): every node editor centers the canvas.
      id: 'flow.editor',
      title: 'Flow',
      component: FlowEditorPanel,
      role: 'document',
      icon: '⬡',
      regions: [{ id: 'flow.library', label: 'Flows', icon: '🗂', key: 'f', position: 'left' }],
      // Multi-instance: one editor pane per open flow (keyed by flowId param).
    },
    {
      id: 'flow.library',
      title: 'Flows',
      component: FlowLibraryPanel,
      role: 'tool',
      icon: '🗂',
      defaultDock: 'left',
      singleton: true,
    },
  ],
  commands: [
    {
      id: 'flow.new',
      title: 'Flow: New orchestration',
      run: async () => {
        const flow = await createFlow('Untitled flow');
        openFlow(flow.id);
      },
    },
    {
      id: 'flow.openLibrary',
      title: 'Flow: Open library',
      run: () => registry.openPanel('flow.library'),
    },
  ],
  frames: [
    {
      id: 'orchestration',
      name: 'Orchestration',
      icon: '🕸',
      frame: {
        center: { pane: 'flow.editor' },
        docks: { left: { tools: ['flow.library'], size: 280 } },
      },
    },
  ],
};
