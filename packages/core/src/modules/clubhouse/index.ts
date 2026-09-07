import { registry, type ModuleManifest } from '../../registry';
import { clubhouseAction } from './actions';
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
    {
      id: 'clubhouse.toggleMic',
      title: 'Clubhouse: Toggle microphone',
      run: () => clubhouseAction('toggleMic'),
    },
  ],
  keybindings: [
    // Scoped to the pane: `1` unscoped would be swallowed by — and would swallow —
    // every text field in the app, this pane's own room chat first among them.
    {
      key: '1',
      command: 'clubhouse.toggleMic',
      when: "paneFocus == 'clubhouse.account'",
    },
  ],
};

export * from './actions';
export * from './api';
export * from './useClubhouseVoice';
