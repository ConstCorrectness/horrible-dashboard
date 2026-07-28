/**
 * The database console's editor. One controlled CodeMirror wrapper serving both
 * query dialects:
 *
 * - `sql`   — schema-aware completion from `@codemirror/lang-sql`.
 * - `json`  — vector-store operation bodies, with completion for the op names and
 *   field keys, plus the collections discovered by introspection.
 * - `mongo` — MongoDB operation bodies. Same JSON editor, different vocabulary: the
 *   ops and body keys are MQL's (`find`/`aggregate`, `filter`/`pipeline`), because
 *   offering `search`/`vector` at a Mongo collection would just mislead.
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

/** Ops and body keys of the MongoDB query contract (drivers/mongo_driver.py). */
const MONGO_OPS = [
  { label: 'find', info: 'Query documents with a "filter" (+ projection, sort, skip)' },
  { label: 'aggregate', info: 'Run a "pipeline" of aggregation stages' },
  { label: 'count', info: 'Count documents matching "filter"' },
  { label: 'distinct', info: 'Distinct values of "field"' },
  { label: 'collections', info: 'List collections in the database' },
  { label: 'databases', info: 'List databases in the cluster' },
  { label: 'describe', info: 'Fields and BSON types, sampled from documents' },
  { label: 'indexes', info: "A collection's indexes" },
  { label: 'stats', info: 'Database storage stats' },
  { label: 'insert', info: 'Insert "documents" (not read-only)' },
  { label: 'update', info: 'Update by "filter" with "update" (not read-only)' },
  { label: 'delete', info: 'Delete by "filter" (not read-only)' },
  { label: 'create_collection', info: 'Create a collection (not read-only)' },
  { label: 'drop_collection', info: 'Drop a collection (not read-only)' },
  { label: 'command', info: 'Run a raw database command (not read-only)' },
];

const MONGO_KEYS = [
  { label: 'op', info: 'Which operation to run (required)' },
  { label: 'collection', info: 'Target collection name' },
  { label: 'db', info: "Database override (defaults to the connection's)" },
  { label: 'filter', info: 'MQL filter, e.g. {"person_id": "abc"}' },
  { label: 'projection', info: 'Fields to include/exclude, e.g. {"addresses": 1}' },
  { label: 'sort', info: 'Sort spec, e.g. {"ts": -1}' },
  { label: 'pipeline', info: 'Aggregation stages (array)' },
  { label: 'documents', info: 'Documents to insert' },
  { label: 'update', info: 'Update document, e.g. {"$set": {"x": 1}}' },
  { label: 'field', info: 'Field name for distinct' },
  { label: 'many', info: 'true to update/delete every match' },
  { label: 'limit', info: 'Max documents (default 50)' },
  { label: 'skip', info: 'Documents to skip' },
];

/**
 * Completion for JSON bodies. Context-sensitive in the one way that matters: after
 * `"op":` it offers ops, after a collection-ish key it offers real collection names,
 * and otherwise it offers body keys. The op/key vocabulary is passed in, so the same
 * machinery serves the vector and mongo dialects without either leaking into the other.
 */
function bodyCompletions(
  ops: { label: string; info: string }[],
  keys: { label: string; info: string }[],
  collections: string[],
) {
  return (context: CompletionContext): CompletionResult | null => {
    const word = context.matchBefore(/[\w".]*/);
    if (!word || (word.from === word.to && !context.explicit)) return null;
    const before = context.state.sliceDoc(Math.max(0, word.from - 40), word.from);

    if (/"op"\s*:\s*"?$/.test(before)) {
      return {
        from: word.from,
        options: ops.map((o) => ({ ...o, type: 'keyword' })),
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
      options: keys.map((k) => ({ ...k, type: 'property' })),
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
    const isMongo = dialect === 'mongo';
    // Both non-SQL dialects send a JSON body; only the vocabulary differs.
    const isBody = isMongo || dialect === 'json';
    const collections = schema?.tables.map((t) => t.name) ?? [];
    const { schema: schemaMap, defaultSchema } = schemaToSqlConfig(schema);

    view.dispatch({
      effects: [
        langConfRef.current.reconfigure(
          isBody
            ? javascript()
            : sql({
                dialect: DIALECTS[provider ?? ''] ?? StandardSQL,
                schema: schemaMap,
                defaultSchema,
                upperCaseKeywords: true,
              }),
        ),
        completionConfRef.current.reconfigure(
          isBody
            ? autocompletion({
                override: [
                  bodyCompletions(
                    isMongo ? MONGO_OPS : VECTOR_OPS,
                    isMongo ? MONGO_KEYS : VECTOR_KEYS,
                    collections,
                  ),
                ],
              })
            : autocompletion(),
        ),
        placeholderConfRef.current.reconfigure(
          placeholder(
            isMongo
              ? 'MongoDB query, e.g. {"op": "find", "collection": "…", "filter": {}} — Ctrl/Cmd+Enter to run'
              : isBody
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
