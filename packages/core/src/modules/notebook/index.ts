import { revealSection } from '../../layout/controller';
import type { ModuleManifest } from '../../registry';
import { notebookAgentTools } from './agentTools';
import { openNotebook } from './open';
import { NotebookBrowser } from './panels/NotebookBrowser';
import { NotebookEditor } from './panels/NotebookEditor';

/**
 * Notebook module: a domain-neutral reactive `.ipynb` notebook. JupyterLab-style
 * cells plus a marimo-style reactive dataflow mode (per-notebook toggle), running
 * on a kernel spawned from a managed venv (ipykernel + ipywidgets). Built on the
 * shared notebook kit (packages/core/src/notebook) and backend/notebook_core.
 * See docs/modules/notebook.mdx.
 */
export const notebookModule: ModuleManifest = {
  id: 'notebook',
  title: 'Notebook',
  settings: [
    {
      key: 'notebook.root',
      title: 'Notebook root',
      description: 'Directory that holds your .ipynb notebooks.',
      type: 'string',
      default: '~/horrible/notebooks',
    },
    {
      key: 'notebook.python',
      title: 'Kernel interpreter override',
      description: 'Path to a Python that has ipykernel + ipywidgets. Blank uses a managed venv.',
      type: 'string',
      default: '',
    },
    {
      key: 'notebook.python.version',
      title: 'Managed venv Python version',
      description: 'Python version uv pins when bootstrapping the managed notebook venv.',
      type: 'string',
      default: '3.12',
    },
  ],
  panels: [
    {
      id: 'notebook.browser',
      title: 'Notebooks',
      component: NotebookBrowser,
      role: 'tool',
      icon: '📓',
      defaultDock: 'left',
      singleton: true,
      // A section of Explorer now — see modules/explorer. Stays registered so the
      // region strip on `notebook.editor` keeps working unchanged.
      embedded: true,
    },
    {
      // Non-singleton: one pane per open notebook (params: {path}).
      id: 'notebook.editor',
      title: 'Notebook',
      component: NotebookEditor,
      role: 'document',
      editor: true,
      icon: '📓',
      regions: [
        { id: 'notebook.browser', label: 'Notebooks', icon: '📓', key: 'k', position: 'left' },
      ],
      // Full cell CRUD + execute + mode for the agent (group `notebook`).
      agentTools: notebookAgentTools,
    },
  ],
  explorerSources: [
    { id: 'notebooks', label: 'Notebooks', icon: '📓', view: 'notebook.browser', key: 'k' },
  ],
  /**
   * Right-clicking an `.ipynb` in the *file tree* offers to open it as a notebook
   * rather than as text. This module contributes it, because the files module has
   * no business knowing which extensions some other module can handle — that is
   * the whole reason the menu is assembled from providers instead of being a list
   * baked into the tree.
   *
   * `order: 1` puts it after the files module's own items. It is deliberately not
   * *instead* of "Open": a notebook is still a JSON file, and opening the raw text
   * is how you fix one the editor can't load.
   */
  contextMenu: [
    {
      kind: 'files.node',
      order: 1,
      items: (target) => {
        const path = String(target.path ?? '');
        if (target.nodeKind === 'dir' || !path.toLowerCase().endsWith('.ipynb')) return [];
        return [
          {
            id: 'notebook.openHere',
            label: 'Open as Notebook',
            run: () => openNotebook(path),
          },
        ];
      },
    },
  ],
  commands: [
    {
      id: 'notebook.open',
      title: 'Notebook: Open notebooks',
      run: () => {
        revealSection('notebooks', 'explorer.home');
      },
    },
  ],
  frames: [
    {
      id: 'notebook',
      name: 'Notebook',
      icon: '📓',
      frame: {
        // Empty document area: pick a notebook from the browser in the left dock.
        center: { tabs: [] },
        docks: { left: { tools: ['explorer.home'], size: 280 } },
      },
    },
  ],
};
