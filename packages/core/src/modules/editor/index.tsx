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
import { getBuffer, listBufferUris } from './buffers';
import { RecentNotesWidget } from './RecentNotes';
import { createNote, sourceTitle } from './sources';

/**
 * Open a buffer for a source URI (`note:<id>`, `workspace-file:<path>`). The
 * instance id is derived from the source so reopening the same source focuses the
 * existing buffer instead of duplicating it.
 */
export function openBuffer(source: string): void {
  registry.openPanel('editor.buffer', {
    instanceId: `editor.buffer:${source}`,
    params: { source, title: sourceTitle(source) },
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
  ],
};

export { loadSource, saveSource, sourceTitle, type LoadedSource } from './sources';
