/**
 * Code-intelligence module: the app-owned tree-sitter symbol index surfaced as an
 * **Outline** pane, plus the `symbols.*` agent tools. Wired to the rest of the app
 * through the shared **code locus** (core/locus.ts) — the outline both follows the
 * editor cursor and drives it — so this module never reaches into the editor.
 * See docs/modules/code.mdx.
 */
import { registry, type ModuleManifest } from '../../registry';
import { codeAgentTools } from './agentTools';
import { OutlinePane } from './OutlinePane';
import { symbolSearchModal } from './searchModal';
import { SymbolSearch } from './SymbolSearch';

export const codeModule: ModuleManifest = {
  id: 'code',
  title: 'Code',
  panels: [
    {
      id: 'code.outline',
      title: 'Outline',
      component: OutlinePane,
      defaultPlacement: 'left',
      singleton: true,
      agentTools: codeAgentTools,
    },
    {
      id: 'code.search',
      title: 'Symbol Search',
      component: SymbolSearch,
      defaultPlacement: 'right',
      singleton: true,
    },
  ],
  commands: [
    {
      id: 'code.openOutline',
      title: 'Code: Open symbol outline',
      // Outline/Search follow the editor cursor via the shared code locus, so they
      // reveal as companions inside the Code Workbench (editor.buffer) rather than as
      // detached panes. See docs/architecture/panel-groups.mdx.
      run: () => registry.revealCompanion('code.outline'),
    },
    {
      id: 'code.openSearch',
      title: 'Code: Open symbol search pane',
      run: () => registry.revealCompanion('code.search'),
    },
    {
      id: 'code.findSymbol',
      title: 'Code: Find symbol (quick open)',
      run: () => symbolSearchModal.set(true),
    },
  ],
  // Quick-open the symbol-search modal. mod+p (VS Code quick-open); the app's command
  // palette is mod+k. CodeMirror's basicSetup doesn't bind mod+p, so it works in the
  // editor too, and AppShell preventDefaults it (stopping the browser print dialog).
  keybindings: [{ key: 'mod+p', command: 'code.findSymbol' }],
};

export { SymbolSearchModal } from './SymbolSearchModal';
