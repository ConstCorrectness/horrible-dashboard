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
import { oneDark } from '@codemirror/theme-one-dark';

import { useAgentContext } from '../../agent-context';
import { ApiError } from '../../api';
import { usePaneParams } from '../../panes';
import { registerBuffer, type BufferSnapshot } from './buffers';
import { setActiveBufferSource } from './index';
import { loadSource, saveSource } from './sources';

function languageFor(title: string) {
  if (/\.(tsx?|jsx?|mjs|cjs)$/i.test(title)) {
    return javascript({ typescript: /\.tsx?$/i.test(title), jsx: /x$/i.test(title) });
  }
  return markdown();
}

export function BufferView() {
  const params = usePaneParams();
  const source = typeof params.source === 'string' ? params.source : null;

  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const langRef = useRef(new Compartment());
  const revisionRef = useRef<number | undefined>(undefined);

  const [title, setTitle] = useState(source ? '…' : 'Untitled');
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

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
          effects: langRef.current.reconfigure(languageFor(loaded.title)),
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
      save: () => save(),
    });
    // `save`/`snapshotRef` read current values via refs; only `source` re-registers.
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
      <div className="editor-cm" ref={hostRef} />
    </div>
  );
}
