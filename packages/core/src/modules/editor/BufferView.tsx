/**
 * A single editor buffer: CodeMirror 6 over a URI source (see sources.ts). Reads
 * its `source` from pane params, loads the content, and saves back through the
 * source model. One panel instance per open buffer; reopening the same source
 * focuses the existing instance (stable instance id in index.tsx).
 *
 * The Mod-s keymap saves directly here; C3 routes editing commands through the
 * shell keybinding service. See docs/modules/editor.md.
 */
import { useContext, useEffect, useRef, useState } from 'react';
import { basicSetup } from 'codemirror';
import { EditorView, keymap } from '@codemirror/view';
import { Compartment, EditorState, Prec } from '@codemirror/state';
import { indentWithTab } from '@codemirror/commands';
import { acceptCompletion } from '@codemirror/autocomplete';
import { markdown } from '@codemirror/lang-markdown';
import { javascript } from '@codemirror/lang-javascript';
import { python } from '@codemirror/lang-python';
import { oneDark } from '@codemirror/theme-one-dark';
import { unifiedMergeView } from '@codemirror/merge';

import {
  clearActiveContextInstance,
  PaneInstanceContext,
  setActiveContextInstance,
  useAgentContext,
} from '../../agent-context';
import { ApiError } from '../../api';
import { dialogs } from '../../dialogs';
import { registerCloseGuard, setPaneDirty } from '../../layout/close-guards';
import { getLocus, setLocus, subscribeLocus } from '../../locus';
import { registry } from '../../registry';
import { usePaneParams } from '../../panes';
import { useSetting } from '../../settings';
import { autosuggest } from './autosuggest';
import { registerBuffer, type BufferSnapshot } from './buffers';
import { openBuffer, setActiveBufferSource, setActiveSaveAs } from './index';
import { dirOf, lspExtension, lspLanguageId } from './lsp';
import { readDiagnostics } from './lsp-registry';
import { createNote, loadSource, saveSource } from './sources';
import { dbGhostFetch, indexBuffer, indexBufferNow } from './symbolCompletion';

const FILE_URI = 'workspace-file:';

interface UnsavedState {
  content: string;
  dirty: boolean;
  proposing: boolean;
  original?: string;
  dirtyBeforeProposal?: boolean;
}
const unsavedCache = new Map<string, UnsavedState>();

/** Open a workspace file (go-to-definition target) and reveal it in the tree. The
 * reveal runs once the new buffer is the active one. */
function goToFile(path: string): void {
  openBuffer(`${FILE_URI}${path}`);
  setTimeout(() => void registry.runCommand('files.revealActiveBuffer'), 150);
}

/** The intellisense knobs a buffer reads from settings, captured when it opens (the
 * LSP compartment is reconfigured on load, so changes take effect on reopen). */
interface IntellisenseSettings {
  /** `editor.pythonPath` — overrides the backend's auto-detected interpreter. */
  pythonPath?: string;
  /** `editor.frameworkImports` — the curated framework-import completions. */
  frameworkImports?: boolean;
  /** `editor.indexedSymbols` — the indexed stdlib/package symbols. */
  indexedSymbols?: boolean;
  /** `editor.completionWarmupMs` — cold-start wait before falling back. */
  warmupMs?: number;
  /** `editor.changeDebounceMs` — edit push debounce. */
  changeDebounceMs?: number;
  /** `editor.diagnostics` — render diagnostics in the gutter. */
  diagnostics?: boolean;
  /** `editor.hover` — hover tooltips. */
  hover?: boolean;
}

/** The LSP extension for a just-loaded source, or `[]` when there's no language
 * server for it (only `workspace-file:` buffers with a known language get one). */
function lspFor(source: string | null, title: string, settings: IntellisenseSettings) {
  if (!source || !source.startsWith(FILE_URI)) return [];
  const language = lspLanguageId(title);
  if (!language) return [];
  const path = source.slice(FILE_URI.length);
  return lspExtension({
    path,
    languageId: language,
    root: dirOf(path),
    bufferUri: source,
    openFile: goToFile,
    pythonPathOverride: settings.pythonPath || undefined,
    frameworkImports: settings.frameworkImports,
    indexedSymbols: settings.indexedSymbols,
    warmupMs: settings.warmupMs,
    changeDebounceMs: settings.changeDebounceMs,
    diagnostics: settings.diagnostics,
    hover: settings.hover,
  });
}

