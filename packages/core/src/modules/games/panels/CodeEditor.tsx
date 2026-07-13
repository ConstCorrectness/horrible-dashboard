import { useEffect, useRef } from 'react';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { markdown } from '@codemirror/lang-markdown';
import { python } from '@codemirror/lang-python';
import { javascript } from '@codemirror/lang-javascript';
import { indentUnit } from '@codemirror/language';
import { EditorState, Compartment } from '@codemirror/state';
import { oneDark } from '@codemirror/theme-one-dark';
import { EditorView, keymap, placeholder } from '@codemirror/view';

interface CodeEditorProps {
  value: string;
  language: 'python' | 'json' | 'markdown' | 'text';
  onChange: (value: string) => void;
  placeholder?: string;
  minHeight?: string;
}

export function CodeEditor({
  value = '',
  language,
  onChange,
  placeholder: placeholderText,
  minHeight = '6rem',
}: CodeEditorProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const callbacks = useRef({ onChange });
  callbacks.current = { onChange };
  const langCompartment = useRef(new Compartment());

  const safeValue = value || '';

  useEffect(() => {
    if (!hostRef.current) return;

    const getLanguageExtension = (lang: string) => {
      switch (lang) {
        case 'python':
          return python();
        case 'json':
          return javascript();
        case 'markdown':
          return markdown();
        default:
          return [];
      }
    };

    const view = new EditorView({
      parent: hostRef.current,
      state: EditorState.create({
        doc: safeValue,
        extensions: [
          history(),
          keymap.of([indentWithTab, ...defaultKeymap, ...historyKeymap]),
          langCompartment.current.of(getLanguageExtension(language)),
          indentUnit.of('    '),
          oneDark,
          placeholder(placeholderText || ''),
          EditorView.lineWrapping,
          EditorView.updateListener.of((update) => {
            if (update.docChanged) {
              callbacks.current.onChange(update.state.doc.toString());
            }
          }),
          EditorView.theme({
            '&': {
              fontSize: '0.8rem',
              backgroundColor: 'var(--surface, #1c1c1c)',
              border: '1px solid var(--border, #33343a)',
              borderRadius: '4px',
              width: '100%',
            },
            '.cm-content': {
              fontFamily: 'var(--font-mono, monospace)',
              padding: '0.5rem 0',
            },
            '.cm-scroller': {
              minHeight: minHeight,
              fontFamily: 'var(--font-mono, monospace)',
            },
            '.cm-gutters': {
              backgroundColor: 'var(--surface, #1c1c1c)',
              color: 'var(--text-dim, #888)',
              borderRight: '1px solid var(--border, #33343a)',
              borderRadius: '4px 0 0 4px',
            },
            '&.cm-focused': {
              outline: 'none',
              borderColor: 'var(--accent, #6ea8fe)',
            },
          }),
        ],
      }),
    });

    viewRef.current = view;

    return () => {
      viewRef.current = null;
      view.destroy();
    };
  }, []);

  // Reconcile external document replacement without recreating the view.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current !== safeValue) {
      view.dispatch({ changes: { from: 0, to: current.length, insert: safeValue } });
    }
  }, [safeValue]);

  useEffect(() => {
    const getLanguageExtension = (lang: string) => {
      switch (lang) {
        case 'python':
          return python();
        case 'json':
          return javascript();
        case 'markdown':
          return markdown();
        default:
          return [];
      }
    };

    viewRef.current?.dispatch({
      effects: langCompartment.current.reconfigure(getLanguageExtension(language)),
    });
  }, [language]);

  return <div ref={hostRef} style={{ width: '100%' }} />;
}
