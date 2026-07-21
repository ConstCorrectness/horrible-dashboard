/**
 * The browser's network inspector: what the embedded Chromium is talking to.
 *
 * Two halves, because they answer different questions and come from different places:
 *
 * - **Open connections** — requests currently in flight, pushed live on the `browser`
 *   channel (`connections`). These exist *only* while unresolved, so they can't come
 *   from the I/O ring buffer, which records on completion. This is the "what is this
 *   page doing right now / why is it hanging" view.
 * - **Completed** — finished requests read from the shared telemetry store, the same
 *   events the observability panel shows, filtered to `browser`. Includes requests the
 *   egress guard blocked, which is the whole point: a silently aborted request is
 *   exactly what you'd otherwise never see.
 *
 * Selecting a completed request opens the **same** `IoInspector` the observability
 * panel uses — headers, bodies with JSON pretty-print, timing, egress verdict. The
 * component is shared from `telemetry-view` rather than duplicated, since both
 * surfaces render the same `IoEvent` type.
 *
 * **Scope:** this lists *all* embedded-browser traffic, not just one pane's. The
 * backend keys browser sessions by WebSocket connection (`BrowserSession` in
 * backend/modules/browser/session.py) and the app has one shared `/ws`, so every
 * browser pane drives the same headless Chromium — there is only one stream of
 * traffic to show. The header says "All browser traffic" rather than implying
 * otherwise. Per-pane scoping needs per-pane browser sessions first.
 *
 * Registered as a view (`browser.network`) and declared as a right-hand region of
 * `browser.view`, so it persists per pane instance, resizes, and can be dragged
 * out to an area of its own where the inspector has room.
 */
import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';

import { telemetryStore, type IoEvent } from '../../../telemetry';
import { IoInspector, ioEventKey, ioStatusClass } from '../../../telemetry-view';
import { subscribeConnections, type BrowserConnection } from '../session';

function host(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/** Trailing path segment — enough to recognize a request without the full URL. */
function leaf(url: string): string {
  try {
    const { pathname, search } = new URL(url);
    const name = pathname.split('/').filter(Boolean).pop() ?? '/';
    return name + (search ? '?…' : '');
  } catch {
    return url;
  }
}

const cell: React.CSSProperties = {
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
};

export function NetworkStrip() {
  const [conns, setConns] = useState<BrowserConnection[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const events = useSyncExternalStore(telemetryStore.subscribe, telemetryStore.getSnapshot);

  useEffect(() => subscribeConnections(setConns), []);

  const done = useMemo(() => {
    const q = query.trim().toLowerCase();
    return events
      .filter((e: IoEvent) => e.source === 'browser')
      .filter(
        (e) => !q || `${e.method} ${e.target} ${e.resource_type ?? ''}`.toLowerCase().includes(q),
      )
      .slice(-200)
      .reverse();
  }, [events, query]);

  const blocked = done.filter((e) => e.verdict === 'blocked').length;
  // Resolved against the unfiltered list: a selection must survive the filter
  // changing out from under it.
  const selected = events.find((e) => ioEventKey(e) === selectedKey) ?? null;

  return (
    <div className="browser-net">
      <div className="browser-net-head">
        <strong>All browser traffic</strong>
        <span style={{ color: 'var(--text-dim)' }}>
          {conns.length} open · {done.length} done
          {blocked > 0 && <span className="io-status-blocked"> · {blocked} blocked</span>}
        </span>
      </div>

      <input
        className="browser-net-filter"
        type="search"
        placeholder="Filter requests…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      <div className="browser-net-list">
        {conns.length > 0 && <div className="browser-net-section">In flight</div>}
        {conns.map((c) => (
          <div key={c.id} className="browser-net-row" title={c.url}>
            {/* A pulsing dot is the honest signal here: elapsedMs is a snapshot from
                the last push, not a live timer, so animating it would be a lie. */}
            <span className="io-status-blocked">●</span>
            <span style={{ ...cell, flex: 1 }}>{leaf(c.url)}</span>
            <span style={{ color: 'var(--text-dim)' }}>{Math.round(c.elapsedMs)}ms</span>
          </div>
        ))}

        {done.length > 0 && <div className="browser-net-section">Completed</div>}
        {done.map((e) => (
          <button
            key={ioEventKey(e)}
            type="button"
            className={`browser-net-row browser-net-row--clickable${
              ioEventKey(e) === selectedKey ? ' browser-net-row--selected' : ''
            }`}
            title={`${e.method} ${e.target}${e.error ? ` — ${e.error}` : ''}`}
            onClick={() => setSelectedKey(ioEventKey(e))}
          >
            <span
              style={{ width: 58, ...cell, color: 'var(--text-dim)' }}
              title={e.resource_type ?? ''}
            >
              {e.resource_type ?? ''}
            </span>
            <span style={{ ...cell, flex: 1 }}>{leaf(e.target)}</span>
            <span style={{ width: 62, ...cell, color: 'var(--text-dim)' }}>{host(e.target)}</span>
            <span className={ioStatusClass(e)}>
              {e.verdict === 'blocked' ? '⃠' : e.error ? '!' : (e.status ?? '')}
            </span>
          </button>
        ))}

        {conns.length === 0 && done.length === 0 && (
          <div className="dashboard-hint" style={{ padding: '0.45rem' }}>
            {query
              ? 'No requests match the filter.'
              : 'No requests yet. Navigate and every request this page makes appears here.'}
          </div>
        )}
      </div>

      {selected && (
        <div className="browser-net-inspector">
          <IoInspector event={selected} onClose={() => setSelectedKey(null)} />
        </div>
      )}
    </div>
  );
}
