import { lazyPane } from '../../lazy-pane';
import { registry, type ModuleManifest } from '../../registry';

// Loaded when the pane first renders, not at boot — see `lazyPane`.
const EvalsHub = lazyPane(() => import('./EvalsHub'), 'EvalsHub');

/**
 * Evals: measuring what a model actually does with this app's tools.
 *
 * The module exists because progressive disclosure and `TOOL_BUDGET` were
 * mitigations nobody had ever measured. A case runs through the *real*
 * orchestrator loop against a stubbed browser, so what it scores is the catalog
 * this app ships — not a tool list a harness invented.
 *
 * **One pane, four sections**, per the pane-consolidation rule. Suites, Run,
 * Results and Compare are four views of the same object; four panes would mean
 * four openers and four copies of "which suite are we talking about".
 *
 * Cases are *not* authored here beyond a browse view. A suite is a `.jsonl` file
 * and the editor is the case editor — that is what makes a suite reviewable,
 * diffable and committable, which a form-built blob in a database is not.
 */
export const evalsModule: ModuleManifest = {
  id: 'evals',
  title: 'Evals',
  category: 'research',
  panels: [
    {
      // `document`: you work in it for a long stretch, beside the suite file you
      // are editing. A widget takes an area alone, which is wrong for a pane whose
      // whole workflow is "read the failure, fix the case, run it again".
      id: 'evals.hub',
      title: 'Evals',
      component: EvalsHub,
      role: 'document',
      icon: '🎯',
      singleton: true,
      sections: [
        { id: 'suites', label: 'Suites', icon: '📋', key: 's', default: true },
        { id: 'run', label: 'Run', icon: '▶', key: 'r' },
        { id: 'results', label: 'Results', icon: '📊', key: 'e' },
        { id: 'compare', label: 'Compare', icon: '🏁', key: 'c' },
      ],
    },
  ],
  settings: [
    {
      key: 'evals.acceptRemoteSuites',
      title: 'Accept suites from friends',
      description:
        "Let a friend offer an eval suite to run on this node's agent. Each offer still needs your accept, which shows its size and estimated cost; no tool acts, but your tokens are spent and your model plus your skills' and MCP tools' names and hashes are reported back. Off: offers are refused with this reason.",
      type: 'boolean',
      default: false,
    },
  ],
  commands: [
    {
      id: 'evals.open',
      title: 'Evals: Open',
      run: () => registry.openPanel('evals.hub'),
    },
  ],
};
