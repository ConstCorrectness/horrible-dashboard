/**
 * Flow module: an n8n-style canvas for composing runnable multi-agent
 * orchestrations. Agent nodes reuse the orchestrator loop and the existing tool +
 * permission surface; only the canvas and the graph executor are new. See
 * docs/modules/flow-canvas.md.
 */
import './canvas/flow.css';

import { revealRegionView } from '../../layout/controller';
import type { ModuleManifest } from '../../registry';
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
      // Embedded, and its one home is the left region strip of `flow.editor`
      // above. It was briefly also an Explorer section, which was a mistake: a
      // flow is not a thing you browse alongside files and notebooks, it only
      // means anything next to the canvas that opens it. Explorer is for the
      // three trees you navigate a *workspace* with — see modules/explorer.
      embedded: true,
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
      // Opens the canvas' own left strip. `revealRegionView` opens the host pane
      // first when none is up, so this works from a cold workspace too.
      run: () => {
        revealRegionView('flow.library');
      },
    },
  ],
  frames: [
    {
      id: 'orchestration',
      name: 'Orchestration',
      icon: '🕸',
      frame: {
        center: { pane: 'flow.editor' },
        docks: { left: { tools: ['explorer.home'], size: 280 } },
      },
    },
  ],
};
