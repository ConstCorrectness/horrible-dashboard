import { registry, type ModuleManifest } from '../../registry';
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
    },
  ],
  commands: [
    {
      id: 'clubhouse.connect',
      title: 'Clubhouse: Connect account',
      // The widget lives on the dashboard grid; the command takes you there.
      run: () => registry.openPanel('dashboard.home'),
    },
    {
      id: 'clubhouse.rooms',
      title: 'Clubhouse: Live rooms',
      run: () => registry.openPanel('clubhouse.rooms'),
    },
  ],
};

export * from './api';
