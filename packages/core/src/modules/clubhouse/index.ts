import { registry, type ModuleManifest } from '../../registry';
import { disconnectClubhouse, getClubhouseChannels, getClubhouseStatus } from './api';
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
      agentTools: [
        {
          name: 'clubhouse.status',
          description: 'Read the connected Clubhouse account status (name, username).',
          sideEffect: false,
          handler: () => getClubhouseStatus(),
        },
        {
          name: 'clubhouse.listRooms',
          description: 'List the live Clubhouse rooms for the connected account.',
          sideEffect: false,
          handler: () => getClubhouseChannels(),
        },
        {
          name: 'clubhouse.disconnect',
          description: 'Disconnect the connected Clubhouse account (clears the server-side token).',
          sideEffect: true,
          handler: () => disconnectClubhouse(),
        },
      ],
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
