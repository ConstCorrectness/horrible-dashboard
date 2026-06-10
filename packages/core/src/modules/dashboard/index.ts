import { registry, type ModuleManifest } from '../../registry';
import { DashboardPanel } from './DashboardPanel';
import { BackendStatusWidget, WelcomeWidget, GameWidget } from './widgets';

/** See docs/modules/dashboard.md. */
export const dashboardModule: ModuleManifest = {
  id: 'dashboard',
  title: 'Dashboard',
  panels: [
    {
      id: 'dashboard.home',
      title: 'Dashboard',
      component: DashboardPanel,
      defaultPlacement: 'center',
    },
  ],
  commands: [
    {
      id: 'dashboard.open',
      title: 'Dashboard: Open',
      run: () => registry.openPanel('dashboard.home'),
    },
  ],
  widgets: [
    { id: 'dashboard.welcome', title: 'Welcome', component: WelcomeWidget },
    { id: 'dashboard.backendStatus', title: 'Backend status', component: BackendStatusWidget },
    { id: 'dashboard.gameWidget', title: 'Game Harness', component: GameWidget },
  ],
};
