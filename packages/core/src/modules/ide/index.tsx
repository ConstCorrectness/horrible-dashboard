/**
 * **IDE module** — the workbench: one pane that is a code editor with its
 * sidebars, the shape every developer already has in their fingers.
 *
 * The parts existed before this module did. `editor.buffer` is CodeMirror with a
 * language server, `files.tree` is a file explorer, `code.outline` follows the
 * cursor, `terminal.instance` is a real PTY — and the `scripting` workspace
 * preset already arranged them in docks. What was missing was a single surface
 * that composes them, and three panels nobody had written: a repo-wide text
 * search, a source-control view, and a problems list.
 *
 * The composition is done with **`regions:`** (see `manifest.ts`), not with
 * layout code. That is forced — `packages/core` cannot import `@horrible/ui`, so
 * a pane declared here has no way to render another module's view — and it turns
 * out to be the better answer anyway: the strips inherit resize, per-instance
 * persistence, collapse-to-rail, tabbing and drag-out from the frame engine
 * instead of a second docking engine growing inside a pane.
 *
 * See docs/modules/ide.mdx.
 */
import { revealRegionView, toggleRegionView } from '../../layout/controller';
import { registry, type ModuleManifest } from '../../registry';
import { openBuffer } from '../editor';
import { ideAgentTools } from './agentTools';
import { IdeWorkbench, workbenchInstanceId } from './IdeWorkbench';
import { IDE_KEYBINDINGS, IDE_PANE_META, IDE_REGIONS, WORKBENCH_VIEW } from './manifest';
import { activateTab, closeTab, readTabs } from './openBuffers';
import { focusSearchQuery } from './TextSearchPane';
import { ProblemsPane } from './ProblemsPane';
import { ScmPane } from './ScmPane';
import { TextSearchPane } from './TextSearchPane';

export { WORKBENCH_VIEW } from './manifest';

/**
 * Open the workbench, and a file in it when given one.
 *
 * Prefer plain `openBuffer` from callers that do not care where a file lands:
 * while a workbench is open it claims those anyway (the `BufferHost` seam), and
 * while none is, an ordinary editor pane is the right answer for a user who has
 * not asked for an IDE.
 */
export function openInWorkbench(source?: string): void {
  registry.openPanel(WORKBENCH_VIEW);
  if (!source) return;
  // The pane mounts — and installs its host — on the next tick, so the open is
  // deferred rather than racing it. Otherwise the very first file opened this
  // way would split off an editor pane beside the workbench just created.
  queueMicrotask(() => openBuffer(source));
}

/** Close the active tab of the live workbench, prompting if it is unsaved. */
function closeActiveTab(): void {
  const host = workbenchInstanceId();
  if (!host) return;
  const { active } = readTabs(host);
  if (active) closeTab(host, active);
}

/** Move by `delta` through the open tabs, wrapping. */
function cycleTab(delta: number): void {
  const host = workbenchInstanceId();
  if (!host) return;
  const { tabs, active } = readTabs(host);
  if (tabs.length < 2 || !active) return;
  activateTab(host, tabs[(tabs.indexOf(active) + delta + tabs.length) % tabs.length]);
}

export const ideModule: ModuleManifest = {
  id: 'ide',
  title: 'IDE',
  panels: [
    {
      id: WORKBENCH_VIEW,
      // Title, role, icon, singleton and capture come from `manifest.ts` so a
      // test can assert on them without importing this file (which reaches the
      // editor, and so a WebSocket).
      ...IDE_PANE_META,
      component: IdeWorkbench,
      // Visible to a guest as a pane with no content protocol, exactly as a
      // buffer is. The params allowlist stays empty on purpose: this pane's
      // params are the list of files you have open.
      share: { mode: 'mirror' },
      regions: IDE_REGIONS,
      agentTools: ideAgentTools,
    },
    // The three panels the workbench needed and the app did not have. All three
    // are `embedded`: their home is a region strip, and a standalone opener would
    // present a second, competing one.
    {
      id: 'ide.textSearch',
      title: 'Search',
      component: TextSearchPane,
      role: 'tool',
      icon: '⌕',
      defaultDock: 'left',
      singleton: true,
      embedded: true,
    },
    {
      id: 'ide.scm',
      title: 'Source Control',
      component: ScmPane,
      role: 'tool',
      icon: '⎇',
      defaultDock: 'left',
      singleton: true,
      embedded: true,
    },
    {
      id: 'ide.problems',
      title: 'Problems',
      component: ProblemsPane,
      role: 'tool',
      icon: '⚠',
      defaultDock: 'bottom',
      singleton: true,
      embedded: true,
    },
  ],
  commands: [
    { id: 'ide.open', title: 'IDE: Open workbench', run: () => openInWorkbench(), slash: 'ide' },
    {
      id: 'ide.findInFiles',
      title: 'IDE: Find in files',
      run: () => {
        revealRegionView('ide.textSearch');
        focusSearchQuery();
      },
      slash: 'find-in-files',
    },
    { id: 'ide.showScm', title: 'IDE: Source control', run: () => revealRegionView('ide.scm') },
    {
      id: 'ide.showProblems',
      title: 'IDE: Problems',
      run: () => revealRegionView('ide.problems'),
    },
    {
      id: 'ide.showExplorer',
      title: 'IDE: Show explorer',
      run: () => revealRegionView('files.tree'),
    },
    {
      id: 'ide.toggleTerminal',
      title: 'IDE: Toggle terminal panel',
      run: () => toggleRegionView('terminal.instance'),
    },
    {
      id: 'ide.toggleAgent',
      title: 'IDE: Toggle agent panel',
      run: () => toggleRegionView('agent.chat'),
    },
    { id: 'ide.closeTab', title: 'IDE: Close active tab', run: closeActiveTab },
    { id: 'ide.nextTab', title: 'IDE: Next tab', run: () => cycleTab(1) },
    { id: 'ide.prevTab', title: 'IDE: Previous tab', run: () => cycleTab(-1) },
  ],
  keybindings: IDE_KEYBINDINGS,
};

export { workbenchInstanceId } from './IdeWorkbench';
