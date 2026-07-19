/**
 * The SQL console's editor: a small controlled CodeMirror wrapper with
 * schema-aware completion from `@codemirror/lang-sql`. The schema/dialect pair
 * lives in a compartment so a connection switch reconfigures the live view
 * instead of remounting it.
 */
import { useEffect, useRef } from 'react';
import { autocompletion, closeBrackets, completionKeymap } from '@codemirror/autocomplete';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { MySQL, PostgreSQL, SQLite, StandardSQL, sql, type SQLDialect } from '@codemirror/lang-sql';
import { Compartment, EditorState, Prec } from '@codemirror/state';
import { oneDark } from '@codemirror/theme-one-dark';
import { EditorView, drawSelection, keymap, placeholder } from '@codemirror/view';
import type { SchemaResponse } from './api';
import { schemaToSqlConfig } from './sqlSchema';

// duckdb speaks postgres-flavored SQL; PostgreSQL is the nearest dialect lang-sql has.
const DIALECTS: Record<string, SQLDialect> = {
  postgres: PostgreSQL,
  mysql: MySQL,
  sqlite: SQLite,
  duckdb: PostgreSQL,
};

interface SqlEditorProps {
  value: string;
  onChange: (value: string) => void;
  onRun: () => void;
  provider: string | null;
  schema: SchemaResponse | null;
}

export function SqlEditor({ value, onChange, onRun, provider, schema }: SqlEditorProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const sqlConfRef = useRef(new Compartment());
  const onRunRef = useRef(onRun);
  onRunRef.current = onRun;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const initialValueRef = useRef(value);

  // Mount once; config changes flow through the compartment below.
  useEffect(() => {
    if (!hostRef.current) return;
    const view = new EditorView({
      parent: hostRef.current,
      state: EditorState.create({
        doc: initialValueRef.current,
        extensions: [
          history(),
          drawSelection(),
          closeBrackets(),
          autocompletion(),
          Prec.high(
            keymap.of([
              {
                key: 'Mod-Enter',
                run: () => {
                  onRunRef.current();
                  return true;
                },
              },
            ]),
          ),
          keymap.of([...completionKeymap, ...defaultKeymap, ...historyKeymap]),
          placeholder('Write SQL, then press Ctrl/Cmd+Enter or Run…'),
          oneDark,
          EditorView.lineWrapping,
          sqlConfRef.current.of(sql()),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) onChangeRef.current(u.state.doc.toString());
          }),
        ],
      }),
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, []);

  // Reconfigure completion when the connection's schema or provider changes.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const { schema: schemaMap, defaultSchema } = schemaToSqlConfig(schema);
    view.dispatch({
      effects: sqlConfRef.current.reconfigure(
        sql({
          dialect: DIALECTS[provider ?? ''] ?? StandardSQL,
          schema: schemaMap,
          defaultSchema,
          upperCaseKeywords: true,
        }),
      ),
    });
  }, [schema, provider]);

  // External writes (history dropdown, table click) replace the whole doc; the
  // no-op case after an internal edit compares equal and dispatches nothing.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (value !== current) {
      view.dispatch({
        changes: { from: 0, to: current.length, insert: value },
        selection: { anchor: value.length },
      });
    }
  }, [value]);

  return <div ref={hostRef} className="dbc-editor dbc-editor-cm" />;
}
