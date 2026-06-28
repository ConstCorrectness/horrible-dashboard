import { useMemo, useState, useSyncExternalStore, type ReactNode } from 'react';

import type { AgentContextSnapshot } from '@horribledashboard/sdk';

import { useAgentContext } from '../../agent-context';
import { useSetting } from '../../settings';
import { telemetryStore, type IoEvent, type IoSource } from '../../telemetry';

function useIoEvents(): IoEvent[] {
  return useSyncExternalStore(telemetryStore.subscribe, telemetryStore.getSnapshot);
}

function isError(e: IoEvent): boolean {
  return Boolean(e.error) || (e.status != null && e.status >= 400);
}

/**
 * The agent-readable snapshot of the data flow: a summary plus the most recent
 * calls (newest first) so the agent can reason about what the app is doing —
 * "did that request fail?", "how much traffic is going out?".
 */
function ioSnapshot(events: IoEvent[], recentCount = 10): AgentContextSnapshot {
  return {
    totalCalls: events.length,
    errors: events.filter(isError).length,
    recent: [...events]
      .slice(-recentCount)
      .reverse()
      .map((e) => ({
        source: e.source,
        method: e.method,
        target: e.target,
        status: e.status ?? null,
        durationMs: e.duration_ms != null ? Math.round(e.duration_ms) : null,
        error: e.error ?? null,
      })),
  };
}

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString();
}

function fmtBytes(n: number | null | undefined): string {
  if (n == null) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

/** Whichever byte count the event carries (ws frames record request_bytes). */
function eventBytes(e: IoEvent): number | null | undefined {
  return e.response_bytes ?? e.request_bytes;
}

function statusClass(e: IoEvent): string {
  if (e.error || (e.status != null && e.status >= 400)) return 'io-status-bad';
  if (e.status != null && e.status >= 200 && e.status < 300) return 'io-status-ok';
  return '';
}

function eventKey(e: IoEvent): string {
  return `${e.source}-${e.id}`;
}

/** Whether the event carries detail worth an expanded view. */
function hasDetails(e: IoEvent): boolean {
  return Boolean(
    e.request_headers || e.response_headers || e.request_body || e.response_body || e.error,
  );
}

// --- Body rendering: JSON pretty-print + highlight, with a raw fallback. -----

/** Pretty-print a body if it parses as JSON (whole, or NDJSON line-by-line). */
function prettyJson(text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    // NDJSON / SSE-ish: each non-empty line a JSON value (Ollama streams, ws teed).
    const lines = trimmed.split('\n').filter((l) => l.trim());
    if (lines.length < 2) return null;
    const parsed: unknown[] = [];
    for (const line of lines) {
      try {
        parsed.push(JSON.parse(line));
      } catch {
        return null; // not uniformly JSON — show raw
      }
    }
    return parsed.map((v) => JSON.stringify(v, null, 2)).join('\n');
  }
}

