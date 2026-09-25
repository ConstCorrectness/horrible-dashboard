import { lazyPane } from '../../lazy-pane';
import { registry, type ModuleManifest } from '../../registry';

// Loaded when the pane first renders, not at boot — see `lazyPane`.
const TrajectoriesHub = lazyPane(() => import('./TrajectoriesHub'), 'TrajectoriesHub');

/**
 * Trajectories: what this node's agents actually did, as queryable data.
 *
 * The module exists because the node runs agents constantly and retained almost
 * none of it. `agent_turns` records what a model was *shown*; `eval_results`
 * records how one graded case scored. Nothing recorded what the agent *did*, so
 * "which tool does the coder waste rounds on" and "did last week's prompt edit
 * help" had no data behind them.
 *
 * **One pane, four sections**, per the pane-consolidation rule: what is happening
 * now, the runs it becomes, the collections they land in, and the harness that
 * produced them are four views of one object.
 *
 * Capture is **on by default** (a `dashboard-agent` dataset, seeded once) and
 * dataset-scoped — see the Datasets section.
 * Runs are stored raw, including tool arguments, and redacted only on the way out
 * (export, peer share, MCP). That is a deliberate local-introspection stance and
 * it is documented in docs/modules/trajectories.mdx.
 */
export const trajectoriesModule: ModuleManifest = {
  id: 'trajectories',
  title: 'Trajectories',
  panels: [
    {
      // `document`: you read a run for a long stretch, usually beside the code or
      // the chat that produced it. A widget takes an area alone, which is wrong
      // for a pane whose workflow is "read the failure, change the harness, run
      // it again".
      id: 'trajectories.hub',
      title: 'Trajectories',
      component: TrajectoriesHub,
      role: 'document',
      icon: '🛤️',
      singleton: true,
      sections: [
        { id: 'runs', label: 'Runs', icon: '▤', key: 'r', default: true },
        { id: 'live', label: 'Live', icon: '◉', key: 'l' },
        { id: 'datasets', label: 'Datasets', icon: '▦', key: 'd' },
        { id: 'harness', label: 'Harness', icon: '⚖', key: 'h' },
        // Friends' shared runs. A section, not a pane: a pulled run *is* a run, and it
        // lands in Runs beside this node's own with the same detail view.
        { id: 'peers', label: 'Friends', icon: '⇄', key: 'p' },
        // Strangers, not friends: digests of runs — their shape, never their payloads —
        // on the agent commons index. Publishing happens from a run's detail view.
        { id: 'commons', label: 'Commons', icon: '◎', key: 'c' },
        // Agents you wrote, reporting in over OTLP: the endpoint, the token for other
        // machines, and a snippet per framework. Their runs land in Runs.
        { id: 'connect', label: 'Connect', icon: '⊕', key: 'o' },
      ],
    },
  ],
  commands: [
    {
      id: 'trajectories.open',
      title: 'Trajectories: Open',
      run: () => registry.openPanel('trajectories.hub'),
    },
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
    // OpenTelemetry (backend/modules/otel). None of these is a credential — the
    // export endpoint and its headers live in the `otel` connector.
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
      key: 'otel.forwardReceived',
      title: 'Forward received traces to the export connector',
      description:
        'Relay traces your own agents send here on to the external OTLP backend as well.',
      type: 'boolean',
      default: false,
    },
  ],
};