function languageFor(title: string) {
  if (/\.(tsx?|jsx?|mjs|cjs)$/i.test(title)) {
    return javascript({ typescript: /\.tsx?$/i.test(title), jsx: /x$/i.test(title) });
  }
  if (/\.py$/i.test(title)) return python();
  return markdown();
}

/** An explicit language hint (e.g. a note opened by the visualizer) wins over the
 * title-based guess, since notes have no extension. */
function languageForHint(hint: string | null, title: string) {
  if (hint === 'javascript') return javascript();
  if (hint === 'python') return python();
  return languageFor(title);
}

export function BufferView() {
  const params = usePaneParams();
  const source = typeof params.source === 'string' ? params.source : null;
  const langHint = typeof params.language === 'string' ? params.language : null;
  const instanceId = useContext(PaneInstanceContext);

  // The workspace file path this buffer edits (locus is keyed by path), or null for
  // note/scratch buffers that have no place in the code tree. Refs so the mount-time
  // CodeMirror listener always reads the live value.
  const filePath = source && source.startsWith(FILE_URI) ? source.slice(FILE_URI.length) : null;
  const filePathRef = useRef(filePath);
  filePathRef.current = filePath;
  const lastLocusLineRef = useRef<number | null>(null);
  // Scroll+select the current locus if it targets this file. A stable ref so both the
  // locus subscription and the load effect (once content has arrived) call the latest;
  // it reads everything live, so calling a slightly stale reference is fine.
  const applyLocusRef = useRef<() => void>(() => {});

  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const langRef = useRef(new Compartment());
  const mergeRef = useRef(new Compartment());
  const autoRef = useRef(new Compartment());
  const lspRef = useRef(new Compartment());
  // Read-only-ness arrives with the content (Drive files, GitHub blobs), so it's a
  // compartment reconfigured on load rather than a mount-time extension.
  const readOnlyRef = useRef(new Compartment());
  const revisionRef = useRef<number | undefined>(undefined);
  // The buffer content captured when a proposal opens, restored on Decline.
  const originalRef = useRef('');
  const dirtyBeforeProposalRef = useRef(false);

  const [title, setTitle] = useState(source ? '…' : 'Untitled');
  const [dirty, setDirty] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  const [proposing, setProposing] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const autosuggestOn = useSetting<boolean>('editor.autosuggest') ?? false;
  // Interpreter for basedpyright (Python third-party import completions); overrides
  // the backend's auto-detected one. Captured when a buffer opens (see the load
  // effect) — changing it takes effect on reopen.
  const pythonPath = useSetting<string>('editor.pythonPath') ?? '';
  // Curated framework-import suggestions (see pythonImports.ts); on by default.
  const frameworkImports = useSetting<boolean>('editor.frameworkImports') ?? true;
  // The rest of the intellisense knobs, all captured on open like `pythonPath`.
  const indexedSymbols = useSetting<boolean>('editor.indexedSymbols') ?? true;
  const warmupMs = useSetting<number>('editor.completionWarmupMs') ?? 2000;
  const changeDebounceMs = useSetting<number>('editor.changeDebounceMs') ?? 300;
  const diagnosticsOn = useSetting<boolean>('editor.diagnostics') ?? true;
  const hoverOn = useSetting<boolean>('editor.hover') ?? true;

  // Save As, published to the module so `/save-as` can reach the active buffer.
  // Assigned each render (below, once `saveAs` is defined) and cleared on unmount
  // so a closed buffer's Save As can't outlive it.
  const saveAsRef = useRef<() => Promise<boolean>>(() => Promise.resolve(false));
  useEffect(() => () => setActiveSaveAs(null), []);
  const sourceRef = useRef(source);
  sourceRef.current = source;
  const instanceIdRef = useRef(instanceId);
  instanceIdRef.current = instanceId;
  const isProgrammaticRef = useRef(false);
  // The LSP language id of the loaded buffer ('' when unknown), read live by the
  // mount-time change listener so it can push edits to the symbol index.
  const indexLangRef = useRef('');
  // Live dirty/title, read by the close guard (registered once, called on close).
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const titleRef = useRef(title);
  titleRef.current = title;

  // Mount CodeMirror once.
  useEffect(() => {
    if (!hostRef.current) return;
    const view = new EditorView({
      parent: hostRef.current,
      state: EditorState.create({
        doc: '',
        extensions: [
          basicSetup,
          oneDark,
          // Tab does editor work instead of moving focus to the next control —
          // `basicSetup` deliberately leaves Tab unbound (accessibility), which is why
          // it otherwise escapes to the browser's focus traversal. With a completion
          // popup open Tab accepts it (Enter-style, like VS Code); otherwise it indents,
          // and Shift-Tab dedents. Ghost-text autosuggest binds Tab at `Prec.highest`,
          // so an inline suggestion is still accepted first when one is showing.
          Prec.high(keymap.of([{ key: 'Tab', run: acceptCompletion }, indentWithTab])),
          langRef.current.of(markdown()),
          mergeRef.current.of([]),
          autoRef.current.of([]),
          lspRef.current.of([]),
          readOnlyRef.current.of([]),
          EditorView.updateListener.of((u) => {
            if (u.docChanged && !isProgrammaticRef.current) {
              setDirty(true);
              const src = sourceRef.current;
              if (src) {
                const content = u.state.doc.toString();
                unsavedCache.set(src, {
                  content,
                  dirty: true,
                  proposing: false, // user edits drop proposing state in our cache model
                });
                // Keep the buffer's symbols current in the completion index
                // (debounced; harvest is a no-op for languages we don't parse yet).
                if (indexLangRef.current) indexBuffer(src, indexLangRef.current, content);
              }
            }
            // Track the focused buffer as "active" (Mod-s etc. route through the
            // shell keybinding service → editor.save, never a hardcoded handler;
            // the agent attaches this instance's snapshot to a turn so it can alter
            // the open code without a discovery round-trip).
            if (u.focusChanged && u.view.hasFocus) {
              const src = sourceRef.current;
              if (src) setActiveBufferSource(src);
              if (instanceIdRef.current) setActiveContextInstance(instanceIdRef.current);
              // Same "active buffer" moment: publish this buffer's Save As so
              // `/save-as` in the minibuffer reaches the one you're looking at.
              setActiveSaveAs(() => saveAsRef.current());
            }
            // Publish the cursor to the code-locus bus (so the outline follows) — on
            // focus, and when the cursor changes line. Throttled to line granularity;
            // only workspace files have a locus path.
            const path = filePathRef.current;
            if (path && (u.selectionSet || (u.focusChanged && u.view.hasFocus))) {
              const head = u.state.selection.main.head;
              const line = u.state.doc.lineAt(head);
              if (lastLocusLineRef.current !== line.number || u.focusChanged) {
                lastLocusLineRef.current = line.number;
                const pos = { line: line.number, column: head - line.from + 1 };
                setLocus({ path, range: { start: pos, end: pos } }, 'editor');
              }
            }
          }),
        ],
      }),
    });
    viewRef.current = view;
    return () => view.destroy();
  }, []);

  // Load (or reload) the source content into the editor.
  useEffect(() => {
    if (!source) {
      setTitle('Untitled');
      return;
    }
    setActiveBufferSource(source);
    setActiveSaveAs(() => saveAsRef.current());
    if (instanceId) setActiveContextInstance(instanceId);
    let cancelled = false;
    setStatus('Loading…');
    void loadSource(source)
      .then((loaded) => {
        const view = viewRef.current;
        if (cancelled || !view) return;
        revisionRef.current = loaded.revision;
        setTitle(loaded.title);
        setReadOnly(loaded.readOnly ?? false);

        let insertContent = loaded.content;
        let initialDirty = false;
        let isProposing = false;

        const src = sourceRef.current;
        if (src) {
          const cached = unsavedCache.get(src);
          if (cached) {
            if (cached.dirty || cached.proposing) {
              insertContent = cached.content;
              initialDirty = cached.dirty;
            }
            if (cached.proposing) {
              isProposing = true;
              originalRef.current = cached.original || '';
              dirtyBeforeProposalRef.current = cached.dirtyBeforeProposal || false;
            }
          }
        }

        isProgrammaticRef.current = true;
        view.dispatch({
          changes: { from: 0, to: view.state.doc.length, insert: insertContent },
          effects: [
            langRef.current.reconfigure(languageForHint(langHint, loaded.title)),
            // Connect (or disconnect) a language server for this buffer. The
            // reconfigure tears down any prior session (didClose + stop) and the
            // new plugin sees the just-applied content for its didOpen.
            lspRef.current.reconfigure(
              lspFor(source, loaded.title, {
                pythonPath,
                frameworkImports,
                indexedSymbols,
                warmupMs,
                changeDebounceMs,
                diagnostics: diagnosticsOn,
                hover: hoverOn,
              }),
            ),
            readOnlyRef.current.reconfigure(EditorState.readOnly.of(loaded.readOnly ?? false)),
            ...(isProposing
              ? [mergeRef.current.reconfigure(unifiedMergeView({ original: originalRef.current }))]
              : []),
          ],
        });
        isProgrammaticRef.current = false;

        // Seed the completion index with this buffer's symbols right away, and
        // remember its language for the live change listener above.
        const indexLang = lspLanguageId(loaded.title) ?? '';
        indexLangRef.current = indexLang;
        if (indexLang) indexBufferNow(source, indexLang, insertContent);

        setDirty(initialDirty);
        setProposing(isProposing);
        setStatus(null);
        // Content is now in the doc — re-apply any pending locus so a jump that opened
        // this file (its scroll fired against an empty doc) lands on the right line.
        applyLocusRef.current();
      })
      .catch((err: unknown) => {
        if (!cancelled) setStatus(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [source]);

  // Drop the active-context marker when this buffer unmounts, so a closed pane
  // doesn't keep feeding stale content to the agent.
  useEffect(() => {
    if (!instanceId) return;
    return () => clearActiveContextInstance(instanceId);
  }, [instanceId]);

  // Follow the code locus: when another pane (outline, dash, agent — anything but
  // this editor) points the locus at this file, scroll to and select the range. The
  // `source !== 'editor'` guard is the echo break — we ignore loci we published.
  applyLocusRef.current = () => {
    const view = viewRef.current;
    const path = filePathRef.current;
    const loc = getLocus();
    if (!view || !path || loc.source === 'editor' || loc.path !== path || !loc.range) return;
    const doc = view.state.doc;
    const startLine = Math.min(Math.max(loc.range.start.line, 1), doc.lines);
    const endLine = Math.min(Math.max(loc.range.end.line, 1), doc.lines);
    const from = doc.line(startLine).from;
    const to = doc.line(endLine).to;
    lastLocusLineRef.current = startLine; // don't echo this jump back as our own move
    view.dispatch({
      selection: { anchor: from, head: to },
      effects: EditorView.scrollIntoView(from, { y: 'center' }),
    });
  };
  useEffect(() => {
    const apply = () => applyLocusRef.current();
    apply(); // apply whatever the locus already is when this buffer (re)mounts
    return subscribeLocus(apply);
  }, [source]);

  // Returns whether the buffer was saved — the close guard uses this to decide
  // whether closing may proceed (a failed save must not silently discard work).
  const save = async (): Promise<boolean> => {
    const view = viewRef.current;
    if (!view) return false;
    if (!source) {
      // A sourceless scratch buffer has nowhere to save in place — Save As it.
      return saveAs();
    }
    try {
      setStatus('Saving…');
      const res = await saveSource(source, view.state.doc.toString(), revisionRef.current);
      if (res.revision !== undefined) revisionRef.current = res.revision;
      setDirty(false);
      unsavedCache.delete(source);
      setStatus('Saved');
      setTimeout(() => setStatus((s) => (s === 'Saved' ? null : s)), 1200);
      return true;
    } catch (err) {
      setStatus(
        err instanceof ApiError && err.status === 409
          ? 'Conflict — file changed since load; reload to merge'
          : err instanceof Error
            ? err.message
            : String(err),
      );
      return false;
    }
  };

  // Save the current content to a **new** destination (VS Code's "Save As…"):
  // a workspace file path for file buffers, or a new note otherwise. Writes a
  // copy and leaves this buffer's own source untouched — used from the close
  // prompt and for sourceless buffers that have nowhere else to go. Returns
  // whether it saved.
  const saveAs = async (): Promise<boolean> => {
    const view = viewRef.current;
    if (!view) return false;
    const content = view.state.doc.toString();
    const FILE = 'workspace-file:';
    try {
      if (source && source.startsWith(FILE)) {
        const dest = await dialogs.prompt({
          title: 'Save As',
          message: 'Path to save a copy to',
          defaultValue: source.slice(FILE.length),
          confirmLabel: 'Save',
        });
        const path = dest?.trim();
        if (!path) return false;
        setStatus('Saving…');
        await saveSource(`${FILE}${path}`, content);
      } else {
        const name = await dialogs.prompt({
          title: 'Save As note',
          message: 'Title for the new note',
          defaultValue: title === 'Untitled' ? '' : title,
          placeholder: 'Untitled',
          confirmLabel: 'Save',
        });
        const noteTitle = name?.trim();
        if (!noteTitle) return false;
        setStatus('Saving…');
        await createNote(noteTitle, content);
      }
      setStatus('Saved a copy');
      setTimeout(() => setStatus((s) => (s === 'Saved a copy' ? null : s)), 1500);
      return true;
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
      return false;
    }
  };

  // Held in a ref so the focus handler above (registered once, on mount) always
  // publishes the *current* closure rather than the one from first render.
  saveAsRef.current = saveAs;
  // Show an agent-proposed edit as an inline diff (original vs proposed). The user
  // reviews per-chunk in the gutter, then Accepts (keep) or Declines (revert).
  const propose = (content: string) => {
    const view = viewRef.current;
    if (!view) return;
    if (!proposing) {
      originalRef.current = view.state.doc.toString();
      dirtyBeforeProposalRef.current = dirty;
    }
    isProgrammaticRef.current = true;
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: content },
      effects: mergeRef.current.reconfigure(unifiedMergeView({ original: originalRef.current })),
    });
    isProgrammaticRef.current = false;
    setProposing(true);

    const src = sourceRef.current;
    if (src) {
      unsavedCache.set(src, {
        content,
        dirty, // we retain the dirty state it had, or whatever it is, but it's part of proposal
        proposing: true,
        original: originalRef.current,
        dirtyBeforeProposal: dirtyBeforeProposalRef.current,
      });
    }
  };
  // The controller registers once per source; route through a ref so it always
  // calls the current `propose` (which closes over live `dirty`/`proposing`).
  const proposeRef = useRef(propose);
  proposeRef.current = propose;

  const closeProposal = (revert: boolean) => {
    const view = viewRef.current;
    if (!view) return;
    isProgrammaticRef.current = true;
    view.dispatch({
      ...(revert
        ? { changes: { from: 0, to: view.state.doc.length, insert: originalRef.current } }
        : {}),
      effects: mergeRef.current.reconfigure([]),
    });
    isProgrammaticRef.current = false;
    // Accept leaves changes in place (dirty); Decline restores the pre-proposal state.
    const newDirty = revert ? dirtyBeforeProposalRef.current : true;
    setDirty(newDirty);
    setProposing(false);

    const src = sourceRef.current;
    if (src) {
      unsavedCache.set(src, {
        content: view.state.doc.toString(),
        dirty: newDirty,
        proposing: false,
      });
    }
  };

  // Close guard: an unsaved buffer prompts before it's removed. Mirrors VS Code's
  // prompt exactly — Save (default) / Don't Save / Cancel. An untitled buffer has
  // nowhere to write, so its Save runs the Save As flow (`save()` falls through);
  // VS Code labels that button "Save" too rather than offering a separate one.
  // Returns whether the close proceeds.
  const closeGuard = async (): Promise<boolean> => {
    if (!dirtyRef.current) return true;
    const choice = await dialogs.choice({
      title: `Do you want to save the changes you made to ${titleRef.current}?`,
      message: "Your changes will be lost if you don't save.",
      buttons: [
        { label: 'Save', value: 'save', primary: true },
        { label: "Don't Save", value: 'dontSave' },
        { label: 'Cancel', value: 'cancel' },
      ],
      cancelValue: 'cancel',
    });
    switch (choice) {
      case 'save':
        return save();
      case 'dontSave':
        return true;
      default:
        return false; // Cancel / Esc / backdrop — keep the buffer open
    }
  };
  const closeGuardRef = useRef(closeGuard);
  closeGuardRef.current = closeGuard;

  // Register the guard for this pane instance (the frame's close paths run it),
  // and flag the buffer dirty for the app-exit (beforeunload) warning.
  useEffect(() => {
    if (!instanceId) return;
    return registerCloseGuard(instanceId, () => closeGuardRef.current());
  }, [instanceId]);
  useEffect(() => {
    const id = instanceId ?? source;
    if (!id) return;
    setPaneDirty(id, dirty);
    return () => setPaneDirty(id, false);
  }, [instanceId, source, dirty]);

  // A live snapshot for the agent (kept current via a ref reassigned each render).
  const snapshotRef = useRef<() => BufferSnapshot>(() => ({
    uri: '(unsaved)',
    title,
    content: '',
    dirty,
    selection: { from: 0, to: 0, text: '' },
    diagnostics: [],
  }));
  snapshotRef.current = () => {
    const view = viewRef.current;
    const sel = view?.state.selection.main;
    return {
      uri: source ?? '(unsaved)',
      title,
      content: view?.state.doc.toString() ?? '',
      dirty,
      selection:
        view && sel
          ? { from: sel.from, to: sel.to, text: view.state.doc.sliceString(sel.from, sel.to) }
          : { from: 0, to: 0, text: '' },
      diagnostics: source ? readDiagnostics(source) : [],
    };
  };

  // Read path: the agent pulls this buffer's snapshot on demand.
  useAgentContext(() => snapshotRef.current());

  // Inline autosuggest: enabled live by the setting, suppressed while reviewing a
  // proposed edit (the diff owns the buffer then). Reconfigured via a compartment.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const ext =
      autosuggestOn && !proposing
        ? autosuggest({
            // Ghost text is a prefix lookup into the DB symbol index — the same
            // index the dropdown uses — not a model. It returns the tail of the
            // single best-matching symbol for the token at the cursor, so there's
            // no round-trip latency and no hallucinated multi-line guesses.
            fetch: (prefix, suffix) => dbGhostFetch(prefix, suffix, lspLanguageId(title) ?? ''),
          })
        : [];
    view.dispatch({ effects: autoRef.current.reconfigure(ext) });
  }, [autosuggestOn, proposing, title]);

  // Write path: register a controller so `editor.applyEdit`/`editor.save` (gated,
  // type-level tools) can act on this buffer instance by URI.
  useEffect(() => {
    if (!source) return;
    return registerBuffer(source, {
      snapshot: () => snapshotRef.current(),
      setContent: (content) => {
        const view = viewRef.current;
        if (view) {
          view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: content } });
        }
      },
      propose: (content) => proposeRef.current(content),
      save: () => save().then(() => undefined),
    });
    // `save`/`snapshotRef`/`propose` read current values via refs; only `source` re-registers.
  }, [source]);

  return (
    <div className="editor-buffer">
      <div className="editor-header">
        <span className="editor-title">
          {title}
          {dirty ? ' •' : ''}
        </span>
        <span className="editor-status">{status}</span>
        {readOnly ? (
          <span className="editor-readonly" title="This source can't be written back">
            Read-only
          </span>
        ) : (
          <button className="editor-save" onClick={() => void save()}>
            Save
          </button>
        )}
      </div>
      {proposing && (
        <div className="editor-proposal">
          <span className="editor-proposal-label">⤳ Agent proposed an edit — review the diff</span>
          <button className="editor-proposal-accept" onClick={() => closeProposal(false)}>
            Accept
          </button>
          <button className="editor-proposal-decline" onClick={() => closeProposal(true)}>
            Decline
          </button>
        </div>
      )}
      <div className="editor-cm" ref={hostRef} />
    </div>
  );
}
