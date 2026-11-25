import type { ModuleManifest } from '../../registry';
import { SUITE_SECTIONS } from '../interp/ids';
import { openSuiteTab } from '../interp/stage';

/**
 * Evals: measuring what a model actually does with this app's tools.
 *
 * Hosted inside the Interpretability IDE (`SUITE_SECTIONS.evals`), so suites,
 * sweeps, results, and comparisons sit right beside the Turn Debugger and Context
 * Window that explain every failing row.
 */
export const evalsModule: ModuleManifest = {
  id: 'evals',
  title: 'Evals',
  category: 'research',
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
      title: 'Evals: Open suites & benchmarks',
      run: () => {
        openSuiteTab(SUITE_SECTIONS.evals);
      },
    },
    // Preserves callers (`LocalModels.tsx`, `RecipePane.tsx`, saved keybindings)
    // that switch to a specific Evals sub-tab via `section.show:evals.hub:<tab>`.
    ...(['suites', 'run', 'results', 'compare'] as const).map((subTab) => ({
      id: `section.show:evals.hub:${subTab}`,
      title: `Evals: ${subTab[0].toUpperCase()}${subTab.slice(1)} (in Interpretability)`,
      run: () => {
        openSuiteTab(SUITE_SECTIONS.evals, { subTab });
      },
    })),
  ],
};
