import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAgentContext } from '../../agent-context';
import { ApiError } from '../../api';
import { dialogs } from '../../dialogs';
import { useSetting } from '../../settings';
import { toastsStore } from '../../toasts';
import {
  createConnection,
  deleteConnection,
  getSchema,
  listConnections,
  runQuery,
  testConnection,
  testSavedConnection,
  updateConnection,
  type ConnectionInfo,
  type ConnectionInput,
  type ProviderInfo,
  type QueryResult,
  type SchemaResponse,
} from './api';
import { QueryEditor } from './QueryEditor';

const MAX_HISTORY = 25;

/** Starter query for a connection, per dialect. */
function starterQuery(dialect: string): string {
  return dialect === 'json' || dialect === 'mongo' ? '{ "op": "collections" }' : '';
}

/**
 * What clicking a table/collection in the sidebar inserts. SQL gets a SELECT;
 * vector stores get a scan, which is the closest equivalent — a `search` would
 * need query text the click can't supply. Mongo gets an unfiltered `find`.
 */
function tableClickQuery(dialect: string, name: string): string {
  if (dialect === 'mongo') {
    return `{ "op": "find", "collection": "${name}", "filter": {}, "limit": 20 }`;
  }
  return dialect === 'json'
    ? `{ "op": "list", "collection": "${name}", "limit": 20 }`
    : `SELECT * FROM ${name} LIMIT 100;`;
}

/** The read-only toggle's tooltip — what it will refuse, in this dialect's terms. */
function readOnlyHint(dialect: string): string {
  if (dialect === 'mongo') {
    return 'Reject write ops (insert, update, delete, drop_collection) and any aggregate with $out/$merge';
  }
  return dialect === 'json'
    ? 'Reject write ops (upsert, delete, drop_collection)'
    : 'Reject anything but a single SELECT/WITH/EXPLAIN';
}

/** How the agent should shape a query for this dialect (exposed as agent context). */
function queryFormat(dialect: string): string {
  if (dialect === 'mongo') {
    return (
      'MongoDB: send a JSON body like {"op":"find","collection":"…","filter":{},"limit":20}. ' +
      'Ops: find, aggregate, count, distinct, collections, databases, describe, indexes, stats ' +
      '(read) / insert, update, delete, create_collection, drop_collection, command (write). ' +
      'Bodies are Extended JSON, so an ObjectId is {"$oid":"…"}.'
    );
  }
  if (dialect === 'json') {
    return (
      'Vector store: send a JSON body like {"op":"search","collection":"…","query":"…","limit":5}. ' +
      'Ops: search, list, get, count, collections, describe, peek (read) / upsert, delete (write).'
    );
  }
  return 'SQL.';
}

function errText(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : String(err);
}

/** Render a single result cell. Backend already coerces blobs/vectors to compact
 * strings; anything still structured (JSON columns) is stringified here. */
