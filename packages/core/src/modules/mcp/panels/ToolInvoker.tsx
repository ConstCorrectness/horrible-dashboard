import { useMemo, useState } from 'react';

import { callTool, type McpCallResult, type McpSchema, type McpServer, type McpTool } from '../api';

/**
 * Run one of a server's tools by hand.
 *
 * The form comes from the server's own `inputSchema`, not from a free-text JSON box.
 * That difference is the whole feature: a JSON box makes you guess the argument names
 * and then reports your typo as the server's error, whereas a generated form is the
 * server telling you what it takes — and it is generated from exactly the schema the
 * model is handed, so a schema that's confusing here is confusing there.
 *
 * Anything the flat form can't express (nested objects, arrays of objects) falls back
 * to a JSON textarea for that one field rather than silently dropping it. A form that
 * quietly omits a field would send an argument object that isn't what the user typed.
 */

type Values = Record<string, string>;

function isFlat(schema: McpSchema | undefined): boolean {
  const type = schema?.type;
  return type === 'string' || type === 'number' || type === 'integer' || type === 'boolean';
}

/** A form value coerced to what the schema says the field is. */
function coerce(schema: McpSchema | undefined, raw: string): unknown {
  const type = schema?.type;
  if (type === 'number' || type === 'integer') {
    const n = Number(raw);
    // A non-numeric string sent as a string is a clearer server-side error than
    // silently sending NaN, which serializes to null and looks like "not provided".
    return Number.isFinite(n) ? n : raw;
  }
  if (type === 'boolean') return raw === 'true';
  if (isFlat(schema)) return raw;
  try {
    return JSON.parse(raw);
  } catch {
    // Invalid JSON in a complex field goes as a string; the server's rejection names
    // the field, which is more useful than a client-side "invalid JSON" with no target.
    return raw;
  }
}

function Field({
  name,
  schema,
  required,
  value,
  onChange,
}: {
  name: string;
  schema: McpSchema;
  required: boolean;
  value: string;
  onChange: (v: string) => void;
}) {
  const label = (
    <label style={{ display: 'block', fontSize: '0.7rem', color: 'var(--text-dim)' }}>
      <code>{name}</code>
      {required && <span style={{ color: 'var(--danger, #f85149)' }}> *</span>}{' '}
      <span>{schema.description ?? ''}</span>
    </label>
  );

  let input;
  if (schema.enum && schema.enum.length > 0) {
    input = (
      <select value={value} onChange={(e) => onChange(e.target.value)} style={{ width: '100%' }}>
        {!required && <option value="">—</option>}
        {schema.enum.map((option) => (
          <option key={String(option)} value={String(option)}>
            {String(option)}
          </option>
        ))}
      </select>
    );
  } else if (schema.type === 'boolean') {
    input = (
      <select value={value} onChange={(e) => onChange(e.target.value)} style={{ width: '100%' }}>
        <option value="false">false</option>
        <option value="true">true</option>
      </select>
    );
  } else if (isFlat(schema)) {
    input = (
      <input
        value={value}
        placeholder={schema.type}
        onChange={(e) => onChange(e.target.value)}
        style={{ width: '100%' }}
      />
    );
  } else {
    input = (
      <textarea
        value={value}
        rows={3}
        placeholder={`JSON (${schema.type ?? 'object'})`}
        onChange={(e) => onChange(e.target.value)}
        style={{ width: '100%', fontFamily: 'monospace', fontSize: '0.7rem' }}
      />
    );
  }

  return (
    <div style={{ marginBottom: '0.35rem' }}>
      {label}
      {input}
    </div>
  );
}

