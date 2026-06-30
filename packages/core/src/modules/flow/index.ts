/**
 * Flow module: an n8n-style canvas for composing runnable multi-agent
 * orchestrations. Agent nodes reuse the orchestrator loop and the existing tool +
 * permission surface; only the canvas and the graph executor are new. See
 * docs/modules/flow-canvas.md.
 */
import './canvas/flow.css';

import { registry, type ModuleManifest, type PanelGroupDecl } from '../../registry';
import { FlowEditorPanel } from './panels/FlowEditorPanel';
import { FlowLibraryPanel, openFlow } from './panels/FlowLibraryPanel';
import { createFlow } from './flows';

export const flowModule: ModuleManifest = {
  id: 'flow',
  title: 'Flow',
  panelGroups: [
    {
      id: 'flow.studio',
      label: 'Flow Studio',
      primary: 'flow.library',
      companions: [{ id: 'flow.editor', label: 'Canvas', icon: '⬡' }],
    } satisfies PanelGroupDecl,
  ],
  panels: [
    {
      id: 'flow.editor',
      title: 'Flow',
      component: FlowEditorPanel,
      defaultPlacement: 'center',
      // Multi-instance: one editor pane per open flow (keyed by flowId param).
    },
    {
      id: 'flow.library',
      title: 'Flows',
      component: FlowLibraryPanel,
      defaultPlacement: 'left',
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
  layouts: [
    {
      id: 'orchestration',
      name: 'Orchestration',
      icon: '🕸',
      panes: [
        { id: 'flow.library' },
        { id: 'flow.editor', position: { referencePanel: 'flow.library', direction: 'right' } },
      ],
    },
  ],
};
