import { useState, useSyncExternalStore } from 'react';

import type { AgentContextSnapshot } from '@horribledashboard/sdk';

import { useAgentContext } from '../../agent-context';
import { useSetting } from '../../settings';
import { telemetryStore, type IoEvent, type IoSource } from '../../telemetry';
import {
  fmtBytes,
  hasIoDetails,
  IoDetails,
  IoInspector,
  ioEventBytes,
  ioEventKey,
  ioStatusClass,
  ioStatusLabel,
  isIoError,
} from '../../telemetry-view';

function useIoEvents(): IoEvent[] {
  return useSyncExternalStore(telemetryStore.subscribe, telemetryStore.getSnapshot);
}

/**
 * The agent-readable snapshot of the data flow: a summary plus the most recent
 * calls (newest first) so the agent can reason about what the app is doing —
 * "did that request fail?", "how much traffic is going out?".
 */
function ioSnapshot(events: IoEvent[], recentCount = 10): AgentContextSnapshot {
  return {
    totalCalls: events.length,
    errors: events.filter(isIoError).length,
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

/** Active row filter for the panel: a text query, a source, and an errors toggle. */
interface IoFilter {
  query: string;
  source: IoSource | 'all';
  errorsOnly: boolean;
}

const EMPTY_FILTER: IoFilter = { query: '', source: 'all', errorsOnly: false };
const SOURCES: readonly IoSource[] = ['client', 'inbound', 'outbound', 'ws', 'browser'];

/** Apply a filter to the event list (method/target/body substring, source, errors). */
function applyFilter(events: IoEvent[], f: IoFilter): IoEvent[] {
  const q = f.query.trim().toLowerCase();
  return events.filter((e) => {
    if (f.source !== 'all' && e.source !== f.source) return false;
    if (f.errorsOnly && !isIoError(e)) return false;
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
  const isExpanded = (e: IoEvent) => expanded.has(ioEventKey(e));
  const toggle = (e: IoEvent) => {
    if (!hasIoDetails(e)) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      const key = ioEventKey(e);
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
  const selected = events.find((e) => ioEventKey(e) === selectedKey) ?? null;

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
                  key={ioEventKey(e)}
                  className={`io-row${ioEventKey(e) === selectedKey ? ' io-row-selected' : ''}`}
                  onClick={() => setSelectedKey(ioEventKey(e))}
                >
                  <td className="io-dim">{fmtTime(e.ts)}</td>
                  <td>
                    <span className={`io-badge io-${e.source}`}>{e.source}</span>
                  </td>
                  <td>{e.method}</td>
                  <td className="io-target" title={e.target}>
                    {e.target}
                  </td>
                  <td className={ioStatusClass(e)}>{ioStatusLabel(e)}</td>
                  <td className="io-dim">
                    {e.duration_ms != null ? Math.round(e.duration_ms) : ''}
                  </td>
                  <td className="io-dim">{fmtBytes(ioEventBytes(e))}</td>
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
        {selected && <IoInspector event={selected} onClose={() => setSelectedKey(null)} />}
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
  const errors = events.filter(isIoError).length;
  const recent = [...events].slice(-recentCount).reverse();

  return (
    <div className="obs-widget">
      <div className="obs-summary">
        <strong>{events.length}</strong> calls
        {errors > 0 && <span className="io-status-bad"> · {errors} errors</span>}
      </div>
      <ul className="obs-recent">
        {recent.map((e) => (
          <li key={ioEventKey(e)}>
            <button
              className={`obs-recent-row ${hasIoDetails(e) ? 'io-expandable' : ''}`}
              onClick={() => toggle(e)}
            >
              <span className="io-caret">{hasIoDetails(e) ? (isExpanded(e) ? '▾' : '▸') : ''}</span>
              <span className={`io-badge io-${e.source}`}>{e.source}</span>
              <span className="io-target" title={e.target}>
                {e.method} {e.target}
              </span>
              <span className={ioStatusClass(e)}>{ioStatusLabel(e)}</span>
            </button>
            {isExpanded(e) && <IoDetails event={e} />}
          </li>
        ))}
        {recent.length === 0 && <li className="dashboard-hint">No I/O yet.</li>}
      </ul>
    </div>
  );
}
