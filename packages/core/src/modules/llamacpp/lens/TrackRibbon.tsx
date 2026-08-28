/**
 * Where the model decides on a word.
 *
 * `LensTrack` carries `logits[layer][position]` **and** `ranks[layer][position]`,
 * and the pane used only the ranks — as a superscript inside a grid cell and a
 * tint behind it. The logits were fetched on every track and thrown away.
 *
 * That is the whole point of a logit lens and it had no picture. "Paris" does not
 * fade in evenly across the depth: it sits near the floor for two thirds of the
 * stack and then climbs steeply over a handful of layers. A per-cell tint cannot
 * show a knee; a curve can, and the knee is the finding.
 *
 * ## Two encodings, deliberately
 *
 * The **line** is the logit, which is the quantity the model actually computes.
 * The **band** underneath is the rank, which is what the logit means relative to
 * every other token — a rising logit that stays at rank 4000 has not decided
 * anything. Drawing only one of them makes a different chart that answers half the
 * question, so both are here and the rank is drawn as context rather than as a
 * second line competing with the first.
 */
import { useMemo, useState } from 'react';

import { Sparkline } from '../../../viz/Sparkline';
import type { LensTrack } from '../api';

/** Rank at or below this reads as "the model would say this now". */
const DECIDED_RANK = 1;

export function TrackRibbon({
  track,
  position,
  onPosition,
}: {
  track: LensTrack;
  /** Which token column the curve is read at. */
  position: number;
  onPosition: (position: number) => void;
}) {
  const [hover, setHover] = useState<number | null>(null);

  const column = track.positions.indexOf(position);
  const series = useMemo(() => {
    if (column < 0) return [];
    return track.layers.map((layer, row) => ({
      layer,
      logit: track.logits[row]?.[column] ?? null,
      rank: track.ranks[row]?.[column] ?? null,
    }));
  }, [track, column]);

  const measured = series.filter((p) => p.logit !== null);
  if (column < 0 || measured.length < 2) {
    return (
      <p className="llama-note">
        No track for <code>{track.text}</code> at this position — pick a prompt token whose column
        this trace captured.
      </p>
    );
  }

  // The first layer at which this token is the model's top choice. The single
  // most useful number here, and it is what the curve's knee is pointing at.
  const decidedAt = series.find((p) => p.rank !== null && p.rank <= DECIDED_RANK)?.layer ?? null;
  const shown = hover !== null ? series.find((p) => p.layer === hover) : null;

  return (
    <div className="llama-ribbon">
      <div className="llama-ribbon-head">
        <b>{track.text || '␣'}</b>
        <span className="llama-meta">logit by layer at position {position}</span>
        {decidedAt !== null ? (
          <span className="llama-tag llama-ok">top choice from layer {decidedAt}</span>
        ) : (
          /* Said rather than left blank: a token that never wins is a real and
             common answer, and an absent badge would read as a missing feature. */
          <span className="llama-tag">never the top choice in this pass</span>
        )}
      </div>

      <Sparkline
        points={series.map((p) => ({ x: p.layer, y: p.logit }))}
        width={320}
        height={64}
        label={`${track.text} logit across ${series.length} layers`}
      />

      {/* The rank, as a band under the curve. Log-scaled, because the difference
          between rank 1 and rank 10 is the whole story and the difference between
          40,000 and 50,000 is noise. */}
      <div className="llama-ribbon-ranks" role="img" aria-label={`${track.text} rank by layer`}>
        {series.map((p) => (
          <span
            key={p.layer}
            className={`llama-ribbon-rank${p.rank !== null && p.rank <= DECIDED_RANK ? ' llama-ribbon-won' : ''}`}
            style={{
              // rank 1 -> full, and it falls away logarithmically from there.
              opacity:
                p.rank === null ? 0 : Math.max(0.08, 1 - Math.log10(Math.max(1, p.rank)) / 5),
            }}
            title={
              p.rank === null
                ? `layer ${p.layer}: not measured`
                : `layer ${p.layer}: rank ${p.rank}${p.logit === null ? '' : `, logit ${p.logit.toFixed(2)}`}`
            }
            onMouseEnter={() => setHover(p.layer)}
            onMouseLeave={() => setHover(null)}
          />
        ))}
      </div>

      <div className="llama-ribbon-axis">
        <span>layer {series[0].layer}</span>
        <span>
          {shown
            ? `layer ${shown.layer} · rank ${shown.rank ?? '—'} · logit ${shown.logit?.toFixed(2) ?? '—'}`
            : 'shade is rank, log-scaled — solid means it is winning'}
        </span>
        <span>{series[series.length - 1].layer}</span>
      </div>

      {track.positions.length > 1 && (
        <div className="llama-ribbon-positions">
          <span className="llama-meta">Read at</span>
          {track.positions.map((p) => (
            <button
              key={p}
              className={`llama-chip${p === position ? ' llama-chip-on' : ''}`}
              onClick={() => onPosition(p)}
            >
              {p}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
