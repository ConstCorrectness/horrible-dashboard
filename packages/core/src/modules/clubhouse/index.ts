import { registry, type ModuleManifest } from '../../registry';
import { clubhouseAgentTools } from './agentTools';
import { ClubhouseWidget } from './ClubhouseWidget';

/** See docs/modules/clubhouse.md. */
export const clubhouseModule: ModuleManifest = {
  id: 'clubhouse',
  title: 'Clubhouse',
  panels: [
    {
      id: 'clubhouse.account',
      title: 'Clubhouse',
      component: ClubhouseWidget,
      role: 'widget',
      icon: '🎙',
      // Account tile: reads fine as a narrow companion, so it earns a rail glyph
      // while still opening in the center by default.
      dockable: 'right',
      singleton: true,
      agentTools: clubhouseAgentTools,
    },
  ],
  commands: [
    {
      id: 'clubhouse.connect',
      title: 'Clubhouse: Connect account',
      // Open the account widget/panel in the active workspace.
      run: () => registry.openPanel('clubhouse.account'),
    },
  ],
};

export * from './api';
export * from './useClubhouseVoice';
