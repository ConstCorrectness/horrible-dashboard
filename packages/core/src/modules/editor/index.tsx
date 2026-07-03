/**
 * Editor module: markdown/text/code buffers (CodeMirror 6) over the URI source
 * model. The rendering target other modules use to open text — the file explorer
 * opens files as `workspace-file:` buffers, notes open as `note:` buffers.
 *
 * Ships the buffer panel + source model + `openBuffer` (C1), the command surface
 * + mod+s keybinding routed through the shell keybinding service (C3), agent
 * tools (C4), and the recent-notes dashboard widget (C5). See docs/modules/editor.md.
 */
import { registry, type ModuleManifest } from '../../registry';
import { editorAgentTools } from './agentTools';
import { BufferView } from './BufferView';
import { IndexedPackages } from './IndexedPackages';
import { getBuffer, listBufferUris } from './buffers';
import { RecentNotesWidget } from './RecentNotes';
import { registerEditorService } from './service';
import { createNote, sourceTitle } from './sources';

/** Optional knobs when opening a buffer. */
export interface OpenBufferOptions {
  /** Highlighting hint for sources with no extension to infer from (notes). */
  language?: 'javascript' | 'python';
}

/**
 * Open a buffer for a source URI (`note:<id>`, `workspace-file:<path>`). The
 * instance id is derived from the source so reopening the same source focuses the
 * existing buffer instead of duplicating it.
 */
export function openBuffer(source: string, opts?: OpenBufferOptions): void {
  registry.openPanel('editor.buffer', {
    instanceId: `editor.buffer:${source}`,
    params: { source, title: sourceTitle(source), language: opts?.language },
  });
}

// The source of the most recently focused buffer, so other modules (e.g. the
// file tree's "reveal active buffer") can locate it. A focus-tracked "active
// buffer" service is a later refinement.
let activeBufferSource: string | null = null;

export function getActiveBufferSource(): string | null {
  return activeBufferSource;
}

/** Internal: BufferView reports its source when it mounts/loads/focuses. */
export function setActiveBufferSource(source: string | null): void {
  activeBufferSource = source;
}

async function newNote(): Promise<void> {
  openBuffer(await createNote());
}

async function saveActive(): Promise<void> {
  const source = getActiveBufferSource();
  const buffer = source ? getBuffer(source) : undefined;
  if (buffer) await buffer.save();
}

async function saveAll(): Promise<void> {
  await Promise.all(listBufferUris().map((uri) => getBuffer(uri)?.save() ?? Promise.resolve()));
}

/**
 * Open the active buffer in the visualizer, inferring the engine from the buffer's
 * language (a `.py` buffer → pygame, JS → the visualizer's current/default engine).
 * Lives in the editor so it can read the active buffer; the visualizer exposes the
 * `setTarget` seam it drives.
 */
async function visualizeActiveBuffer(): Promise<void> {
  const uri = getActiveBufferSource();
  if (!uri) return;
  const { getActiveVisualizer } = await import('../visualizer/store');
  const { modeForLanguage, languageForUri } = await import('../visualizer/bridge');
  registry.openPanel('visualizer.pane');
  // Let the pane mount before pointing it at the buffer.
  await new Promise((resolve) => setTimeout(resolve, 100));
  const viz = getActiveVisualizer();
  if (!viz) return;
  const current = viz.getState().mode;
  viz.setTarget(uri, modeForLanguage(languageForUri(uri), current));
}

export const editorModule: ModuleManifest = {
  id: 'editor',
  title: 'Editor',
  panels: [
    {
      id: 'editor.buffer',
      title: 'Editor',
      component: BufferView,
      defaultPlacement: 'center',
      agentTools: editorAgentTools,
      // Not a singleton: one window per open buffer.
    },
  ],
  widgets: [
    {
      id: 'editor.recentNotes',
      title: 'Recent notes',
      component: RecentNotesWidget,
      defaultPlacement: 'left',
    },
  ],
  commands: [
    { id: 'editor.newNote', title: 'Editor: New note', run: newNote },
    { id: 'editor.save', title: 'Editor: Save', run: saveActive },
    { id: 'editor.saveAll', title: 'Editor: Save all', run: saveAll },
    {
      id: 'editor.visualizeBuffer',
      title: 'Editor: Open in visualizer',
      run: visualizeActiveBuffer,
    },
  ],
  // Editing keys go through the shell keybinding service, never a hardcoded
  // handler in the component — so they stay rebindable.
  keybindings: [{ key: 'mod+s', command: 'editor.save' }],
  settings: [
    {
      key: 'editor.autosuggest',
      title: 'Inline autosuggest',
      description:
        'Suggest inline completions from the local model as you type in a buffer (Tab to accept, Esc to dismiss).',
      type: 'boolean',
      default: false,
    },
    {
      key: 'editor.pythonPath',
      title: 'Python interpreter',
      description:
        'Path to the Python interpreter basedpyright analyzes against, so third-party import completions (e.g. torch, numpy) resolve. Leave empty to auto-detect a project .venv or your system Python. Takes effect for buffers opened after it changes.',
      type: 'string',
      default: '',
    },
    {
      key: 'editor.frameworkImports',
      title: 'Framework import suggestions',
      description:
        'Suggest common Python framework symbols and aliases (numpy/np, pandas/DataFrame, torch/nn, transformers/AutoModelForCausalLM, …) in any .py file and insert the import automatically when accepted. Fills the gap where the language server cannot auto-import installed libraries.',
      type: 'boolean',
      default: true,
    },
  ],
  // The indexed-packages table (framework versions per interpreter) is richer than a
  // declarative control, so it's a custom section.
  settingsSections: [
    { id: 'editor.indexedPackages', title: 'Indexed packages', component: IndexedPackages },
  ],
};

// Expose the editor's buffer surface to other modules (e.g. the visualizer) via
// the registry, so they don't deep-import editor internals.
registerEditorService();

export { loadSource, saveSource, sourceTitle, type LoadedSource } from './sources';
export type { EditorService, BufferLanguage, OpenBufferRequest } from './service';
