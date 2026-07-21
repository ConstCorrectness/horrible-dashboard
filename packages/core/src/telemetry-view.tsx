/**
 * Shared presentational components for `IoEvent` — the detail inspector, header
 * lists, and body rendering (JSON pretty-print + highlight, raw fallback, copy).
 *
 * These live beside `telemetry.ts` rather than inside the observability module
 * because more than one surface renders the shared event type: the observability
 * panel/widget, and the browser pane's network region. A module reaching into
 * another module's internals is exactly what the registry conventions forbid, so
 * anything two modules both need lives at this level instead.
 */
import { useMemo, useState, type ReactNode } from 'react';

import type { IoEvent } from './telemetry';

export function isIoError(e: IoEvent): boolean {
  return Boolean(e.error) || (e.status != null && e.status >= 400);
}

/**
 * A request the egress policy aborted. Distinct from an error: nothing went
 * wrong, the guard did its job — so it reads as a deliberate verdict, not a
 * failure.
 */
export function isIoBlocked(e: IoEvent): boolean {
  return e.verdict === 'blocked';
}

export function ioStatusClass(e: IoEvent): string {
  if (isIoBlocked(e)) return 'io-status-blocked';
  if (e.error || (e.status != null && e.status >= 400)) return 'io-status-bad';
  if (e.status != null && e.status >= 200 && e.status < 300) return 'io-status-ok';
  return '';
}

/** The status cell's text: a verdict outranks a code, since a blocked request has none. */
export function ioStatusLabel(e: IoEvent): string | number {
  if (isIoBlocked(e)) return 'BLOCKED';
  if (e.error) return 'ERR';
  return e.status ?? '';
}

export function ioEventKey(e: IoEvent): string {
  return `${e.source}-${e.id}`;
}

/** Whether the event carries detail worth an expanded view. */
export function hasIoDetails(e: IoEvent): boolean {
  return Boolean(
    e.request_headers || e.response_headers || e.request_body || e.response_body || e.error,
  );
}

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

/** Whichever byte count the event carries (ws frames record request_bytes). */
export function ioEventBytes(e: IoEvent): number | null | undefined {
  return e.response_bytes ?? e.request_bytes;
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
export function BodyView({ body }: { body: string }) {
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

export function HeaderList({ headers }: { headers: Record<string, string> }) {
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
export function IoDetails({ event }: { event: IoEvent }) {
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
export function IoInspector({ event, onClose }: { event: IoEvent; onClose: () => void }) {
  const rows: [string, ReactNode][] = [
    ['Source', <span className={`io-badge io-${event.source}`}>{event.source}</span>],
    ['Method', event.method],
    ['Target', <span className="io-mono">{event.target}</span>],
  ];
  // Browser requests carry two extra axes: what kind of resource Chromium thought
  // it was fetching, and whether the egress guard let it out.
  if (event.resource_type) rows.push(['Resource', event.resource_type]);
  if (event.verdict) {
    rows.push(['Egress', <span className={ioStatusClass(event)}>{event.verdict}</span>]);
  }
  if (event.status != null)
    rows.push(['Status', <span className={ioStatusClass(event)}>{event.status}</span>]);
  if (event.error) {
    rows.push([
      isIoBlocked(event) ? 'Reason' : 'Error',
      <code className={ioStatusClass(event)}>{event.error}</code>,
    ]);
  }
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
