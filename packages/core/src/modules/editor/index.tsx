/**
 * Editor module: markdown/text/code buffers (CodeMirror 6) over the URI source
 * model. The rendering target other modules use to open text — the file explorer
 * opens files as `workspace-file:` buffers, notes open as `note:` buffers.
 *
 * Ships the buffer panel + source model + `openBuffer` (C1), the command surface
 * + mod+s keybinding routed through the shell keybinding service (C3), agent
 * tools (C4), and the recent-notes dashboard widget (C5). See docs/modules/editor.md.
 */
import { areaHostingView, openPaneInArea, retargetPane } from '../../layout/controller';
import { getLocus, subscribeLocus } from '../../locus';
import { minibuffer } from '../../minibuffer';
import { registry, type ModuleManifest } from '../../registry';
import { editorAgentTools } from './agentTools';
import { BufferView } from './BufferView';
import { IndexedPackages } from './IndexedPackages';
import { getBuffer, listBufferUris } from './buffers';
import { focusedEditorView, toggleCompletionIn } from './completion';
import { RecentNotesWidget } from './RecentNotes';
import { registerEditorService } from './service';
import { createNote, sourceTitle } from './sources';

/** Optional knobs when opening a buffer. */
export interface OpenBufferOptions {
  /** Highlighting hint for sources with no extension to infer from (notes). */
  language?: 'javascript' | 'python';
}

/**
 * The pane holding the buffer the user is looking at, when that buffer is blank
 * and unmodified. Such a buffer is a placeholder, not work — opening a note from
 * it should land *in* it rather than split off a second editor. Null whenever
 * there is anything to preserve (content, unsaved edits) or it's already the
 * target, in which case the caller does a normal focus-or-create open.
 */
function blankBufferPane(target: string): string | null {
  const active = getActiveBufferSource();
  if (!active || active === target) return null;
  const snapshot = getBuffer(active)?.snapshot();
  if (!snapshot || snapshot.dirty || snapshot.content.trim() !== '') return null;
  return `editor.buffer:${active}`;
}

/**
 * Open a buffer for a source URI (`note:<id>`, `workspace-file:<path>`). The
 * instance id is derived from the source so reopening the same source focuses the
 * existing buffer instead of duplicating it, and a blank current buffer is reused
 * in place instead of accumulating empty editors.
 *
 * When neither applies, the buffer joins whatever area the editor already occupies
 * as another tab. Role-routed `openPanel` would split off a fresh area instead —
 * correct for the first document, but it means every file clicked in the tree
 * carves the frame up further.
 */
