import { revealSection } from '../../layout/controller';
import type { ModuleManifest } from '../../registry';
import { notebookAgentTools } from './agentTools';
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
