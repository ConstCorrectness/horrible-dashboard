import { revealSection } from '../../layout/controller';
import { minibuffer } from '../../minibuffer';
import { registry, type ModuleManifest } from '../../registry';
import type { EditorService } from '../editor/service';
import { LlamaCppPane } from './ServerPane';
import { sendTracePrompt } from './trace-prompt';

/** Enough of the buffer to name it in the traces section. */
function bufferLabel(uri: string): string {
  const path = uri.replace(/^workspace-file:/, '');
  return path.split(/[\\/]/).pop() || path;
}

/**
 * Trace the editor's selection: what the model does with *your* code.
 *
 * The traces section's prompt was a fixed `'The capital of France is'`, which
 * demonstrates the machinery and answers nothing you were actually wondering.
 * Feeding it the buffer makes the token strip your identifiers and (with attention
 * on) the attention map a map over your own code.
 *
 * The editor is reached through its registered **service**, not by importing its
 * internals — the same seam the visualizer uses. A selection only exists in a
 * mounted CodeMirror view, so an unmounted buffer is reported rather than silently
 * traced from its persisted bytes: tracing something other than what is on screen
 * is worse than declining.
 *
 * An *untitled* buffer is reported specifically rather than as "no buffer": it is
 * on screen, so "needs an open buffer" would read as a bug. The editor tracks the
 * active buffer by source URI and an unsaved one has none, which is a real gap and
 * not worth papering over with a guess about which pane was meant.
 */
function traceSelection(): void {
  const editor = registry.getService<EditorService>('editor');
  const uri = editor?.getActiveBufferSource();
  if (!editor || !uri) {
    minibuffer.say(
      'Trace selection needs a saved buffer — an untitled one has no source to read',
      'error',
    );
    return;
  }
  const selection = editor.getBufferSelection(uri);
  if (!selection) {
    minibuffer.say('That buffer is not currently open in a visible pane', 'error');
    return;
  }
  // No selection means the cursor is somewhere — trace the whole buffer, the way
  // "run selection" tools treat an empty selection as "the file".
  const text = selection.text || editor.peekBufferContent(uri) || '';
  if (!text.trim()) {
    minibuffer.say('Nothing to trace — the buffer is empty', 'error');
    return;
  }
  const scope = selection.text ? 'selection' : 'whole file';
  sendTracePrompt({ prompt: text, label: `${bufferLabel(uri)} (${scope})` });
  revealSection('traces', 'llamacpp.server');
}

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
      // A three-section management console — pick a build, browse GGUFs, read a
      // trace — is a surface you work in, not a tile you glance at. It was a
      // `widget`, which reserves a whole centre area for one pane; the `lab`
      // preset tabs it with three others, which is a document's behaviour.
      role: 'document',
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
    {
      id: 'llamacpp.traceSelection',
      title: 'llama.cpp: Trace editor selection',
      run: traceSelection,
      slash: 'trace-selection',
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
