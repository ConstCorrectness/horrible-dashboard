import type { ReactElement } from 'react';

import { revealSection } from '../../layout/controller';
import { registry, type ModuleManifest } from '../../registry';
import './explorer.css';

/**
 * **Explorer** — one place to browse a workspace, replacing three.
 *
 * | Retired pane | Section |
 * | --- | --- |
 * | `files.tree` | **Files** |
 * | `notebook.browser` | **Notebooks** |
 * | `training.projects` | **Projects** |
 *
 * All three were the same pane wearing three hats: a narrow left-dock list you pick
 * something out of and open in the center. They were separate because there was no
 * way for a module to contribute to another module's pane — so each shipped its
 * own, each earned its own rail glyph, and the left dock became a row of near
 * identical strips you had to remember the names of.
 *
 * They are **not deleted**, they are `embedded`: still registered, so the two
 * that are also region strips of their document pane (Notebooks, Projects) keep
 * working exactly as before, and `show("notebooks")` still resolves. What they
 * lose is a competing top-level home.
 *
 * **`flow.library` and `records.list` are deliberately not here.** They were, and
 * that was the merge overreaching: Explorer answers "what is in this workspace",
 * and the three sections above are all trees over the same rooted content. A saved
 * flow and a table schema are not — each is a switcher for one document type, and
 * means nothing except beside the canvas or grid it re-points. Both now live as a
 * left region strip on their own document instead (`flow.editor`, the three
 * `records.*` documents), which is where a per-document switcher belongs. Their
 * names still resolve, via `VIEW_ALIASES` — leaving Explorer must not cost
 * reachability any more than joining it did.
 *
 * The extension point is `ModuleManifest.explorerSources` — the one place a module
 * legitimately contributes a section to a pane it does not own, and the reason a
 * plugin can add a browser without a pane of its own. See `ExplorerSourceDecl`.
 *
 * **Agent context is per section.** Section bodies render inside this pane's
 * instance id — which is why the file tree's snapshot arrives under
 * `explorer.home` rather than `files.tree` — but providers are keyed by instance
 * *and* section, so each source registers its own without clobbering its
 * neighbours. A source with none is reported as `hasPayload: false` rather than
 * silence. See docs/architecture/windowing.mdx.
 */
function ExplorerEmpty(): ReactElement {
  // Only reachable if every contributing module is disabled — an assembly whose
  // Explorer has nothing to explore. Says so rather than rendering blank.
  return (
    <div className="explorer-empty">
      <p>No browsers are installed.</p>
      <p className="explorer-empty-hint">
        Modules add one with <code>explorerSources</code>.
      </p>
    </div>
  );
}

export const explorerModule: ModuleManifest = {
  id: 'explorer',
  title: 'Explorer',
  panels: [
    {
      id: 'explorer.home',
      title: 'Explorer',
      component: ExplorerEmpty,
      role: 'tool',
      icon: '🗂',
      defaultDock: 'left',
      defaultDockSize: 280,
      singleton: true,
      // Sections are contributed, not declared — see `withExplorerSources`.
      explorerHost: true,
    },
  ],
  commands: [
    {
      id: 'explorer.open',
      title: 'Explorer: Open',
      run: () => registry.openPanel('explorer.home'),
    },
    {
      id: 'explorer.files',
      title: 'Explorer: Files',
      run: () => {
        revealSection('files', 'explorer.home');
      },
    },
  ],
};