function ResultView({ result }: { result: McpCallResult }) {
  return (
    <div style={{ marginTop: '0.4rem' }}>
      <div style={{ color: 'var(--text-dim)', fontSize: '0.7rem' }}>
        {result.error ? 'Error' : 'Result'} · {result.elapsedMs} ms
      </div>
      <pre
        style={{
          margin: '0.2rem 0 0',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          fontSize: '0.7rem',
          color: result.error ? 'var(--danger, #f85149)' : 'inherit',
          maxHeight: 220,
          overflow: 'auto',
        }}
      >
        {result.error ?? result.content ?? ''}
      </pre>
      {result.structured != null && (
        <pre
          style={{
            margin: '0.2rem 0 0',
            whiteSpace: 'pre-wrap',
            fontSize: '0.68rem',
            color: 'var(--text-dim)',
            maxHeight: 160,
            overflow: 'auto',
          }}
        >
          {JSON.stringify(result.structured, null, 2)}
        </pre>
      )}
      {result.attachments.length > 0 && (
        <div style={{ color: 'var(--text-dim)', fontSize: '0.68rem' }}>
          {result.attachments.join(', ')} — not shown; the model doesn't get these inline either.
        </div>
      )}
    </div>
  );
}

export function ToolInvoker({ server }: { server: McpServer }) {
  const [selected, setSelected] = useState(server.tools[0]?.name ?? '');
  const [values, setValues] = useState<Values>({});
  const [result, setResult] = useState<McpCallResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const tool: McpTool | undefined = useMemo(
    () => server.tools.find((t) => t.name === selected),
    [server.tools, selected],
  );
  const properties = tool?.inputSchema?.properties ?? {};
  const required = tool?.inputSchema?.required ?? [];
  const names = Object.keys(properties);

  const run = async () => {
    if (!tool) return;
    setBusy(true);
    setError(null);
    try {
      const args: Record<string, unknown> = {};
      for (const name of names) {
        const raw = values[`${tool.name}:${name}`] ?? '';
        // An untouched optional field is omitted rather than sent as "". A server that
        // distinguishes "absent" from "empty" — most do — would otherwise see a value
        // the user never typed.
        if (raw === '' && !required.includes(name)) continue;
        args[name] = coerce(properties[name], raw);
      }
      setResult(await callTool(server.id, tool.name, args));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (server.tools.length === 0) {
    return <div style={{ color: 'var(--text-dim)' }}>This server exposes no tools.</div>;
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: '0.35rem', alignItems: 'center' }}>
        <select
          value={selected}
          onChange={(e) => {
            setSelected(e.target.value);
            setResult(null);
          }}
          style={{ flex: 1 }}
        >
          {server.tools.map((t) => (
            <option key={t.name} value={t.name}>
              {t.name}
              {t.readOnly ? ' (read-only)' : ''}
            </option>
          ))}
        </select>
        <button disabled={busy} onClick={() => void run()}>
          {busy ? 'Running…' : 'Run'}
        </button>
      </div>

      {tool?.description && (
        <div style={{ color: 'var(--text-dim)', fontSize: '0.7rem', margin: '0.3rem 0' }}>
          {tool.description}
        </div>
      )}

      {/* Running a write tool from here is deliberate and unguarded — the user is the
          one pressing the button — but it should never be a surprise. */}
      {tool && !tool.readOnly && (
        <div style={{ color: 'var(--warn, #d29922)', fontSize: '0.68rem', marginBottom: '0.3rem' }}>
          Not annotated read-only: this runs for real.
        </div>
      )}

      {names.length === 0 && (
        <div style={{ color: 'var(--text-dim)', fontSize: '0.7rem' }}>No arguments.</div>
      )}
      {names.map((name) => (
        <Field
          key={`${tool?.name}:${name}`}
          name={name}
          schema={properties[name] ?? {}}
          required={required.includes(name)}
          value={values[`${tool?.name}:${name}`] ?? ''}
          onChange={(v) => setValues((prev) => ({ ...prev, [`${tool?.name}:${name}`]: v }))}
        />
      ))}

      {error && <div style={{ color: 'var(--danger, #f85149)' }}>{error}</div>}
      {result && <ResultView result={result} />}
    </div>
  );
}
