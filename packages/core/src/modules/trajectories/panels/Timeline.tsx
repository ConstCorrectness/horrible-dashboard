/**
 * The run waterfall: rounds down the page, wall-clock left to right.
 *
 * Deliberately **not** extracted into a shared `viz/Timeline`. There is exactly one
 * consumer, and the generic version of this would have to take a colour mapper, a
 * nesting strategy and a label renderer as props — three abstractions invented for
 * a second caller that does not exist.
 *
 * Kept beside the flat step list rather than replacing it. They answer different
 * questions: the timeline shows where a run spent its time, the list shows what it
 * did, and a 200-step run is unreadable as bars while a 12-step one is unreadable
 * as rows.
 */
import { useState } from 'react';

import { axisTicks, buildTimeline, type TimelineBar } from './timeline-model';
import { heading, mono, ms } from './common';
import type { TrajectoryStep } from '../api';

/** Semantic colour per step kind. Tokens only — never a hex literal. */
function tone(bar: TimelineBar): string {
  if (bar.failed) return 'var(--danger)';
  if (bar.gated) return 'var(--warning)';
  if (bar.kind === 'message' || bar.kind === 'thought') return 'var(--text-secondary)';
  if (bar.kind === 'error') return 'var(--danger)';
  return 'var(--accent)';
}

function Bar({
  bar,
  selected,
  onSelect,
}: {
  bar: TimelineBar;
  selected: boolean;
  onSelect: (seq: number) => void;
}) {
  const colour = tone(bar);
  return (
    <button
      className="traj-bar"
      title={`#${bar.seq} ${bar.label} — ${bar.instant ? 'instant' : ms(bar.durationMs)}`}
      onClick={() => onSelect(bar.seq)}
      style={{
        left: `${bar.left * 100}%`,
        width: `${bar.width * 100}%`,
        background: colour,
        // An instant has no duration to show, so it reads as a tick rather than a
        // bar too short to be believed.
        opacity: bar.instant ? 0.55 : 1,
        outline: selected ? `2px solid ${colour}` : 'none',
      }}
    >
      <span className="traj-bar-label">{bar.label}</span>
    </button>
  );
}

export function Timeline({
  steps,
  startedAt,
  onSelect,
  selectedSeq,
}: {
  steps: TrajectoryStep[];
  startedAt?: number;
  onSelect?: (seq: number) => void;
  selectedSeq?: number | null;
}) {
  const [hoverSeq, setHoverSeq] = useState<number | null>(null);
  const model = buildTimeline(steps, startedAt);

  if (!model.rounds.length) {
    return (
      <div style={{ ...mono, padding: 'var(--space-4) 0' }}>
        No timed steps yet — a run shows its shape as soon as it acts.
      </div>
    );
  }

  const select = (seq: number) => {
    setHoverSeq(seq);
    onSelect?.(seq);
  };
  const active = selectedSeq ?? hoverSeq;

  return (
    <div style={{ marginBottom: 'var(--space-5)' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: 'var(--space-3)',
          marginBottom: 'var(--space-3)',
        }}
      >
        <span style={heading}>Timeline</span>
        <span style={mono}>
          {model.rounds.length} {model.rounds.length === 1 ? 'round' : 'rounds'} ·{' '}
          {ms(model.spanMs)}
        </span>
      </div>

      <div className="traj-timeline">
        {model.rounds.map((round) => (
          <div key={round.round} className="traj-lane">
            <span className="traj-lane-name" style={mono}>
              r{round.round}
            </span>
            <div className="traj-track">
              {round.bars.map((bar) => (
                <Bar
                  key={bar.seq}
                  bar={bar}
                  selected={active === bar.seq}
                  onSelect={select}
                />
              ))}
            </div>
          </div>
        ))}

        <div className="traj-lane traj-axis">
          <span className="traj-lane-name" />
          <div className="traj-track">
            {axisTicks(model.spanMs).map((tick) => (
              <span
                key={tick.at}
                className="traj-tick"
                style={{ ...mono, left: `${tick.at * 100}%` }}
              >
                {tick.label}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
