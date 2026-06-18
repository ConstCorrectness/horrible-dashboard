import { Fragment, useState, useSyncExternalStore, type ReactNode } from 'react';

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
  return `${(n / 1024).toFixed(1)} KB`;
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

/** Expanded per-event detail: headers and bodies, already redacted at capture. */
function IoDetails({ event }: { event: IoEvent }) {
  const sections: [string, ReactNode][] = [];
  if (event.error) sections.push(['Error', <code key="v">{event.error}</code>]);
  if (event.request_headers) {
    sections.push(['Request headers', <HeaderList key="v" headers={event.request_headers} />]);
  }
  if (event.request_body) {
    sections.push(['Request body', <pre key="v">{event.request_body}</pre>]);
  }
  if (event.response_headers) {
    sections.push(['Response headers', <HeaderList key="v" headers={event.response_headers} />]);
  }
  if (event.response_body) {
    sections.push(['Response body', <pre key="v">{event.response_body}</pre>]);
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

/** Active row filter for the panel: a text query, a source, and an errors toggle. */
interface IoFilter {
  query: string;
  source: IoSource | 'all';
  errorsOnly: boolean;
}

const EMPTY_FILTER: IoFilter = { query: '', source: 'all', errorsOnly: false };
const SOURCES: readonly IoSource[] = ['client', 'inbound', 'outbound'];

/** Apply a filter to the event list (method/target substring, source, errors). */
function applyFilter(events: IoEvent[], f: IoFilter): IoEvent[] {
  const q = f.query.trim().toLowerCase();
  return events.filter((e) => {
    if (f.source !== 'all' && e.source !== f.source) return false;
    if (f.errorsOnly && !isError(e)) return false;
    if (q && !`${e.method} ${e.target}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

/** Tracks which event keys are expanded; shared by the panel and the widget. */
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

/** Full observability panel: the live I/O table with expandable rows. */
export function ObservabilityPanel() {
  const events = useIoEvents();
  const [isExpanded, toggle] = useExpanded();
  const [filter, setFilter] = useState<IoFilter>(EMPTY_FILTER);
  useAgentContext(() => ioSnapshot(events));
  const filtered = applyFilter(events, filter);
  const rows = [...filtered].reverse(); // newest first
  const filtering = filter.query !== '' || filter.source !== 'all' || filter.errorsOnly;

  return (
    <div className="obs-panel">
      <div className="obs-toolbar">
        <input
          className="obs-filter-query"
          type="search"
          placeholder="Filter method or target…"
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
      <div className="obs-table-wrap">
        <table className="obs-table">
          <thead>
            <tr>
              <th aria-label="Details" />
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
              <Fragment key={eventKey(e)}>
                <tr className={hasDetails(e) ? 'io-expandable' : ''} onClick={() => toggle(e)}>
                  <td className="io-caret">{hasDetails(e) ? (isExpanded(e) ? '▾' : '▸') : ''}</td>
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
                  <td className="io-dim">{fmtBytes(e.response_bytes)}</td>
                </tr>
                {isExpanded(e) && (
                  <tr className="io-details-row">
                    <td colSpan={8}>
                      <IoDetails event={e} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="dashboard-hint">
                  {filtering
                    ? 'No events match the filter.'
                    : 'No I/O yet — interact with the app and traffic appears here.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
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
