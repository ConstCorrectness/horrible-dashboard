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
      title: 'Observability (Network & API Calls)',
      component: ObservabilityPanel,
      role: 'document',
      icon: '📡',
      singleton: true,
      dockable: ['bottom', 'right'],
    },
  ],
  widgets: [
    {
      id: 'observability.io',
      title: 'Data Flow Monitor',
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
      id: 'observability.openLogs',
      title: 'Observability: Open Request & Endpoint Inspector',
      run: () => registry.openPanel('observability.logs'),
    },
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
      key: 'observability.mutedSources',
      title: 'Muted I/O sources',
      description:
        'Comma-separated sources to hide from the data-flow list — for example `ws` to stop websocket frames burying everything else. Muting hides rows; nothing stops being captured, so unmuting brings the history back rather than starting a new one. Edited by the toggles in the panel itself.',
      type: 'string',
      default: '',
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