function cellText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export function DatabaseConsole() {
  const defaultConnection = useSetting<string>('database.defaultConnection') ?? 'app';
  const rowLimit = useSetting<number>('database.rowLimit') ?? 1000;

  const [connections, setConnections] = useState<ConnectionInfo[]>([]);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [activeConn, setActiveConn] = useState<string>(defaultConnection);

  const [schema, setSchema] = useState<SchemaResponse | null>(null);
  const [schemaError, setSchemaError] = useState<string | null>(null);

  const [sql, setSql] = useState('');
  const [result, setResult] = useState<QueryResult | null>(null);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  const [history, setHistory] = useState<string[]>([]);

  const [managerOpen, setManagerOpen] = useState(false);

  const activeConnInfo = connections.find((c) => c.id === activeConn);
  const dialect = activeConnInfo?.dialect ?? 'sql';

  // Expose the active connection + its schema so the agent writes a correct query.
  // `dialect` matters as much as the table list here: without it the agent writes
  // SQL at a vector store and gets a parse error it can't diagnose.
  useAgentContext(() => ({
    activeConnection: activeConn,
    provider: activeConnInfo?.provider ?? null,
    dialect,
    queryFormat: queryFormat(dialect),
    [dialect === 'sql' ? 'tables' : 'collections']:
      schema?.tables.map((t) => ({
        name: t.schema_name ? `${t.schema_name}.${t.name}` : t.name,
        columns: t.columns.map((c) => c.name),
      })) ?? [],
  }));

  const loadConnections = useCallback(async () => {
    try {
      const res = await listConnections();
      setConnections(res.connections);
      setProviders(res.providers);
      setActiveConn((prev) =>
        res.connections.some((c) => c.id === prev) ? prev : (res.connections[0]?.id ?? 'app'),
      );
    } catch (err) {
      setQueryError(errText(err));
    }
  }, []);

  const loadSchema = useCallback(async (connId: string) => {
    setSchema(null);
    setSchemaError(null);
    try {
      setSchema(await getSchema(connId));
    } catch (err) {
      setSchemaError(errText(err));
    }
  }, []);

  useEffect(() => {
    void loadConnections();
  }, [loadConnections]);

  useEffect(() => {
    if (activeConn) void loadSchema(activeConn);
  }, [activeConn, loadSchema]);

  // Seed a starter query when switching dialects with nothing to lose. A JSON body
  // is much less guessable than SELECT, so an empty editor there is a dead end —
  // but never clobber a query the user is in the middle of writing.
  useEffect(() => {
    setSql((prev) => (prev.trim() ? prev : starterQuery(dialect)));
  }, [dialect]);

  const execute = useCallback(async () => {
    const trimmed = sql.trim();
    if (!trimmed || running) return;
    setRunning(true);
    setQueryError(null);
    try {
      const res = await runQuery({
        connection_id: activeConn,
        sql: trimmed,
        read_only: readOnly,
        row_limit: rowLimit,
      });
      setResult(res);
      setHistory((prev) => [trimmed, ...prev.filter((q) => q !== trimmed)].slice(0, MAX_HISTORY));
    } catch (err) {
      setResult(null);
      setQueryError(errText(err));
    } finally {
      setRunning(false);
    }
  }, [sql, running, activeConn, readOnly, rowLimit]);

  const onTableClick = (qualifiedName: string) => {
    setSql(tableClickQuery(dialect, qualifiedName));
  };

  const statusLine = useMemo(() => {
    if (!result) return null;
    if (result.columns.length === 0) {
      const affected = result.affected ?? 0;
      return `${result.message ?? 'OK'} · ${affected} row${affected === 1 ? '' : 's'} affected · ${result.elapsed_ms.toFixed(1)} ms`;
    }
    const trunc = result.truncated ? ` (truncated to ${rowLimit})` : '';
    return `${result.rowcount} row${result.rowcount === 1 ? '' : 's'}${trunc} · ${result.elapsed_ms.toFixed(1)} ms`;
  }, [result, rowLimit]);

  return (
    <div className="dbc-container">
      <div className="dbc-toolbar">
        <label className="dbc-conn-label">
          Connection
          <select
            className="dbc-select"
            value={activeConn}
            onChange={(e) => setActiveConn(e.target.value)}
          >
            {connections.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.provider})
              </option>
            ))}
          </select>
        </label>
        <button className="dbc-btn" onClick={() => void loadSchema(activeConn)}>
          ↻ Schema
        </button>
        <button className="dbc-btn" onClick={() => setManagerOpen(true)}>
          Manage…
        </button>
        <label className="dbc-checkbox" title={readOnlyHint(dialect)}>
          <input
            type="checkbox"
            checked={readOnly}
            onChange={(e) => setReadOnly(e.target.checked)}
          />
          Read-only
        </label>
        {history.length > 0 && (
          <select
            className="dbc-select dbc-history"
            value=""
            onChange={(e) => {
              if (e.target.value) setSql(e.target.value);
            }}
          >
            <option value="">History…</option>
            {history.map((q, i) => (
              <option key={i} value={q}>
                {q.length > 60 ? `${q.slice(0, 60)}…` : q}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="dbc-body">
        <aside className="dbc-sidebar">
          {schemaError && <div className="dbc-sidebar-error">{schemaError}</div>}
          {!schema && !schemaError && <div className="dbc-sidebar-muted">Loading schema…</div>}
          {schema && schema.tables.length === 0 && (
            <div className="dbc-sidebar-muted">No tables.</div>
          )}
          {schema?.tables.map((t) => {
            const qualified = t.schema_name ? `${t.schema_name}.${t.name}` : t.name;
            return (
              <div key={qualified} className="dbc-tree-table">
                <button
                  className="dbc-tree-table-name"
                  title={
                    dialect === 'sql'
                      ? 'Insert SELECT for this table'
                      : 'Insert a query for this collection'
                  }
                  onClick={() => onTableClick(qualified)}
                >
                  {qualified}
                </button>
                <div className="dbc-tree-cols">
                  {t.columns.map((c) => (
                    <div key={c.name} className="dbc-tree-col">
                      <span className={c.primary_key ? 'dbc-col-pk' : ''}>{c.name}</span>
                      <span className="dbc-col-type">{c.type}</span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </aside>

        <div className="dbc-main">
          <QueryEditor
            value={sql}
            onChange={setSql}
            onRun={() => void execute()}
            provider={activeConnInfo?.provider ?? null}
            dialect={dialect}
            schema={schema}
          />
          <div className="dbc-run-bar">
            <button
              className="dbc-btn dbc-btn-primary"
              onClick={() => void execute()}
              disabled={running || !sql.trim()}
            >
              {running ? 'Running…' : 'Run ▶'}
            </button>
            {statusLine && <span className="dbc-status">{statusLine}</span>}
          </div>

          {queryError && <div className="dbc-error">{queryError}</div>}

          {result && result.columns.length > 0 && (
            <div className="dbc-result-scroll">
              <table className="dbc-result-table">
                <thead>
                  <tr>
                    <th className="dbc-rownum">#</th>
                    {result.columns.map((c) => (
                      <th key={c.name}>
                        {c.name}
                        {c.type ? <span className="dbc-col-type"> {c.type}</span> : null}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((row, ri) => (
                    <tr key={ri}>
                      <td className="dbc-rownum">{ri + 1}</td>
                      {row.map((value, ci) => (
                        <td key={ci} className={value === null ? 'dbc-null' : ''}>
                          {value === null ? 'NULL' : cellText(value)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {result.rows.length === 0 && <div className="dbc-empty">Query returned no rows.</div>}
            </div>
          )}

          {!result && !queryError && <div className="dbc-empty">Results will appear here.</div>}
        </div>
      </div>

      {managerOpen && (
        <ConnectionManager
          connections={connections}
          providers={providers}
          onClose={() => setManagerOpen(false)}
          onChanged={() => void loadConnections()}
        />
      )}
    </div>
  );
}

interface ManagerProps {
  connections: ConnectionInfo[];
  providers: ProviderInfo[];
  onClose: () => void;
  onChanged: () => void;
}

// Mirrors `connections._SECRET_FIELDS` — these come back from the backend as booleans
// (set / unset), never as values. `uri` is one of them because a connection string
// embeds the password.
const SECRET_FIELDS = new Set(['password', 'dsn', 'uri']);

function ConnectionManager({ connections, providers, onClose, onChanged }: ManagerProps) {
  // null = list view; otherwise the connection being edited ('new' for a fresh one).
  const [editing, setEditing] = useState<ConnectionInfo | 'new' | null>(null);
  const [name, setName] = useState('');
  const [provider, setProvider] = useState(providers[0]?.id ?? 'sqlite');
  const [fields, setFields] = useState<Record<string, string>>({});
  const [testMsg, setTestMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const providerInfo = providers.find((p) => p.id === provider);
  const isNew = editing === 'new';
  const editingId = editing && editing !== 'new' ? editing.id : null;

  const beginEdit = (conn: ConnectionInfo | 'new') => {
    setTestMsg(null);
    if (conn === 'new') {
      setName('');
      setProvider(providers[0]?.id ?? 'sqlite');
      setFields({});
    } else {
      setName(conn.name);
      setProvider(conn.provider);
      const init: Record<string, string> = {};
      for (const [k, v] of Object.entries(conn.config)) {
        // Secret values come back as booleans; leave the input blank (keeps stored value).
        if (!SECRET_FIELDS.has(k) && typeof v !== 'boolean') init[k] = String(v);
      }
      setFields(init);
    }
    setEditing(conn);
  };

  const buildInput = (): ConnectionInput => {
    const config: Record<string, string> = {};
    for (const [k, v] of Object.entries(fields)) {
      if (v !== '') config[k] = v;
    }
    return { name: name.trim() || provider, provider, config };
  };

  const onTest = async () => {
    setBusy(true);
    setTestMsg(null);
    try {
      // Editing with blank secrets → test the stored config server-side.
      const res =
        editingId && Object.values(fields).every((v) => v === '')
          ? await testSavedConnection(editingId)
          : await testConnection(buildInput());
      setTestMsg({ ok: res.ok, text: res.ok ? 'Connection OK' : (res.error ?? 'Failed') });
    } catch (err) {
      setTestMsg({ ok: false, text: errText(err) });
    } finally {
      setBusy(false);
    }
  };

  const onSave = async () => {
    setBusy(true);
    try {
      if (editingId) await updateConnection(editingId, buildInput());
      else await createConnection(buildInput());
      toastsStore.add('success', 'Database', 'Connection saved');
      onChanged();
      setEditing(null);
    } catch (err) {
      setTestMsg({ ok: false, text: errText(err) });
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async (conn: ConnectionInfo) => {
    const ok = await dialogs.confirm({
      title: `Delete "${conn.name}"?`,
      message: 'This removes the saved connection (the database itself is untouched).',
      danger: true,
      confirmLabel: 'Delete',
    });
    if (!ok) return;
    try {
      await deleteConnection(conn.id);
      toastsStore.add('success', 'Database', 'Connection deleted');
      onChanged();
    } catch (err) {
      toastsStore.add('error', 'Database', errText(err));
    }
  };

  return (
    <div className="dbc-modal-backdrop" onClick={onClose}>
      <div className="dbc-modal" onClick={(e) => e.stopPropagation()}>
        <div className="dbc-modal-hdr">
          <h3>{editing ? (isNew ? 'New connection' : 'Edit connection') : 'Connections'}</h3>
          <button className="dbc-btn" onClick={onClose}>
            ✕
          </button>
        </div>

        {editing === null ? (
          <div className="dbc-conn-list">
            {connections.map((c) => (
              <div key={c.id} className="dbc-conn-row">
                <div>
                  <strong>{c.name}</strong>
                  <span className="dbc-col-type"> {c.provider}</span>
                  {c.builtin && <span className="dbc-builtin-badge">built-in</span>}
                </div>
                {!c.builtin && (
                  <div className="dbc-conn-row-actions">
                    <button className="dbc-btn" onClick={() => beginEdit(c)}>
                      Edit
                    </button>
                    <button className="dbc-btn dbc-btn-danger" onClick={() => void onDelete(c)}>
                      Delete
                    </button>
                  </div>
                )}
              </div>
            ))}
            <button className="dbc-btn dbc-btn-primary" onClick={() => beginEdit('new')}>
              + Add connection
            </button>
          </div>
        ) : (
          <div className="dbc-form">
            <label className="dbc-field">
              Name
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="dbc-field">
              Provider
              <select
                value={provider}
                onChange={(e) => {
                  setProvider(e.target.value);
                  setFields({});
                  setTestMsg(null);
                }}
              >
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
            {providerInfo?.fields.map((f) => (
              <label key={f} className="dbc-field">
                {f}
                {SECRET_FIELDS.has(f) && editingId ? ' (leave blank to keep)' : ''}
                <input
                  type={f === 'password' ? 'password' : 'text'}
                  value={fields[f] ?? ''}
                  placeholder={f === 'path' ? '/path/to/file.db' : ''}
                  onChange={(e) => setFields((prev) => ({ ...prev, [f]: e.target.value }))}
                />
              </label>
            ))}
            {testMsg && (
              <div className={testMsg.ok ? 'dbc-test-ok' : 'dbc-test-err'}>{testMsg.text}</div>
            )}
            <div className="dbc-form-actions">
              <button className="dbc-btn" onClick={() => void onTest()} disabled={busy}>
                Test
              </button>
              <button className="dbc-btn" onClick={() => setEditing(null)} disabled={busy}>
                Back
              </button>
              <button
                className="dbc-btn dbc-btn-primary"
                onClick={() => void onSave()}
                disabled={busy}
              >
                Save
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