export function openBuffer(source: string, opts?: OpenBufferOptions): void {
  const instanceId = `editor.buffer:${source}`;
  const params = { source, title: sourceTitle(source), language: opts?.language };
  const blank = blankBufferPane(source);
  if (blank && retargetPane(blank, instanceId, params)) return;
  const area = areaHostingView('editor.buffer');
  if (area && openPaneInArea('editor.buffer', area, params, instanceId)) return;
  registry.openPanel('editor.buffer', { instanceId, params });
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

// The active buffer's Save As, published the same way BrowserPanel publishes its
// URL-bar focuser: the implementation needs the live CodeMirror view, which only
// the mounted component has, but `/save-as` has to reach it from the minibuffer.
let activeSaveAs: (() => Promise<boolean>) | null = null;

/** Internal: BufferView registers its Save As while it is the active buffer. */
export function setActiveSaveAs(fn: (() => Promise<boolean>) | null): void {
  activeSaveAs = fn;
}

async function saveActiveAs(): Promise<void> {
  if (!activeSaveAs) {
    minibuffer.say('Save As needs an open editor buffer', 'error');
    return;
  }
  await activeSaveAs();
}

async function newNote(): Promise<void> {
  openBuffer(await createNote());
}

// Untitled buffers are distinguished only by a counter — they have no source URI to
// key on, and two scratch buffers must be able to coexist.
let untitledSeq = 0;

/**
 * A scratch buffer with no source: an unsaved *file*, not a note. It opens as
 * `untitled.md` and detects its own language from what you type, until you Save As and
 * the name settles it. A blank current buffer is reused rather than adding a second
 * empty editor, the same rule `openBuffer` follows.
 */
function newBuffer(): void {
  const blank = blankBufferPane('');
  const params = { title: 'untitled.md' };
  const instanceId = `editor.buffer:untitled:${++untitledSeq}`;
  if (blank && retargetPane(blank, instanceId, params)) return;
  const area = areaHostingView('editor.buffer');
  if (area && openPaneInArea('editor.buffer', area, params, instanceId)) return;
  registry.openPanel('editor.buffer', { instanceId, params });
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
      role: 'document',
      editor: true,
      icon: '✎',
      // Visible to a guest as a pane, with no content protocol yet — a
      // buffer's text arrives when the editor grows a collab room. The
      // params allowlist stays empty on purpose: a buffer's params carry
      // the file path, which is exactly the kind of thing a guest should
      // not learn from the fact that a pane is open.
      share: { mode: 'mirror' },
      // The code-workbench satellites as regions: panes that only mean something
      // *relative to the active buffer*. Outline and Symbol Search follow the
      // editor cursor via the shared code locus; Provenance is blame+history of
      // the active file; Recent notes indexes the editor's own buffers. Files and
      // Terminal are deliberately NOT here — they're co-equal tools composed by
      // the `scripting` preset, not satellites. Region state persists per buffer.
      regions: [
        { id: 'code.outline', label: 'Outline', icon: '≡', key: 'o', position: 'right' },
        { id: 'code.search', label: 'Symbol Search', icon: '⌕', key: 's', position: 'right' },
        { id: 'git.provenance', label: 'Provenance', icon: '⎇', key: 'g', position: 'right' },
        { id: 'editor.recentNotes', label: 'Recent notes', icon: '🗒', key: 'r', position: 'left' },
      ],
      agentTools: editorAgentTools,
      // Not a singleton: one window per open buffer.
    },
  ],
  widgets: [
    {
      id: 'editor.recentNotes',
      title: 'Recent notes',
      component: RecentNotesWidget,
      role: 'widget',
      icon: '🗒',
      // Embedded: the editor's left region strip is its home. It was never
      // seeded anywhere or opened by a command, so the standalone entry it had
      // in the type switcher was a duplicate nobody used.
      embedded: true,
    },
  ],
  commands: [
    { id: 'editor.newNote', title: 'Editor: New note', run: newNote, slash: 'new' },
    {
      id: 'editor.newBuffer',
      title: 'Editor: New buffer',
      run: newBuffer,
      slash: 'scratch',
    },
    { id: 'editor.save', title: 'Editor: Save', run: saveActive, slash: 'save' },
    { id: 'editor.saveAs', title: 'Editor: Save as…', run: saveActiveAs, slash: 'save-as' },
    { id: 'editor.saveAll', title: 'Editor: Save all', run: saveAll, slash: 'save-all' },
    {
      id: 'editor.visualizeBuffer',
      title: 'Editor: Open in visualizer',
      run: visualizeActiveBuffer,
    },
    {
      id: 'editor.toggleSuggestions',
      title: 'Editor: Toggle suggestions',
      run: () => {
        const view = focusedEditorView();
        if (view) toggleCompletionIn(view);
      },
    },
  ],
  // Editing keys go through the shell keybinding service, never a hardcoded
  // handler in the component — so they stay rebindable.
  keybindings: [
    { key: 'mod+s', command: 'editor.save' },
    // `ctrl+shift+space`, not CodeMirror's own `ctrl+space`: that one is the IME
    // toggle on Windows and the input-source switch on macOS (both in
    // `keymap/reserved.ts`, both `preventable: false`), so it never reaches the
    // page and reads as "the popup is broken". `when: textInput` scopes it to a
    // focused editor surface, which is the only place it means anything — and it
    // covers a notebook cell as well as a buffer, since both are contenteditable.
    { key: 'ctrl+shift+space', command: 'editor.toggleSuggestions', when: 'textInput' },
  ],
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
      key: 'editor.indexedSymbols',
      title: 'Indexed symbol completions',
      description:
        'Merge the indexed Python standard library and installed packages into the completion popup, with signatures and docstrings. Accepting one inserts its import line automatically. Built once in the background; see the Indexed packages section below.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'editor.completionWarmupMs',
      title: 'Language server warm-up wait (ms)',
      description:
        'How long a completion request waits for a still-starting language server before falling back to the instant indexed results. A cold buffer needs a few seconds to spawn and index; raise this on large projects, set 0 to never wait.',
      type: 'number',
      default: 2000,
    },
    {
      key: 'editor.changeDebounceMs',
      title: 'Edit push debounce (ms)',
      description:
        'How long to wait after you stop typing before sending the buffer to the language server. Lower means fresher completions and diagnostics at the cost of more traffic.',
      type: 'number',
      default: 300,
    },
    {
      key: 'editor.diagnostics',
      title: 'Show diagnostics',
      description:
        'Render language-server errors and warnings in the gutter and inline. The agent can still read diagnostics when this is off.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'editor.hover',
      title: 'Hover tooltips',
      description: 'Show the language server’s type and documentation tooltip on hover.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'editor.completionTrigger',
      title: 'Completion popup',
      description:
        'When the completion popup opens. “As you type” also opens it on Tab and Ctrl-Space; “Only on Tab” keeps the buffer quiet until you ask. Tab still indents wherever there is nothing to complete.',
      type: 'enum',
      enumValues: ['auto', 'manual'],
      default: 'auto',
    },
    {
      key: 'editor.importCompletions',
      title: 'Import statement completions',
      description:
        'Complete module names after “from”/“import”, and that module’s importable names after “from x import” — including on an empty prefix, so Tab lists what a package offers. Python only; reads the same indexed corpus as the symbol completions.',
      type: 'boolean',
      default: true,
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

// Cross-file locus follow: when the code locus points at a file that isn't open in
// any buffer (e.g. dash.code.set_locus, or the agent walking symbols.find results),
// open it. Already-open files are handled per-buffer — BufferView scrolls in place.
subscribeLocus(() => {
  const loc = getLocus();
  if (!loc.path || loc.source === 'editor') return;
  const uri = `workspace-file:${loc.path}`;
  if (!listBufferUris().includes(uri)) openBuffer(uri);
});

export { loadSource, saveSource, sourceTitle, type LoadedSource } from './sources';
export type { EditorService, BufferLanguage, OpenBufferRequest } from './service';
