import { useState, useSyncExternalStore } from 'react';

import type { AgentContextSnapshot } from '@horribledashboard/sdk';

import { useAgentContext } from '../../agent-context';
import { setSetting, useSetting } from '../../settings';
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

/** Active row filter for the panel: a text query, muted sources, an errors toggle. */
interface IoFilter {
  query: string;
  /**
   * Sources to **hide**, rather than the one source to show.
   *
   * It used to be `source: IoSource | 'all'` — a dropdown that could show
   * everything or exactly one thing, and therefore could not express the one
   * thing anybody actually wants here: *everything except `ws`*. A busy
   * websocket produces frames faster than anything else in the app, so the
   * choice was between a list buried in them and a list with nothing else in it.
   */
  muted: ReadonlySet<IoSource>;
  errorsOnly: boolean;
}

const SOURCES: readonly IoSource[] = ['client', 'inbound', 'outbound', 'ws', 'browser'];

/** Parse the stored setting: a comma-separated list of source names. */
export function parseMuted(raw: string | undefined): ReadonlySet<IoSource> {
  if (!raw) return new Set();
  const known = new Set<string>(SOURCES);
  return new Set(
    raw
      .split(',')
      .map((s) => s.trim().toLowerCase())
      // An unknown name is dropped rather than kept: the setting is editable by
      // hand, and a typo that silently muted nothing is better than one that
      // sits in the list looking like it did something.
      .filter((s): s is IoSource => known.has(s)),
  );
}

/** Serialize back, in the declared order so the value is stable to diff. */
export function formatMuted(muted: ReadonlySet<IoSource>): string {
  return SOURCES.filter((s) => muted.has(s)).join(',');
}

/** Apply a filter to the event list (method/target/body substring, mutes, errors). */
export function applyFilter(events: IoEvent[], f: IoFilter): IoEvent[] {
  const q = f.query.trim().toLowerCase();
  return events.filter((e) => {
    if (f.muted.has(e.source)) return false;
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
  const [query, setQuery] = useState('');
  const [errorsOnly, setErrorsOnly] = useState(false);
  // Persisted, not component state: muting `ws` is a standing preference about a
  // source that never stops being noisy, and a mute that reset every time the
  // pane was reopened would have to be redone every time.
  const muted = parseMuted(useSetting<string>('observability.mutedSources'));
  const filter: IoFilter = { query, muted, errorsOnly };
  useAgentContext(() => ioSnapshot(events));
  const filtered = applyFilter(events, filter);
  const rows = [...filtered].reverse(); // newest first
  const filtering = query !== '' || muted.size > 0 || errorsOnly;

  const toggleSource = (source: IoSource) => {
    const next = new Set(muted);
    if (next.has(source)) next.delete(source);
    else next.add(source);
    void setSetting('observability.mutedSources', formatMuted(next));
  };
  const selected = events.find((e) => ioEventKey(e) === selectedKey) ?? null;

  return (
    <div className="obs-panel">
      <div className="obs-toolbar">
        <input
          className="obs-filter-query"
          type="search"
          placeholder="Filter method, target, or body…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {/* One toggle per source, each showing what it would hide. A row of
            chips rather than a dropdown because muting is not picking: the
            useful state is "all of them except that one", which a select cannot
            hold. */}
        <div className="obs-filter-sources" role="group" aria-label="Sources">
          {SOURCES.map((s) => {
            const count = events.filter((e) => e.source === s).length;
            const off = muted.has(s);
            return (
              <button
                key={s}
                type="button"
                className={`io-badge io-${s} obs-source-toggle${off ? ' is-muted' : ''}`}
                aria-pressed={!off}
                title={
                  off ? `${s}: hidden — click to show` : `${s}: ${count} events — click to hide`
                }
                onClick={() => toggleSource(s)}
              >
                {s}
                {count > 0 && <span className="obs-source-count">{count}</span>}
              </button>
            );
          })}
        </div>
        <label className="obs-filter-errors">
          <input
            type="checkbox"
            checked={errorsOnly}
            onChange={(e) => setErrorsOnly(e.target.checked)}
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
  // The widget shows five rows. If `ws` is muted in the panel it has to be muted
  // here too, or the mute achieves nothing on the surface with the least room.
  const muted = parseMuted(useSetting<string>('observability.mutedSources'));
  const visible = events.filter((e) => !muted.has(e.source));
  useAgentContext(() => ioSnapshot(events, recentCount));
  const errors = visible.filter(isIoError).length;
  const recent = [...visible].slice(-recentCount).reverse();

  return (
    <div className="obs-widget">
      <div className="obs-summary">
        <strong>{visible.length}</strong> calls
        {muted.size > 0 && (
          <span className="dashboard-hint"> · {events.length - visible.length} hidden</span>
        )}
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
