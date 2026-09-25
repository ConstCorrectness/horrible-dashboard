/**
 * GitHub module: a repository viewer (panel `github.repo`).
 *
 * The *connector* (auth, credential custody, agent tools) lives on the backend under
 * `backend/modules/connectors/providers/github*`; this module is only the human
 * surface over it. See docs/modules/github.mdx.
 */
import { lazyPane } from '../../lazy-pane';
import { registry, type ModuleManifest } from '../../registry';
import './github.css';

// Loaded when the pane first renders, not at boot — see `lazyPane`.
const RepoViewer = lazyPane(() => import('./RepoViewer'), 'RepoViewer');

export const githubModule: ModuleManifest = {
  id: 'github',
  title: 'GitHub',
  category: 'build',
  panels: [
    {
      id: 'github.repo',
      title: 'GitHub',
      component: RepoViewer,
      // A document, and deliberately not a singleton: browsing two repositories
      // side by side is the obvious thing to want.
      role: 'document',
      icon: '🐙',
    },
  ],
  commands: [
    {
      id: 'github.openViewer',
      title: 'GitHub: Open repository viewer',
      run: () => {
        registry.openPanel('github.repo');
      },
    },
  ],
};
