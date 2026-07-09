import { useEffect, useRef } from 'react';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { markdown } from '@codemirror/lang-markdown';
import { python } from '@codemirror/lang-python';
import { EditorState, Compartment, Prec } from '@codemirror/state';
import { oneDark } from '@codemirror/theme-one-dark';
import { EditorView, keymap, placeholder } from '@codemirror/view';

interface CellEditorProps {
  value: string;
  language: 'python' | 'markdown';
  onChange: (source: string) => void;
  /** Ctrl/Cmd+Enter and Shift+Enter both run the cell. */
  onRun: () => void;
}

/**
 * A compact CodeMirror editor for one notebook cell. Grows with content; the
 * document is pushed up through `onChange` on every edit (the pane debounces
 * the ws sync). Uses the same @codemirror packages the editor module does —
 * shared workspace deps, no cross-module import.
 */
export function CellEditor({ value, language, onChange, onRun }: CellEditorProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const callbacks = useRef({ onChange, onRun });
  callbacks.current = { onChange, onRun };
  const langCompartment = useRef(new Compartment());

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
                  callbacks.current.onRun();
                  return true;
                },
              },
            ]),
          ),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          langCompartment.current.of(language === 'markdown' ? markdown() : python()),
          oneDark,
          placeholder('# …'),
          EditorView.lineWrapping,
          EditorView.updateListener.of((update) => {
            if (update.docChanged) callbacks.current.onChange(update.state.doc.toString());
          }),
          EditorView.theme({
            '&': { fontSize: '0.8rem', backgroundColor: 'transparent' },
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

  return <div ref={hostRef} />;
}
