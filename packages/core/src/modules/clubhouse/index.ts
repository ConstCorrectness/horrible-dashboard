import { registry, type ModuleManifest } from '../../registry';
import { disconnectClubhouse, getClubhouseChannels, getClubhouseStatus } from './api';
import { ClubhouseWidget } from './ClubhouseWidget';
import { RoomsPanel } from './RoomsPanel';

/** See docs/modules/clubhouse.md. */
export const clubhouseModule: ModuleManifest = {
  id: 'clubhouse',
  title: 'Clubhouse',
  panels: [
    {
      id: 'clubhouse.rooms',
      title: 'Live rooms',
      component: RoomsPanel,
      defaultPlacement: 'center',
      singleton: true,
    },
  ],
  widgets: [
    {
      id: 'clubhouse.account',
      title: 'Clubhouse',
      component: ClubhouseWidget,
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
      // Open the account widget as a pane in the active workspace.
      run: () => registry.openPanel('clubhouse.account'),
    },
    {
      id: 'clubhouse.rooms',
      title: 'Clubhouse: Live rooms',
      run: () => registry.openPanel('clubhouse.rooms'),
    },
  ],
};

export * from './api';
