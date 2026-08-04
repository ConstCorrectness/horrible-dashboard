import type { ReactElement } from 'react';

import { revealSection } from '../../layout/controller';
import { registry, type ModuleManifest } from '../../registry';
import './explorer.css';

/**
 * **Explorer** — one place to browse things, replacing five.
 *
 * | Retired pane | Section |
 * | --- | --- |
 * | `files.tree` | **Files** |
 * | `notebook.browser` | **Notebooks** |
 * | `training.projects` | **Projects** |
 * | `flow.library` | **Flows** |
 * | `records.list` | **Tables** |
 *
 * All five were the same pane wearing five hats: a narrow left-dock list you pick
 * something out of and open in the center. They were separate because there was no
 * way for a module to contribute to another module's pane — so each shipped its
 * own, each earned its own rail glyph, and the left dock became a row of near
 * identical strips you had to remember the names of.
 *
 * They are **not deleted**, they are `embedded`: still registered, so the three
 * that are also region strips of their document pane (Notebooks, Projects, Flows)
 * keep working exactly as before, and `show("notebooks")` still resolves. What
 * they lose is a competing top-level home.
 *
 * The extension point is `ModuleManifest.explorerSources` — the one place a module
 * legitimately contributes a section to a pane it does not own, and the reason a
 * plugin can add a browser without a pane of its own. See `ExplorerSourceDecl`.
 *
 * **One agent-context provider.** Section bodies render inside this pane's
 * instance id, so two providers would silently overwrite each other. Today that
 * one is `FileTree`'s — which is why the file tree's snapshot now arrives under
 * `explorer.home` rather than `files.tree`, with the active section stamped on it
 * by `readPaneAgentContext`. See docs/architecture/windowing.mdx.
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
