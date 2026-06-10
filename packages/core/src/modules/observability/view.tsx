import { useSyncExternalStore } from 'react';

import { telemetryStore, type IoEvent } from '../../telemetry';

function useIoEvents(): IoEvent[] {
  return useSyncExternalStore(telemetryStore.subscribe, telemetryStore.getSnapshot);
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

/** Full observability panel: the live I/O table with source badges. */
export function ObservabilityPanel() {
  const events = useIoEvents();
  const rows = [...events].reverse(); // newest first

  return (
    <div className="obs-panel">
      <div className="obs-toolbar">
        <span className="dashboard-hint">{events.length} events</span>
        <button onClick={() => telemetryStore.clear()}>Clear</button>
      </div>
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
              <tr key={`${e.source}-${e.id}`}>
                <td className="io-dim">{fmtTime(e.ts)}</td>
                <td>
                  <span className={`io-badge io-${e.source}`}>{e.source}</span>
                </td>
                <td>{e.method}</td>
                <td className="io-target" title={e.target}>
                  {e.target}
                </td>
                <td className={statusClass(e)}>{e.error ? 'ERR' : (e.status ?? '')}</td>
                <td className="io-dim">{e.duration_ms != null ? Math.round(e.duration_ms) : ''}</td>
                <td className="io-dim">{fmtBytes(e.response_bytes)}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="dashboard-hint">
                  No I/O yet — interact with the app and traffic appears here.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Compact dashboard widget: a summary and the last few calls. */
export function ObservabilityWidget() {
  const events = useIoEvents();
  const errors = events.filter((e) => e.error || (e.status != null && e.status >= 400)).length;
  const recent = [...events].slice(-5).reverse();

  return (
    <div className="obs-widget">
      <div className="obs-summary">
        <strong>{events.length}</strong> calls
        {errors > 0 && <span className="io-status-bad"> · {errors} errors</span>}
      </div>
      <ul className="obs-recent">
        {recent.map((e) => (
          <li key={`${e.source}-${e.id}`}>
            <span className={`io-badge io-${e.source}`}>{e.source}</span>
            <span className="io-target" title={e.target}>
              {e.method} {e.target}
            </span>
            <span className={statusClass(e)}>{e.error ? 'ERR' : (e.status ?? '')}</span>
          </li>
        ))}
        {recent.length === 0 && <li className="dashboard-hint">No I/O yet.</li>}
      </ul>
    </div>
  );
}
