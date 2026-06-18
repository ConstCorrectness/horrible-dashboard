/**
 * A single editor buffer: CodeMirror 6 over a URI source (see sources.ts). Reads
 * its `source` from pane params, loads the content, and saves back through the
 * source model. One panel instance per open buffer; reopening the same source
 * focuses the existing instance (stable instance id in index.tsx).
 *
 * The Mod-s keymap saves directly here; C3 routes editing commands through the
 * shell keybinding service. See docs/modules/editor.md.
 */
import { useEffect, useRef, useState } from 'react';
import { basicSetup, EditorView } from 'codemirror';
import { Compartment, EditorState } from '@codemirror/state';
import { markdown } from '@codemirror/lang-markdown';
import { javascript } from '@codemirror/lang-javascript';
import { python } from '@codemirror/lang-python';
import { oneDark } from '@codemirror/theme-one-dark';
import { unifiedMergeView } from '@codemirror/merge';

import { useAgentContext } from '../../agent-context';
import { ApiError } from '../../api';
import { registry } from '../../registry';
import { usePaneParams } from '../../panes';
import { useSetting } from '../../settings';
import { completeCode } from '../agent/api';
import { autosuggest } from './autosuggest';
import { registerBuffer, type BufferSnapshot } from './buffers';
import { openBuffer, setActiveBufferSource } from './index';
import { dirOf, lspExtension, lspLanguageId } from './lsp';
import { loadSource, saveSource } from './sources';

const FILE_URI = 'workspace-file:';

/** Open a workspace file (go-to-definition target) and reveal it in the tree. The
 * reveal runs once the new buffer is the active one. */
function goToFile(path: string): void {
  openBuffer(`${FILE_URI}${path}`);
  setTimeout(() => void registry.runCommand('files.revealActiveBuffer'), 150);
}

/** The LSP extension for a just-loaded source, or `[]` when there's no language
 * server for it (only `workspace-file:` buffers with a known language get one). */
function lspFor(source: string | null, title: string) {
  if (!source || !source.startsWith(FILE_URI)) return [];
  const language = lspLanguageId(title);
  if (!language) return [];
  const path = source.slice(FILE_URI.length);
  return lspExtension({ path, languageId: language, root: dirOf(path), openFile: goToFile });
}

function languageFor(title: string) {
  if (/\.(tsx?|jsx?|mjs|cjs)$/i.test(title)) {
    return javascript({ typescript: /\.tsx?$/i.test(title), jsx: /x$/i.test(title) });
  }
  if (/\.py$/i.test(title)) return python();
  return markdown();
}

/** A coarse language hint for the completion prompt. */
function languageHint(title: string): string {
  if (/\.tsx?$/i.test(title)) return 'TypeScript';
  if (/\.jsx?$|\.mjs$|\.cjs$/i.test(title)) return 'JavaScript';
  return 'Markdown';
}

export function BufferView() {
  const params = usePaneParams();
  const source = typeof params.source === 'string' ? params.source : null;

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
          langRef.current.of(markdown()),
          mergeRef.current.of([]),
          autoRef.current.of([]),
          lspRef.current.of([]),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) setDirty(true);
            // Track the focused buffer as "active" (Mod-s etc. route through the
            // shell keybinding service → editor.save, never a hardcoded handler).
            if (u.focusChanged && u.view.hasFocus && source) setActiveBufferSource(source);
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
    let cancelled = false;
    setStatus('Loading…');
    void loadSource(source)
      .then((loaded) => {
        const view = viewRef.current;
        if (cancelled || !view) return;
        revisionRef.current = loaded.revision;
        setTitle(loaded.title);
        view.dispatch({
          changes: { from: 0, to: view.state.doc.length, insert: loaded.content },
          effects: [
            langRef.current.reconfigure(languageFor(loaded.title)),
            // Connect (or disconnect) a language server for this buffer. The
            // reconfigure tears down any prior session (didClose + stop) and the
            // new plugin sees the just-applied content for its didOpen.
            lspRef.current.reconfigure(lspFor(source, loaded.title)),
          ],
        });
        setDirty(false);
        setStatus(null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setStatus(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
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
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: content },
      effects: mergeRef.current.reconfigure(unifiedMergeView({ original: originalRef.current })),
    });
    setProposing(true);
  };
  // The controller registers once per source; route through a ref so it always
  // calls the current `propose` (which closes over live `dirty`/`proposing`).
  const proposeRef = useRef(propose);
  proposeRef.current = propose;

  const closeProposal = (revert: boolean) => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      ...(revert
        ? { changes: { from: 0, to: view.state.doc.length, insert: originalRef.current } }
        : {}),
      effects: mergeRef.current.reconfigure([]),
    });
    // Accept leaves changes in place (dirty); Decline restores the pre-proposal state.
    setDirty(revert ? dirtyBeforeProposalRef.current : true);
    setProposing(false);
  };

  // A live snapshot for the agent (kept current via a ref reassigned each render).
  const snapshotRef = useRef<() => BufferSnapshot>(() => ({
    uri: '(unsaved)',
    title,
    content: '',
    dirty,
    selection: { from: 0, to: 0, text: '' },
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
            fetch: (prefix, suffix, signal) =>
              completeCode(prefix, suffix, languageHint(title), signal),
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
