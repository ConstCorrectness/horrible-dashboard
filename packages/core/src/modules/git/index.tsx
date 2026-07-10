/**
 * Git provenance module: line → the agent conversation that wrote it. A Blame view
 * (follows the code locus, session chips open the conversation) + a History review of
 * agent-authored commits, plus the `git.commit`/`git.blame`/`git.log` agent tools.
 * See docs/modules/git.mdx.
 */
import { revealRegionView } from '../../layout/controller';
import { type ModuleManifest } from '../../registry';
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
      role: 'tool',
      icon: '⎇',
      defaultDock: 'right',
      singleton: true,
      agentTools: gitAgentTools,
    },
  ],
  commands: [
    {
      id: 'git.openProvenance',
      title: 'Git: Open provenance (blame + history)',
      // Provenance is blame+history of the active buffer, so it lives as a companion
      // of the Code Workbench (editor.buffer) rather than a detached pane — reveal it
      // inside the editor group. See docs/architecture/panel-groups.mdx.
      run: () => revealRegionView('git.provenance'),
    },
  ],
};