const JSON_TOKEN =
  /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|(\b-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(\btrue\b|\bfalse\b|\bnull\b)/g;

/** Lightweight JSON syntax highlight → React nodes (no dependency, XSS-safe). */
function highlightJson(json: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  JSON_TOKEN.lastIndex = 0;
  while ((m = JSON_TOKEN.exec(json)) !== null) {
    if (m.index > last) out.push(json.slice(last, m.index));
    const [tok] = m;
    const cls = m[1] ? 'io-j-key' : m[2] ? 'io-j-str' : m[3] ? 'io-j-num' : 'io-j-lit';
    out.push(
      <span key={i++} className={cls}>
        {tok}
      </span>,
    );
    last = m.index + tok.length;
  }
  if (last < json.length) out.push(json.slice(last));
  return out;
}

/** A request/response body with a Pretty⇄Raw toggle and copy. */
function BodyView({ body }: { body: string }) {
  const pretty = useMemo(() => prettyJson(body), [body]);
  const [raw, setRaw] = useState(false);
  const showPretty = pretty != null && !raw;

  return (
    <div className="io-body">
      <div className="io-body-bar">
        {pretty != null && (
          <button className="io-mini-btn" onClick={() => setRaw((r) => !r)}>
            {raw ? 'Pretty' : 'Raw'}
          </button>
        )}
        <button className="io-mini-btn" onClick={() => void navigator.clipboard?.writeText(body)}>
          Copy
        </button>
      </div>
      <pre className="io-body-pre">{showPretty ? highlightJson(pretty as string) : body}</pre>
    </div>
  );
}

function HeaderList({ headers }: { headers: Record<string, string> }) {
  return (
    <dl className="io-kv">
      {Object.entries(headers).map(([name, value]) => (
        <div key={name}>
          <dt>{name}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Compact expanded detail (used by the dashboard widget's inline rows). */
function IoDetails({ event }: { event: IoEvent }) {
  const sections: [string, ReactNode][] = [];
  if (event.error) sections.push(['Error', <code key="v">{event.error}</code>]);
  if (event.request_headers) {
    sections.push(['Request headers', <HeaderList key="v" headers={event.request_headers} />]);
  }
  if (event.request_body)
    sections.push(['Request body', <BodyView key="v" body={event.request_body} />]);
  if (event.response_headers) {
    sections.push(['Response headers', <HeaderList key="v" headers={event.response_headers} />]);
  }
  if (event.response_body) {
    sections.push(['Response body', <BodyView key="v" body={event.response_body} />]);
  }
  return (
    <div className="io-details">
      {sections.map(([title, body]) => (
        <section key={title}>
          <h4>{title}</h4>
          {body}
        </section>
      ))}
    </div>
  );
}

/** The full Wireshark-style detail inspector for a selected event. */
function Inspector({ event, onClose }: { event: IoEvent; onClose: () => void }) {
  const rows: [string, ReactNode][] = [
    ['Source', <span className={`io-badge io-${event.source}`}>{event.source}</span>],
    ['Method', event.method],
    ['Target', <span className="io-mono">{event.target}</span>],
  ];
  if (event.status != null)
    rows.push(['Status', <span className={statusClass(event)}>{event.status}</span>]);
  if (event.error) rows.push(['Error', <code className="io-status-bad">{event.error}</code>]);
  if (event.duration_ms != null) rows.push(['Duration', `${Math.round(event.duration_ms)} ms`]);
  if (event.request_bytes != null) rows.push(['Request size', fmtBytes(event.request_bytes)]);
  if (event.response_bytes != null) rows.push(['Response size', fmtBytes(event.response_bytes)]);
  rows.push(['Time', new Date(event.ts * 1000).toLocaleString()]);

  // For ws frames the single payload lives in request_body; label it plainly.
  const isWs = event.source === 'ws';

  return (
    <div className="io-inspector">
      <div className="io-inspector-head">
        <strong>{event.method}</strong> <span className="io-mono io-target">{event.target}</span>
        <button className="io-mini-btn io-inspector-close" onClick={onClose}>
          ×
        </button>
      </div>
      <section className="io-inspector-section">
        <h4>General</h4>
        <dl className="io-kv">
          {rows.map(([k, v], i) => (
            <div key={i}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </section>
      {event.request_headers && (
        <section className="io-inspector-section">
          <h4>Request headers</h4>
          <HeaderList headers={event.request_headers} />
        </section>
      )}
      {event.request_body && (
        <section className="io-inspector-section">
          <h4>{isWs ? 'Frame payload' : 'Request body'}</h4>
          <BodyView body={event.request_body} />
        </section>
      )}
      {event.response_headers && (
        <section className="io-inspector-section">
          <h4>Response headers</h4>
          <HeaderList headers={event.response_headers} />
        </section>
      )}
      {event.response_body && (
        <section className="io-inspector-section">
          <h4>Response body</h4>
          <BodyView body={event.response_body} />
        </section>
      )}
    </div>
  );
}

/** Active row filter for the panel: a text query, a source, and an errors toggle. */
interface IoFilter {
  query: string;
  source: IoSource | 'all';
  errorsOnly: boolean;
}

const EMPTY_FILTER: IoFilter = { query: '', source: 'all', errorsOnly: false };
const SOURCES: readonly IoSource[] = ['client', 'inbound', 'outbound', 'ws'];

/** Apply a filter to the event list (method/target/body substring, source, errors). */
function applyFilter(events: IoEvent[], f: IoFilter): IoEvent[] {
  const q = f.query.trim().toLowerCase();
  return events.filter((e) => {
    if (f.source !== 'all' && e.source !== f.source) return false;
    if (f.errorsOnly && !isError(e)) return false;
    if (q) {
      const hay = `${e.method} ${e.target} ${e.request_body ?? ''} ${e.response_body ?? ''}`;
      if (!hay.toLowerCase().includes(q)) return false;
    }
    return true;
  });
}

/** Tracks which event keys are expanded; used by the compact widget. */
function useExpanded(): [(e: IoEvent) => boolean, (e: IoEvent) => void] {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const isExpanded = (e: IoEvent) => expanded.has(eventKey(e));
  const toggle = (e: IoEvent) => {
    if (!hasDetails(e)) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      const key = eventKey(e);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  return [isExpanded, toggle];
}

/** Full observability panel: a packet-list table over a detail inspector. */
export function ObservabilityPanel() {
  const events = useIoEvents();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [filter, setFilter] = useState<IoFilter>(EMPTY_FILTER);
  useAgentContext(() => ioSnapshot(events));
  const filtered = applyFilter(events, filter);
  const rows = [...filtered].reverse(); // newest first
  const filtering = filter.query !== '' || filter.source !== 'all' || filter.errorsOnly;
  const selected = events.find((e) => eventKey(e) === selectedKey) ?? null;

  return (
    <div className="obs-panel">
      <div className="obs-toolbar">
        <input
          className="obs-filter-query"
          type="search"
          placeholder="Filter method, target, or body…"
          value={filter.query}
          onChange={(e) => setFilter((f) => ({ ...f, query: e.target.value }))}
        />
        <select
          className="obs-filter-source"
          value={filter.source}
          onChange={(e) =>
            setFilter((f) => ({ ...f, source: e.target.value as IoFilter['source'] }))
          }
        >
          <option value="all">All sources</option>
          {SOURCES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <label className="obs-filter-errors">
          <input
            type="checkbox"
            checked={filter.errorsOnly}
            onChange={(e) => setFilter((f) => ({ ...f, errorsOnly: e.target.checked }))}
          />
          Errors only
        </label>
        <span className="dashboard-hint">
          {filtering ? `${rows.length} / ${events.length}` : `${events.length}`} events
        </span>
        <button onClick={() => telemetryStore.clear()}>Clear</button>
      </div>
      <div className={`obs-split${selected ? ' obs-split-open' : ''}`}>
        <div className="obs-table-wrap">
          <table className="obs-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Source</th>
                <th>Method</th>
                <th>Target</th>
                <th>Status</th>
                <th>ms</th>
                <th>Size</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr
                  key={eventKey(e)}
                  className={`io-row${eventKey(e) === selectedKey ? ' io-row-selected' : ''}`}
                  onClick={() => setSelectedKey(eventKey(e))}
                >
                  <td className="io-dim">{fmtTime(e.ts)}</td>
                  <td>
                    <span className={`io-badge io-${e.source}`}>{e.source}</span>
                  </td>
                  <td>{e.method}</td>
                  <td className="io-target" title={e.target}>
                    {e.target}
                  </td>
                  <td className={statusClass(e)}>{e.error ? 'ERR' : (e.status ?? '')}</td>
                  <td className="io-dim">
                    {e.duration_ms != null ? Math.round(e.duration_ms) : ''}
                  </td>
                  <td className="io-dim">{fmtBytes(eventBytes(e))}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={7} className="dashboard-hint">
                    {filtering
                      ? 'No events match the filter.'
                      : 'No I/O yet — interact with the app and traffic appears here.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {selected && <Inspector event={selected} onClose={() => setSelectedKey(null)} />}
      </div>
    </div>
  );
}

/** Compact dashboard widget: a summary and the last few calls, expandable too. */
export function ObservabilityWidget() {
  const events = useIoEvents();
  const [isExpanded, toggle] = useExpanded();
  const recentCount = useSetting<number>('observability.recentCount') ?? 5;
  useAgentContext(() => ioSnapshot(events, recentCount));
  const errors = events.filter(isError).length;
  const recent = [...events].slice(-recentCount).reverse();

  return (
    <div className="obs-widget">
      <div className="obs-summary">
        <strong>{events.length}</strong> calls
        {errors > 0 && <span className="io-status-bad"> · {errors} errors</span>}
      </div>
      <ul className="obs-recent">
        {recent.map((e) => (
          <li key={eventKey(e)}>
            <button
              className={`obs-recent-row ${hasDetails(e) ? 'io-expandable' : ''}`}
              onClick={() => toggle(e)}
            >
              <span className="io-caret">{hasDetails(e) ? (isExpanded(e) ? '▾' : '▸') : ''}</span>
              <span className={`io-badge io-${e.source}`}>{e.source}</span>
              <span className="io-target" title={e.target}>
                {e.method} {e.target}
              </span>
              <span className={statusClass(e)}>{e.error ? 'ERR' : (e.status ?? '')}</span>
            </button>
            {isExpanded(e) && <IoDetails event={e} />}
          </li>
        ))}
        {recent.length === 0 && <li className="dashboard-hint">No I/O yet.</li>}
      </ul>
    </div>
  );
}
