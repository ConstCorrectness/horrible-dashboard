/**
 * The path packets take, hop by hop, plotted on a world map.
 *
 * Two honesty constraints shaped this:
 *
 * - **No tile provider.** A slippy map would mean an external host, an API key, and
 *   every pan leaking the user's viewport to a third party — in a pane whose whole
 *   purpose is showing what leaves the machine. The coastline below is a coarse
 *   inline path in equirectangular projection: enough to read "these packets crossed
 *   the Atlantic", which is the question being asked.
 * - **Gaps stay gaps.** A hop that didn't answer ICMP, or an IP the database
 *   doesn't know, is drawn as a break in the line rather than interpolated. The
 *   first few hops of any trace are the user's own router and a carrier backbone,
 *   and inventing coordinates for them would be fiction.
 */
import { useEffect, useMemo, useState } from 'react';

import { geoStatus, probeTrace, type GeoStatus, type TraceHop, type TraceResult } from './api';

// Equirectangular: lon/lat map linearly onto x/y, which is why this projection is
// worth the distortion — the arithmetic is two lines and needs no library.
const MAP_W = 720;
const MAP_H = 360;

function project(lat: number, lon: number): [number, number] {
  return [((lon + 180) / 360) * MAP_W, ((90 - lat) / 180) * MAP_H];
}

/**
 * A very coarse land outline. Not a dataset — a handful of polygons that give the
 * plotted points somewhere to sit. Precision here would be false comfort: city-level
 * IP geolocation is 55–80% accurate at best.
 */
const LAND =
  'M 130 60 L 250 55 L 300 75 L 290 110 L 250 130 L 200 120 L 150 100 Z ' +
  'M 195 135 L 235 145 L 250 200 L 225 265 L 200 230 L 190 180 Z ' +
  'M 330 70 L 400 60 L 430 85 L 410 115 L 370 120 L 340 100 Z ' +
  'M 350 125 L 420 120 L 440 190 L 400 250 L 360 200 L 345 160 Z ' +
  'M 440 55 L 620 50 L 660 95 L 640 150 L 560 160 L 500 130 L 450 95 Z ' +
  'M 600 175 L 660 170 L 690 215 L 660 250 L 615 225 Z';

function HopRow({ hop }: { hop: TraceHop }) {
  const rtt = hop.rtt_ms.length
    ? `${Math.min(...hop.rtt_ms).toFixed(0)}ms`
    : hop.timeout
      ? 'no reply'
      : '';
  return (
    <div style={{ display: 'flex', gap: '0.5rem', fontSize: '0.73rem' }}>
      <span style={{ width: 20, color: 'var(--text-dim)' }}>{hop.ttl}</span>
      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {hop.host || hop.ip || '·'}
      </span>
      <span style={{ color: 'var(--text-dim)' }}>
        {hop.geo ? `${hop.geo.city ?? ''} ${hop.geo.country ?? ''}`.trim() : ''}
      </span>
      <span style={{ width: 60, textAlign: 'right', color: 'var(--text-dim)' }}>{rtt}</span>
    </div>
  );
}

export function RouteView({ initialTarget }: { initialTarget?: string }) {
  const [target, setTarget] = useState(initialTarget ?? '');
  const [result, setResult] = useState<TraceResult | null>(null);
  const [geo, setGeo] = useState<GeoStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void geoStatus()
      .then(setGeo)
      .catch(() => undefined);
  }, []);

  const run = () => {
    const host = target.trim();
    if (!host || busy) return;
    setBusy(true);
    setError(null);
    probeTrace(host)
      .then(setResult)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(false));
  };

  const points = useMemo(
    () =>
      (result?.hops ?? [])
        .filter((h) => h.geo)
        .map((h) => ({ hop: h, xy: project(h.geo!.lat, h.geo!.lon) })),
    [result],
  );

  return (
    <div style={{ padding: '0.5rem', fontSize: '0.78rem' }}>
      <div style={{ display: 'flex', gap: '0.4rem', marginBottom: '0.5rem' }}>
        <input
          style={{ flex: 1 }}
          placeholder="host or URL to trace"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && run()}
        />
        <button type="button" onClick={run} disabled={busy || !target.trim()}>
          {busy ? '…' : 'Trace'}
        </button>
      </div>

      {error && <div style={{ color: 'var(--danger, #d66)' }}>{error}</div>}
      {result?.error && <div style={{ color: 'var(--danger, #d66)' }}>{result.error}</div>}

      {!result && !error && (
        <div className="dashboard-hint">
          Trace the routers between here and a host. Uses the system <code>tracert</code>/
          <code>traceroute</code>, because raw ICMP sockets need administrator rights.
        </div>
      )}

      {points.length > 0 && (
        <svg
          viewBox={`0 0 ${MAP_W} ${MAP_H}`}
          style={{ width: '100%', maxHeight: 260, marginBottom: '0.4rem' }}
          role="img"
          aria-label="Traceroute hops on a world map"
        >
          <path d={LAND} fill="var(--bg-hover, rgba(128,128,128,0.18))" stroke="none" />
          <polyline
            points={points.map(({ xy }) => xy.join(',')).join(' ')}
            fill="none"
            stroke="var(--accent, #38bdf8)"
            strokeWidth={1.5}
            opacity={0.7}
          />
          {points.map(({ hop, xy }) => (
            <circle
              key={hop.ttl}
              cx={xy[0]}
              cy={xy[1]}
              r={3}
              fill="var(--accent, #38bdf8)"
              stroke="var(--bg, #111)"
              strokeWidth={0.8}
            >
              <title>{`${hop.ttl}. ${hop.host || hop.ip} — ${hop.geo?.city ?? ''} ${hop.geo?.country ?? ''}`}</title>
            </circle>
          ))}
        </svg>
      )}

      {result && result.hops.length > 0 && (
        <>
          {result.hops.map((hop) => (
            <HopRow key={hop.ttl} hop={hop} />
          ))}
          <div className="dashboard-hint" style={{ marginTop: '0.4rem' }}>
            {result.hops.length} hop(s) in {result.elapsed_ms}ms.
            {geo?.available
              ? ` ${geo.attribution}.`
              : ' Locations need the geoip extra — see settings.'}
          </div>
        </>
      )}
    </div>
  );
}
