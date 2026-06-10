import { registry, type ModuleManifest } from '../../registry';
import { ObservabilityPanel, ObservabilityWidget } from './view';

/**
 * Observe the app's data flow (frontend↔backend↔external), Docker-Desktop-style.
 * Optional: the widget is NOT in the default dashboard layout — add it from the
 * picker, or open the fuller panel. See docs/modules/observability.md.
 */
export const observabilityModule: ModuleManifest = {
  id: 'observability',
  title: 'Observability',
  panels: [
    {
      id: 'observability.logs',
      title: 'Observability',
      component: ObservabilityPanel,
      defaultPlacement: 'bottom',
      singleton: true,
    },
  ],
  widgets: [
    {
      id: 'observability.io',
      title: 'Data flow',
      component: ObservabilityWidget,
    },
  ],
  commands: [
    {
      id: 'observability.open',
      title: 'Observability: Open data-flow panel',
      run: () => registry.openPanel('observability.logs'),
    },
  ],
};
