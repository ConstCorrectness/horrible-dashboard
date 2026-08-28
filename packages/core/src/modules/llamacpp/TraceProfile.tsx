/**
 * The shape of a forward pass: one statistic against depth, and the whole pass as
 * a fingerprint.
 *
 * The statistic selector is the point as much as the charts are: `rms` answers "how
 * big", `zeroFraction` answers "how sparse", and `absMax` answers "is there an
 * outlier". Those are different questions about the same pass, and the pane could
 * previously only ask the first — six of the seven statistics had no renderer at all.
 *
 * The numbers come from `GET /traces/{id}/profile`, not from `records[].summary`.
 * See `trace-profile.ts`: the manifest carries a summary only for the records that
 * hold no data, so the version of this that cost no request drew an empty chart on
 * every trace on disk.
 */
import { useEffect, useMemo, useState } from 'react';

import { HeatCanvas } from '../../viz/HeatCanvas';
import { Sparkline } from '../../viz/Sparkline';
import { getTraceProfile, type ProfilePoint } from './api';
import { KIND_LABELS } from './node-kind';
import {
  profilableRoles,
  profileByLayer,
  roleGrid,
  STAT_LABELS,
  TRACE_STATS,
  type TraceStat,
} from './trace-profile';

export function TraceProfile({
  traceId,
  passIndex,
  onSelect,
}: {
  traceId: string;
  passIndex: number;
  /** Clicking a point opens that record below, the same as picking it from the list. */
  onSelect: (index: number) => void;
}) {
  const [stat, setStat] = useState<TraceStat>('rms');
  const [hover, setHover] = useState<string | null>(null);
  const [all, setAll] = useState<ProfilePoint[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!traceId) return;
    let live = true;
    setLoading(true);
    // The pass and the statistic are the whole request. Which role is on screen is
    // not — that is arrangement of a response already in hand, so flipping between
    // nodes costs nothing.
    getTraceProfile(traceId, passIndex, stat)
      .then((res) => {
        if (!live) return;
        setAll(res.points);
        setError(res.error ?? '');
      })
      .catch((err: unknown) => {
        if (!live) return;
        // Surfaced, not swallowed: an empty profile and a failed request look
        // identical on screen otherwise, and one of them is a bug worth seeing.
        setAll([]);
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [traceId, passIndex, stat]);

  const roles = useMemo(() => profilableRoles(all), [all]);
  const [role, setRole] = useState('');
  const activeRole = role && roles.includes(role) ? role : (roles[0] ?? '');

  const points = useMemo(
    () => (activeRole ? profileByLayer(all, activeRole) : []),
    [all, activeRole],
  );
  const grid = useMemo(() => roleGrid(all), [all]);

  const measured = points.filter((p) => p.value !== null);
  if (error) return <p className="llama-note">Could not read this pass&rsquo;s profile: {error}</p>;
  if (roles.length === 0) return loading ? <p className="llama-meta">Reading the pass…</p> : null;

  return (
    <div className="llama-card">
      <h3>
        Profile{' '}
        <span className="llama-meta">
          {/* Said, not hidden: each point is one statistic over a whole captured
              tensor, not over the prefix `get_record` ships to the browser. */}
          every record of this pass, summarized whole
        </span>
      </h3>

      <div className="llama-row">
        <label>
          Statistic
          <select value={stat} onChange={(e) => setStat(e.target.value as TraceStat)}>
            {TRACE_STATS.map((s) => (
              <option key={s} value={s}>
                {STAT_LABELS[s]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Node
          <select value={activeRole} onChange={(e) => setRole(e.target.value)}>
            {roles.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <span className="llama-meta">
          {measured.length} of {points.length} layers measured
        </span>
      </div>

      {/* The depth profile. "Does the residual grow with depth" is the first
          question anyone asks of a traced pass, and it took eight manual pins to
          approximate before. */}
      {measured.length > 1 ? (
        <div className="llama-profile">
          <Sparkline
            points={points.map((p) => ({ x: p.layer, y: p.value }))}
            width={320}
            height={72}
            label={`${activeRole} ${STAT_LABELS[stat]} against layer`}
          />
          <div className="llama-profile-axis">
            <span>layer {points[0]?.layer ?? 0}</span>
            <span>
              {Math.min(...measured.map((p) => p.value as number)).toPrecision(3)} …{' '}
              {Math.max(...measured.map((p) => p.value as number)).toPrecision(3)}
            </span>
            <span>{points[points.length - 1]?.layer ?? 0}</span>
          </div>
        </div>
      ) : (
        <p className="llama-note">
          Fewer than two layers could be measured for <code>{activeRole}</code> at this
          statistic. A <code>summary</code>-fidelity record has no values to summarize, and
          carries a stored statistic only if one was written when it was captured.
        </p>
      )}

      {/* The whole pass at once. Rows are node kinds, columns are depth. */}
      {grid.kinds.length > 0 && (
        <div className="llama-fingerprint">
          <div className="llama-fingerprint-rows">
            {grid.kinds.map((kind) => (
              <span key={kind}>{KIND_LABELS[kind]}</span>
            ))}
          </div>
          <div className="llama-fingerprint-grid">
            <HeatCanvas
              data={grid.cells.flat()}
              rows={grid.kinds.length}
              cols={grid.layers.length}
              max={1}
              height={Math.max(48, grid.kinds.length * 18)}
              label={`${STAT_LABELS[stat]} by node kind and layer`}
              onHover={(cell) => {
                if (!cell) return setHover(null);
                const raw = grid.raw[cell.row]?.[cell.col];
                setHover(
                  `${KIND_LABELS[grid.kinds[cell.row]]} · layer ${grid.layers[cell.col]} · ` +
                    (raw === null || raw === undefined ? 'not measured' : raw.toPrecision(4)),
                );
              }}
            />
            <div className="llama-fingerprint-axis">
              <span>layer {grid.layers[0]}</span>
              <span>{grid.layers[grid.layers.length - 1]}</span>
            </div>
          </div>
          <p className="llama-why">
            {hover ??
              'Each row is scaled to its own maximum — an attention score and an FFN activation ' +
                'are different quantities, and one shared scale would render every row but the ' +
                'largest as blank.'}
          </p>
        </div>
      )}

      {measured.length > 1 && (
        <button
          className="llama-linkbtn"
          onClick={() => {
            const peak = measured.reduce((best, p) =>
              Math.abs(p.value as number) > Math.abs(best.value as number) ? p : best,
            );
            onSelect(peak.index);
          }}
        >
          Open the largest layer ({STAT_LABELS[stat].toLowerCase()})
        </button>
      )}
    </div>
  );
}
