import { registry, type ModuleManifest } from '../../registry';
import { telemetryStore } from '../../telemetry';
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
      role: 'widget',
      icon: '⊡',
      singleton: true,
      // Embedded: the fuller inspector behind `observability.io`'s bottom strip.
      // The compact widget is the destination; this is its expanded view.
      embedded: true,
    },
  ],
  widgets: [
    {
      id: 'observability.io',
      title: 'Data flow',
      component: ObservabilityWidget,
      role: 'tool',
      icon: '◉',
      defaultDock: 'bottom',
      // The fuller inspector rides along as a bottom region strip.
      regions: [{ id: 'observability.logs', label: 'Inspector', icon: '⊡', position: 'bottom' }],
      // The agent reads the I/O snapshot via getAgentContext (see view.tsx); this
      // is its one write action. Gated like any side effect.
      agentTools: [
        {
          name: 'observability.clear',
          description: 'Clear the observability data-flow log (all captured I/O events).',
          sideEffect: true,
          handler: () => {
            telemetryStore.clear();
            return { ok: true, cleared: true };
          },
        },
      ],
    },
  ],
  commands: [
    {
      id: 'observability.open',
      title: 'Observability: Open data-flow view',
      run: () => registry.openPanel('observability.io'),
    },
  ],
  settings: [
    {
      key: 'observability.recentCount',
      title: 'Recent calls in the Data flow widget',
      description: 'How many of the most recent I/O calls the dashboard widget lists.',
      type: 'number',
      default: 5,
    },
    {
      key: 'observability.maxBodyChars',
      title: 'Captured body size (characters)',
      description:
        'How many characters of each request/response body (and /ws frame) to keep for the inspector (applied by the backend at capture time). Bodies are still hard-capped at 1 MB.',
      type: 'number',
      default: 16384,
    },
  ],
};
