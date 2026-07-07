/**
 * Git provenance module: line → the agent conversation that wrote it. A Blame view
 * (follows the code locus, session chips open the conversation) + a History review of
 * agent-authored commits, plus the `git.commit`/`git.blame`/`git.log` agent tools.
 * See docs/modules/git.mdx.
 */
import { registry, type ModuleManifest } from '../../registry';
import { gitAgentTools } from './agentTools';
import { ProvenancePane } from './ProvenancePane';

export const gitModule: ModuleManifest = {
  id: 'git',
  title: 'Git',
  panels: [
    {
      id: 'git.provenance',
      title: 'Provenance',
      component: ProvenancePane,
      defaultPlacement: 'right',
      singleton: true,
      agentTools: gitAgentTools,
    },
  ],
  commands: [
    {
      id: 'git.openProvenance',
      title: 'Git: Open provenance (blame + history)',
      run: () => registry.openPanel('git.provenance'),
    },
  ],
};
