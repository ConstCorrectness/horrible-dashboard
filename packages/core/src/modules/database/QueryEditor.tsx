/**
 * The database console's editor. One controlled CodeMirror wrapper serving both
 * query dialects:
 *
 * - `sql`  — schema-aware completion from `@codemirror/lang-sql`.
 * - `json` — vector-store operation bodies, with completion for the op names and
 *   field keys, plus the collections discovered by introspection.
 *
 * Both the language and the completion source live in compartments, so switching
 * connections reconfigures the live view rather than remounting it (a remount would
 * drop undo history and the cursor mid-edit).
 *
 * JSON is highlighted with `lang-javascript` rather than a JSON mode: JSON is a
 * subset of JS, the highlighting and bracket handling are correct, and it avoids
 * pulling in another CodeMirror language package for one pane.
 */
import { useEffect, useRef } from 'react';
import {
  autocompletion,
  closeBrackets,
  completionKeymap,
  type CompletionContext,
  type CompletionResult,
} from '@codemirror/autocomplete';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { javascript } from '@codemirror/lang-javascript';
import { MySQL, PostgreSQL, SQLite, StandardSQL, sql, type SQLDialect } from '@codemirror/lang-sql';
import { Compartment, EditorState, Prec } from '@codemirror/state';
import { oneDark } from '@codemirror/theme-one-dark';
import { EditorView, drawSelection, keymap, placeholder } from '@codemirror/view';
import type { SchemaResponse } from './api';
import { schemaToSqlConfig } from './sqlSchema';

// duckdb speaks postgres-flavored SQL; PostgreSQL is the nearest dialect lang-sql has.
// oracle has no lang-sql dialect — StandardSQL is closer than a wrong-flavored one.
const DIALECTS: Record<string, SQLDialect> = {
  postgres: PostgreSQL,
  mysql: MySQL,
  sqlite: SQLite,
  duckdb: PostgreSQL,
};

/** Ops and body keys of the shared vector query contract (drivers/vector_base.py). */
const VECTOR_OPS = [
  { label: 'search', info: 'Vector search — needs "query" (text) or "vector"' },
  { label: 'list', info: 'Scan a collection, optionally filtered by "where"' },
  { label: 'get', info: 'Fetch documents by "ids"' },
  { label: 'count', info: 'Row count for a collection' },
  { label: 'collections', info: 'List every collection in the store' },
  { label: 'describe', info: "A collection's columns / properties" },
  { label: 'peek', info: 'First few documents' },
  { label: 'upsert', info: 'Write documents (not read-only)' },
  { label: 'delete', info: 'Delete documents by "ids" (not read-only)' },
  { label: 'create_collection', info: 'Create a collection (not read-only)' },
  { label: 'drop_collection', info: 'Drop a collection (not read-only)' },
];

const VECTOR_KEYS = [
  { label: 'op', info: 'Which operation to run (required)' },
  { label: 'collection', info: 'Target collection name' },
  { label: 'query', info: 'Text to embed and search with' },
  { label: 'vector', info: 'Explicit query vector (array of numbers)' },
  { label: 'where', info: 'Metadata equality filter, e.g. {"kind": "note"}' },
  { label: 'ids', info: 'Document ids for get / delete' },
  { label: 'documents', info: 'Documents to upsert' },
  { label: 'limit', info: 'Max rows (default 10)' },
  { label: 'offset', info: 'Rows to skip' },
  { label: 'select', info: 'Restrict/order the result columns' },
  { label: 'metric', info: 'Distance metric override, e.g. "cosine" or "l2"' },
];

/**
 * Completion for JSON bodies. Context-sensitive in the one way that matters: after
 * `"op":` it offers ops, after a collection-ish key it offers real collection names,
 * and otherwise it offers body keys.
 */
function vectorCompletions(collections: string[]) {
  return (context: CompletionContext): CompletionResult | null => {
    const word = context.matchBefore(/[\w".]*/);
    if (!word || (word.from === word.to && !context.explicit)) return null;
    const before = context.state.sliceDoc(Math.max(0, word.from - 40), word.from);

    if (/"op"\s*:\s*"?$/.test(before)) {
      return {
        from: word.from,
        options: VECTOR_OPS.map((o) => ({ ...o, type: 'keyword' })),
      };
    }
    if (/"collection"\s*:\s*"?$/.test(before)) {
      return {
        from: word.from,
        options: collections.map((c) => ({ label: c, type: 'class' })),
      };
    }
    return {
      from: word.from,
      options: VECTOR_KEYS.map((k) => ({ ...k, type: 'property' })),
    };
  };
}

interface QueryEditorProps {
  value: string;
  onChange: (value: string) => void;
  onRun: () => void;
  provider: string | null;
  /** 'sql' or 'json' — which query surface this connection speaks. */
  dialect: string;
  schema: SchemaResponse | null;
}

export function QueryEditor({
  value,
  onChange,
  onRun,
  provider,
  dialect,
  schema,
}: QueryEditorProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const langConfRef = useRef(new Compartment());
  const completionConfRef = useRef(new Compartment());
  const placeholderConfRef = useRef(new Compartment());
  const onRunRef = useRef(onRun);
  onRunRef.current = onRun;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const initialValueRef = useRef(value);

  // Mount once; config changes flow through the compartments below.
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
          completionConfRef.current.of(autocompletion()),
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
          placeholderConfRef.current.of(placeholder('')),
          oneDark,
          EditorView.lineWrapping,
          langConfRef.current.of(sql()),
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

  // Reconfigure language + completion when the connection's dialect/schema changes.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const isJson = dialect === 'json';
    const collections = schema?.tables.map((t) => t.name) ?? [];
    const { schema: schemaMap, defaultSchema } = schemaToSqlConfig(schema);

    view.dispatch({
      effects: [
        langConfRef.current.reconfigure(
          isJson
            ? javascript()
            : sql({
                dialect: DIALECTS[provider ?? ''] ?? StandardSQL,
                schema: schemaMap,
                defaultSchema,
                upperCaseKeywords: true,
              }),
        ),
        completionConfRef.current.reconfigure(
          isJson
            ? autocompletion({ override: [vectorCompletions(collections)] })
            : autocompletion(),
        ),
        placeholderConfRef.current.reconfigure(
          placeholder(
            isJson
              ? 'Vector query, e.g. {"op": "search", "collection": "…", "query": "…"} — Ctrl/Cmd+Enter to run'
              : 'Write SQL, then press Ctrl/Cmd+Enter or Run…',
          ),
        ),
      ],
    });
  }, [schema, provider, dialect]);

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
