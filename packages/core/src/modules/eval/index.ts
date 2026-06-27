import { registry, type ModuleManifest } from '../../registry';
import { EvaluationDashboard } from './view';

export const evalModule: ModuleManifest = {
  id: 'eval',
  title: 'Evaluation',
  panels: [
    {
      id: 'eval.dashboard',
      title: 'Evaluation Suite',
      component: EvaluationDashboard,
      defaultPlacement: 'center',
      singleton: true,
    },
  ],
  commands: [
    {
      id: 'eval.open',
      title: 'Evaluation: Open Dashboard',
      run: () => registry.openPanel('eval.dashboard'),
    },
  ],
};
