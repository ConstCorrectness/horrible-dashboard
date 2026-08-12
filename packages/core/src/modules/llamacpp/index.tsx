import { registry, type ModuleManifest } from '../../registry';
import { LlamaCppPane } from './ServerPane';

/**
 * llama.cpp: the node serving its own weights.
 *
 * Ollama and LM Studio are applications the user installs and runs elsewhere; the
 * agent talks to them and can neither see nor choose the file behind a model name.
 * This module makes the node itself the server — it fetches an upstream
 * `llama-server` build, keeps a GGUF catalog, and supervises the process — which
 * is what turns "which model am I running" from a name into a file on disk.
 *
 * One pane, two sections: the build+process and the weights are one workflow in a
 * strict order, and splitting them would give two panes of which one is usually an
 * instruction to open the other. See docs/modules/llamacpp.mdx.
 */
export const llamacppModule: ModuleManifest = {
  id: 'llamacpp',
  title: 'llama.cpp',
  panels: [
    {
      id: 'llamacpp.server',
      title: 'llama.cpp',
      component: LlamaCppPane,
      role: 'widget',
      icon: '🦙',
      singleton: true,
      sections: [
        { id: 'server', label: 'Server', icon: '⚙️', key: 's', default: true },
        { id: 'models', label: 'Models', icon: '🧠', key: 'm' },
        { id: 'traces', label: 'Traces', icon: '🔬', key: 't' },
      ],
    },
  ],
  commands: [
    {
      id: 'llamacpp.open',
      title: 'llama.cpp: Server and models',
      run: () => registry.openPanel('llamacpp.server'),
    },
  ],
  settings: [
    {
      key: 'llamacpp.modelDirs',
      title: 'Extra GGUF directories',
      description:
        'Folders scanned for .gguf files in addition to the managed one, separated by newlines or semicolons. Models found here are servable but never deleted by this app.',
      type: 'string',
      default: '',
    },
    {
      key: 'llamacpp.traceBudgetGb',
      title: 'Activation trace budget (GB)',
      description:
        'Ceiling for stored traces. One traced forward pass with attention on is around a gigabyte, so this is small on purpose; the oldest traces are pruned when a new one takes the directory over it.',
      type: 'number',
      default: 2,
    },
    {
      key: 'llamacpp.diskBudgetGb',
      title: 'Model disk budget (GB)',
      description:
        'Ceiling for the managed model directory. A download whose declared size would exceed it is refused before a byte is written, rather than filling the disk and failing 30 GB in.',
      type: 'number',
      default: 80,
    },
  ],
};

export * from './api';
