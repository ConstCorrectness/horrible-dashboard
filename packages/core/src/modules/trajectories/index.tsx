import type { ModuleManifest } from '../../registry';
import { SUITE_SECTIONS } from '../interp/ids';
import { openSuiteTab } from '../interp/stage';

/**
 * Trajectories: what this node's agents actually did, as queryable data.
 *
 * Now unified with Agentpedia inside the Interpretability IDE's `Runs` section
 * (`SUITE_SECTIONS.runs`), so fleet run search, live OTLP streams, paired harness
 * comparisons, and the 4-column round debugger live in one workspace instead of
 * two competing panes with duplicate "Runs" and "Harness" tabs.
 */
export const trajectoriesModule: ModuleManifest = {
  id: 'trajectories',
  title: 'Trajectories',
  category: 'research',
  commands: [
    {
      id: 'trajectories.open',
      title: 'Trajectories: Open runs & fleet history',
      run: () => {
        openSuiteTab(SUITE_SECTIONS.runs, { subTab: 'all' });
      },
    },
    ...(
      [
        ['runs', 'all', 'All Runs'],
        ['live', 'live', 'Live'],
        ['lineage', 'harness', 'Lineage'],
        ['follow', 'follow', 'Wire Requests'],
        ['datasets', 'datasets', 'Datasets'],
        ['harness', 'harness', 'Harness'],
        ['shared', 'datasets', 'Shared'],
        ['connect', 'datasets', 'Connect'],
      ] as const
    ).map(([oldTab, subTab, label]) => ({
      id: `section.show:trajectories.hub:${oldTab}`,
      title: `Trajectories: ${label} (in Interpretability)`,
      run: () => {
        openSuiteTab(SUITE_SECTIONS.runs, { subTab });
      },
    })),
  ],
  settings: [
    {
      key: 'trajectories.retentionRuns',
      title: 'Retention (runs)',
      description: 'Keep at most this many runs per dataset. Older runs are pruned oldest-first.',
      type: 'number',
      default: 5000,
    },
    {
      key: 'trajectories.captureDelegates',
      title: 'Capture delegated sub-agents',
      description:
        'Record delegated sub-agent turns as their own runs, linked to the parent. Off keeps a dataset to top-level turns only.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'otel.enabled',
      title: 'Trace built-in agents (OpenTelemetry)',
      description:
        "Record invoke_agent / chat / execute_tool spans for every agent turn, shown in a run's Trace view and sent to the export connector if one is set.",
      type: 'boolean',
      default: true,
    },
    {
      key: 'otel.captureContent',
      title: 'Include prompts and tool data in spans',
      description:
        'Add prompts, completions and tool arguments/results to spans (credential-shaped strings are masked). Off by default because spans can be exported to a third party.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'otel.injectEnv',
      title: 'Point notebook and training kernels at this node',
      description:
        'Set OTEL_EXPORTER_OTLP_ENDPOINT and friends when a kernel starts, so an instrumented agent reports here with no configuration. Your own OTEL_* variables take precedence.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'otel.grpcPort',
      title: 'OTLP/gRPC receiver port',
      description:
        '0 keeps it off. 4317 is the OTel default, which is what an exporter uses when nobody set OTEL_EXPORTER_OTLP_PROTOCOL. It binds a second listening port, so it is opt-in — and it is read at startup, so changing it needs a backend restart.',
      type: 'number',
      default: 0,
    },
    {
      key: 'otel.grpcHost',
      title: 'OTLP/gRPC bind address',
      description:
        'Which address the gRPC receiver listens on. Loopback by default: the node itself may be loopback-only, and this setting must not widen that silently. Non-loopback senders still need the ingest token.',
      type: 'string',
      default: '127.0.0.1',
    },
    {
      key: 'otel.propagateHttp',
      title: 'Send trace context to local services',
      description:
        "Add a W3C traceparent header to outbound requests aimed at loopback or LAN addresses, so a service you run yourself continues the agent's trace. Never sent to a public address.",
      type: 'boolean',
      default: false,
    },
    {
      key: 'otel.exportPeerSpans',
      title: "Export friends' returned traces",
      description:
        "When a friend's agent answers yours and sends back the trace of that answer, pass it on to your export destinations (Opik, the OTLP connector) as part of your own trace. It is stored on this node either way.",
      type: 'boolean',
      default: true,
    },
    {
      key: 'otel.forwardReceived',
      title: 'Forward received traces to the export connector',
      description:
        'Relay traces your own agents send here on to the external OTLP backend as well.',
      type: 'boolean',
      default: false,
    },
  ],
};
