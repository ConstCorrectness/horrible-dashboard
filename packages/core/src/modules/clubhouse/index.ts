import { registry, type ModuleManifest } from '../../registry';
import { ClubhouseWidget } from './ClubhouseWidget';

/** See docs/modules/clubhouse.md. */
export const clubhouseModule: ModuleManifest = {
  id: 'clubhouse',
  title: 'Clubhouse',
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
  ],
};

export * from './api';
