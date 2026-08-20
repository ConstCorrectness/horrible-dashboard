import { registry, type ModuleManifest } from '../../registry';
import { EvalsHub } from './EvalsHub';

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
  frames: [
    {
      id: 'evals',
      name: 'Evals',
      icon: '🎯',
      frame: {
        center: {
          split: 'row',
          sizes: [0.55, 0.45],
          children: [
            // The scoreboard, and the suite file next to it. This pairing is the
            // whole working position: a failing row names a case, and the case is
            // one buffer away.
            { tabs: ['evals.hub'], active: 0 },
            // Where a fine-tune is compared against its base over time, and where
            // the notebook for an HF-backed benchmark lives.
            { tabs: ['editor.buffer', 'localtrack.workspace'], active: 0 },
          ],
        },
        docks: {
          left: { tools: ['explorer.home'], size: 260 },
          right: { tools: ['agent.chat'], size: 360 },
          bottom: { tools: ['observability.io'], size: 180, visible: false },
        },
      },
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
