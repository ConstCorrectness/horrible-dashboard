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
import { getLocus, setLocus, subscribeLocus } from '../../locus';
import { registry } from '../../registry';
import { usePaneParams } from '../../panes';
import { useSetting } from '../../settings';
import { completeCode } from '../agent/api';
import { autosuggest } from './autosuggest';
import { registerBuffer, type BufferSnapshot } from './buffers';
import { openBuffer, setActiveBufferSource } from './index';
import { dirOf, lspExtension, lspLanguageId } from './lsp';
import { getLspClient, readDiagnostics } from './lsp-registry';
import { loadSource, saveSource } from './sources';

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

/** The LSP extension for a just-loaded source, or `[]` when there's no language
 * server for it (only `workspace-file:` buffers with a known language get one).
 * `pythonPath` (the `editor.pythonPath` setting) overrides the backend's auto-detected
 * interpreter so third-party imports resolve; `frameworkImports` toggles the curated
 * framework-import completions. */
function lspFor(
  source: string | null,
  title: string,
  pythonPath?: string,
  frameworkImports?: boolean,
) {
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
    pythonPathOverride: pythonPath || undefined,
    frameworkImports,
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

/** A coarse language hint for the completion prompt. */
function languageHint(title: string): string {
  if (/\.tsx?$/i.test(title)) return 'TypeScript';
  if (/\.jsx?$|\.mjs$|\.cjs$/i.test(title)) return 'JavaScript';
  if (/\.py$/i.test(title)) return 'Python';
  if (/\.rs$/i.test(title)) return 'Rust';
  return 'Markdown';
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
  const revisionRef = useRef<number | undefined>(undefined);
  // The buffer content captured when a proposal opens, restored on Decline.
  const originalRef = useRef('');
  const dirtyBeforeProposalRef = useRef(false);

  const [title, setTitle] = useState(source ? '…' : 'Untitled');
  const [dirty, setDirty] = useState(false);
  const [proposing, setProposing] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const autosuggestOn = useSetting<boolean>('editor.autosuggest') ?? false;
  // Interpreter for basedpyright (Python third-party import completions); overrides
  // the backend's auto-detected one. Captured when a buffer opens (see the load
  // effect) — changing it takes effect on reopen.
  const pythonPath = useSetting<string>('editor.pythonPath') ?? '';
  // Curated framework-import suggestions (see pythonImports.ts); on by default.
  const frameworkImports = useSetting<boolean>('editor.frameworkImports') ?? true;

  const sourceRef = useRef(source);
  sourceRef.current = source;
  const instanceIdRef = useRef(instanceId);
  instanceIdRef.current = instanceId;
  const isProgrammaticRef = useRef(false);

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
          EditorView.updateListener.of((u) => {
            if (u.docChanged && !isProgrammaticRef.current) {
              setDirty(true);
              const src = sourceRef.current;
              if (src) {
                unsavedCache.set(src, {
                  content: u.state.doc.toString(),
                  dirty: true,
                  proposing: false, // user edits drop proposing state in our cache model
                });
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
    if (instanceId) setActiveContextInstance(instanceId);
    let cancelled = false;
    setStatus('Loading…');
    void loadSource(source)
      .then((loaded) => {
        const view = viewRef.current;
        if (cancelled || !view) return;
        revisionRef.current = loaded.revision;
        setTitle(loaded.title);

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
            lspRef.current.reconfigure(lspFor(source, loaded.title, pythonPath, frameworkImports)),
            ...(isProposing
              ? [mergeRef.current.reconfigure(unifiedMergeView({ original: originalRef.current }))]
              : []),
          ],
        });
        isProgrammaticRef.current = false;

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

  const save = async () => {
    const view = viewRef.current;
    if (!view || !source) {
      // A sourceless scratch buffer can't be saved yet (C3 adds "save as new note").
      setStatus(source ? null : 'Unsaved buffer — no source');
      return;
    }
    try {
      setStatus('Saving…');
      const res = await saveSource(source, view.state.doc.toString(), revisionRef.current);
      if (res.revision !== undefined) revisionRef.current = res.revision;
      setDirty(false);
      unsavedCache.delete(source);
      setStatus('Saved');
      setTimeout(() => setStatus((s) => (s === 'Saved' ? null : s)), 1200);
    } catch (err) {
      setStatus(
        err instanceof ApiError && err.status === 409
          ? 'Conflict — file changed since load; reload to merge'
          : err instanceof Error
            ? err.message
            : String(err),
      );
    }
  };
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
            fetch: async (prefix, suffix, signal) => {
              // Ground the prompt with L2 (LSP) context — the symbols in scope and
              // the type at the cursor — so the local model suggests code that
              // resolves instead of hallucinating. Only code buffers have a client;
              // notes fall back to the plain prefix/suffix prompt.
              const client = source ? getLspClient(source) : undefined;
              const grounding = client
                ? await client.grounding(prefix.length).catch(() => null)
                : null;
              return completeCode(
                {
                  prefix,
                  suffix,
                  language: languageHint(title),
                  completions: grounding?.completions,
                  hover: grounding?.hover || undefined,
                },
                signal,
              );
            },
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
      save: () => save(),
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
        <button className="editor-save" onClick={() => void save()}>
          Save
        </button>
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
