import { registry, type ModuleManifest } from '../../registry';
import { WelcomeWidget } from './widgets';

/**
 * See docs/modules/dashboard.md. Contributes the welcome card and a command to
 * open it.
 */
export const dashboardModule: ModuleManifest = {
  id: 'dashboard',
  title: 'Dashboard',
  category: 'system',
  commands: [
    {
      id: 'dashboard.open',
      title: 'Dashboard: Open',
      run: () => registry.openPanel('dashboard.welcome'),
    },
  ],
  widgets: [
    {
      id: 'dashboard.welcome',
      title: 'Welcome',
      component: WelcomeWidget,
      role: 'widget',
      icon: '👋',
    },
  ],
};
