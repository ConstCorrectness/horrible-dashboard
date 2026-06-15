import { registry, type ModuleManifest } from '../../registry';
import { BackendStatusWidget, WelcomeWidget } from './widgets';

/**
 * See docs/modules/dashboard.md. The dashboard is no longer a grid panel — it is
 * the default seeded **workspace** (a layout of common widget-panes). This module
 * contributes those widgets and a command to jump to the Dashboard tab.
 */
export const dashboardModule: ModuleManifest = {
  id: 'dashboard',
  title: 'Dashboard',
  commands: [
    {
      id: 'dashboard.open',
      title: 'Dashboard: Open',
      run: () => registry.switchWorkspace('dashboard'),
    },
  ],
  widgets: [
    {
      id: 'dashboard.welcome',
      title: 'Welcome',
      component: WelcomeWidget,
      defaultPlacement: 'left',
    },
    {
      id: 'dashboard.backendStatus',
      title: 'Backend status',
      component: BackendStatusWidget,
      defaultPlacement: 'right',
    },
  ],
};
