import { registry, type ModuleManifest } from '../../registry';
import { TrajectoriesHub } from './TrajectoriesHub';

/**
 * Trajectories: what this node's agents actually did, as queryable data.
 *
 * The module exists because the node runs agents constantly and retained almost
 * none of it. `agent_turns` records what a model was *shown*; `eval_results`
 * records how one graded case scored. Nothing recorded what the agent *did*, so
 * "which tool does the coder waste rounds on" and "did last week's prompt edit
 * help" had no data behind them.
 *
 * **One pane, three sections**, per the pane-consolidation rule: the runs, the
 * collections they land in, and the harness that produced them are three views of
 * one object.
 *
 * Capture is **off by default** and dataset-scoped — see the Datasets section.
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
        { id: 'datasets', label: 'Datasets', icon: '▦', key: 'd' },
        { id: 'harness', label: 'Harness', icon: '⚖', key: 'h' },
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
  ],
};
