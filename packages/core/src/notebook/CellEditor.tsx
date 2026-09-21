import { useEffect, useRef } from 'react';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { markdown } from '@codemirror/lang-markdown';
import { python } from '@codemirror/lang-python';
import { EditorState, Compartment, Prec, type Extension } from '@codemirror/state';
import { oneDark } from '@codemirror/theme-one-dark';
import { EditorView, keymap, placeholder } from '@codemirror/view';

import { docsHover, docsKeymap, renderDocEntry } from '../docs/cm-docs';
import type { DocSourceId } from '../docs/chain';

interface CellEditorProps {
  value: string;
  language: 'python' | 'markdown';
  onChange: (source: string) => void;
  /** Ctrl/Cmd+Enter: run (or, for markdown, render) and stay in this cell. */
  onRun: () => void;
  /**
   * Shift+Enter: run and **move on**. Falls back to `onRun` when a host has not
   * adopted the split yet, so the key never becomes dead.
   */
  onRunNext?: () => void;
  /**
   * Bump to put the caret in this editor. A number rather than a boolean because
   * focus is an *event*, and the same cell can be asked for it twice in a row
   * (Shift+Enter at the end of a notebook, then again in the cell it appended).
   * `0` means "never asked", so a freshly mounted cell does not steal focus.
   */
  focusToken?: number;
  /** Notebook path — lets the docs popup ask this cell's own kernel. */
  notebookPath?: string;
  /**
   * Extra CodeMirror extensions for this cell — how the notebook *modules* add
   * language-server support without the kit depending on the editor module (it
   * stays domain-neutral; see `EditorService.openNotebookLsp`).
   *
   * Held in a compartment because it arrives late: the server session resolves an
   * interpreter and a project root first, so a cell mounts before its extension
   * exists and must pick it up without being remounted (a remount would drop the
   * cell's undo history and the cursor mid-edit).
   */
  extraExtensions?: Extension[];
}

/**
 * Documentation sources for a cell with no language server attached, minus `lsp`:
 * the resolver is never registered here, so leaving it in would spend a round trip
 * before falling through to the ones that work.
 */
const CELL_DOC_SOURCES: DocSourceId[] = ['kernel', 'index', 'web'];

/**
 * …and for a cell that *does* have one (`extraExtensions`). The LSP extension runs
 * its own hover with the same index/web fallback behind it, so keeping those here
 * too would render two tooltips saying the same thing. `kernel` stays because it is
 * the one thing a language server cannot do: ask the live kernel what the object
 * actually is right now, which in a notebook is often the only real answer.
 */
const CELL_DOC_SOURCES_WITH_LSP: DocSourceId[] = ['kernel'];

/**
 * A compact CodeMirror editor for one notebook cell. Grows with content; the
 * document is pushed up through `onChange` on every edit (the pane debounces
 * the ws sync). Uses the same @codemirror packages the editor module does —
 * shared workspace deps, no cross-module import.
 */
export function CellEditor({
  value,
  language,
  onChange,
  onRun,
  onRunNext,
  focusToken = 0,
  notebookPath,
  extraExtensions,
}: CellEditorProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const callbacks = useRef({ onChange, onRun, onRunNext });
  callbacks.current = { onChange, onRun, onRunNext };
  // Read through a ref: the view is built once per cell mount, so a path captured
  // in the closure would go stale if the pane were ever rebound to another file.
  const pathRef = useRef(notebookPath);
  pathRef.current = notebookPath;
  // Same reason as `pathRef`: the docs callbacks are captured once, but whether this
  // cell has a language server flips when the session finishes coming up.
  const hasLspRef = useRef(false);
  hasLspRef.current = (extraExtensions?.length ?? 0) > 0;
  const docSources = (): DocSourceId[] =>
    hasLspRef.current ? CELL_DOC_SOURCES_WITH_LSP : CELL_DOC_SOURCES;
  const langCompartment = useRef(new Compartment());
  const extraCompartment = useRef(new Compartment());

  useEffect(() => {
    if (!hostRef.current) return;
    const view = new EditorView({
      parent: hostRef.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          history(),
          Prec.highest(
            keymap.of([
              {
                key: 'Mod-Enter',
                run: () => {
                  callbacks.current.onRun();
                  return true;
                },
              },
              {
                key: 'Shift-Enter',
                run: () => {
                  // The two keys were bound to the same callback, which made
                  // "run and advance" unexpressible and left Shift+Enter as a
                  // second name for Ctrl+Enter.
                  (callbacks.current.onRunNext ?? callbacks.current.onRun)();
                  return true;
                },
              },
            ]),
          ),
          // Before the default keymap: Shift-Tab is `indentLess` there, and the
          // docs lookup only claims the key when there is a symbol under the
          // cursor — so dedenting still works everywhere else.
          Prec.high(
            docsKeymap({
              notebookPath: () => pathRef.current,
              sources: docSources,
              show: (entry) => {
                const host = panelRef.current;
                if (!host) return;
                host.replaceChildren();
                if (entry) host.appendChild(renderDocEntry(entry));
                host.hidden = !entry;
              },
            }),
          ),
          docsHover({
            notebookPath: () => pathRef.current,
            sources: docSources,
          }),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          langCompartment.current.of(language === 'markdown' ? markdown() : python()),
          extraCompartment.current.of(extraExtensions ?? []),
          oneDark,
          placeholder('# …'),
          EditorView.lineWrapping,
          EditorView.updateListener.of((update) => {
            if (update.docChanged) callbacks.current.onChange(update.state.doc.toString());
          }),
          EditorView.theme({
            '&': { fontSize: 'var(--fs-body)', backgroundColor: 'transparent' },
            '.cm-content': { fontFamily: 'var(--font-mono, monospace)', padding: '0.35rem 0' },
            '&.cm-focused': { outline: 'none' },
          }),
        ],
      }),
    });
    viewRef.current = view;
    return () => {
      viewRef.current = null;
      view.destroy();
    };
    // The view is created once per cell mount; value changes from outside
    // (agent edits, cells_changed) are reconciled in the effect below.
  }, []);

  // Reconcile external document replacement without recreating the view.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current !== value) {
      view.dispatch({ changes: { from: 0, to: current.length, insert: value } });
    }
  }, [value]);

  useEffect(() => {
    viewRef.current?.dispatch({
      effects: langCompartment.current.reconfigure(language === 'markdown' ? markdown() : python()),
    });
  }, [language]);

  // Focus on request. The view is built once per mount and held in `viewRef`, so
  // this costs no remount and keeps the cell's undo history and cursor.
  useEffect(() => {
    if (focusToken > 0) viewRef.current?.focus();
  }, [focusToken]);

  useEffect(() => {
    viewRef.current?.dispatch({
      effects: extraCompartment.current.reconfigure(extraExtensions ?? []),
    });
  }, [extraExtensions]);

  return (
    <>
      <div ref={hostRef} />
      {/* The Shift-Tab panel. Hidden until asked for, and dismissed by clicking it
          — a docs pane that only closes on a second Shift-Tab is a pane people
          leave open by accident. */}
      <div
        ref={panelRef}
        className="cell-docs-panel"
        hidden
        onClick={(e) => {
          e.currentTarget.replaceChildren();
          e.currentTarget.hidden = true;
        }}
      />
    </>
  );
}
