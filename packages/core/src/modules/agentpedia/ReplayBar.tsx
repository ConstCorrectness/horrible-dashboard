/**
 * The stepper's transport: play / pause, speed, and the turn's timeline.
 *
 * The timeline is the turn at a glance. Each round is a segment as wide as its
 * wall time; inside it, each thing the agent did is a marker (● ok, ✕ failed,
 * ◆ gated — the shape carries the state, the colour only repeats it), and each
 * request that went over the wire is a tick along the top. The playhead sweeps
 * across while playing; clicking or dragging anywhere on the track seeks there.
 *
 * Time arithmetic lives in `replay.ts`.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { RoundView } from './api';
import {
  advance,
  locate,
  playheadAt,
  progressAt,
  segmentWeights,
  type ReplayRound,
} from './replay';
import * as S from './styles';
import './replay.css';

const SPEEDS = [0.5, 1, 2, 4] as const;

export interface Replay {
  playing: boolean;
  speed: number;
  /** Progress in round units (see replay.ts). */
  t: number;
  toggle: () => void;
  setSpeed: (speed: number) => void;
  /** Jump to progress `t` (pauses nothing: a seek while playing keeps playing). */
  seek: (t: number) => void;
  /** Stop playing and sit at the start of round `i` — manual stepping. */
  park: (round: number) => void;
}

function replayRounds(rounds: RoundView[]): ReplayRound[] {
  return rounds.map((r) => ({ wallMs: r.cost.wall_ms, steps: r.did.length }));
}

/** Playback state for a turn; drives the stepper's round through `onRound`. */
export function useReplay(rounds: RoundView[], onRound: (index: number) => void): Replay {
  const paced = useMemo(() => replayRounds(rounds), [rounds]);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [t, setT] = useState(0);
  const tRef = useRef(0);
  const lastRound = useRef(0);

  const apply = useCallback(
    (next: number) => {
      tRef.current = next;
      setT(next);
      const { round } = locate(next, paced.length);
      if (round !== lastRound.current) {
        lastRound.current = round;
        onRound(round);
      }
    },
    [paced.length, onRound],
  );

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      // A backgrounded tab delivers one huge frame on return; cap it so the replay
      // resumes where it was rather than jumping to the end.
      const dt = Math.min(0.25, (now - last) / 1000);
      last = now;
      const next = advance(tRef.current, dt, speed, paced);
      apply(next);
      if (next >= paced.length) {
        setPlaying(false);
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, paced, apply]);

  // A new turn starts from the top, paused.
  useEffect(() => {
    setPlaying(false);
    tRef.current = 0;
    lastRound.current = 0;
    setT(0);
  }, [rounds]);

  const park = useCallback((round: number) => {
    setPlaying(false);
    lastRound.current = round;
    tRef.current = round;
    setT(round);
  }, []);

  return {
    playing,
    speed,
    t,
    toggle: () => {
      // Play from the top once the end has been reached.
      if (!playing && tRef.current >= paced.length) apply(0);
      setPlaying((p) => !p);
    },
    setSpeed,
    seek: apply,
    park,
  };
}

export function ReplayBar({ rounds, replay }: { rounds: RoundView[]; replay: Replay }) {
  const weights = useMemo(() => segmentWeights(replayRounds(rounds)), [rounds]);
  const track = useRef<HTMLDivElement>(null);
  const head = playheadAt(replay.t, weights);
  const { round } = locate(replay.t, rounds.length);

  const seekTo = (clientX: number) => {
    const el = track.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    replay.seek(progressAt((clientX - rect.left) / rect.width, weights));
  };

  return (
    <div className="replay-bar">
      <button
        type="button"
        style={replay.playing ? S.primaryButton : S.ghostButton}
        onClick={replay.toggle}
        aria-label={replay.playing ? 'Pause the replay' : 'Replay this turn'}
        title={replay.playing ? 'Pause' : 'Replay the turn round by round'}
      >
        {replay.playing ? '❚❚' : '▶'} {replay.playing ? 'Pause' : 'Replay'}
      </button>
      <div className="replay-speeds" role="group" aria-label="Replay speed">
        {SPEEDS.map((s) => (
          <button
            key={s}
            type="button"
            className={`replay-speed${replay.speed === s ? ' is-on' : ''}`}
            aria-pressed={replay.speed === s}
            onClick={() => replay.setSpeed(s)}
          >
            {s}×
          </button>
        ))}
      </div>
      <div
        ref={track}
        className="replay-track"
        role="slider"
        tabIndex={0}
        aria-label="Turn timeline"
        aria-valuemin={0}
        aria-valuemax={rounds.length}
        aria-valuenow={Number(replay.t.toFixed(2))}
        aria-valuetext={`round ${round + 1} of ${rounds.length}`}
        onPointerDown={(e) => {
          (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
          seekTo(e.clientX);
        }}
        onPointerMove={(e) => {
          if (e.buttons & 1) seekTo(e.clientX);
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowRight')
            replay.seek(Math.min(rounds.length, Math.floor(replay.t) + 1));
          if (e.key === 'ArrowLeft') replay.seek(Math.max(0, Math.ceil(replay.t) - 1));
        }}
      >
        {rounds.map((r, i) => (
          <div
            key={r.round}
            className={`replay-seg${i === round ? ' is-current' : ''}${i < round ? ' is-past' : ''}`}
            style={{ flexBasis: `${weights[i] * 100}%` }}
            title={`Round ${r.round} · ${r.did.length} step${r.did.length === 1 ? '' : 's'} · ${r.wire.length} request${r.wire.length === 1 ? '' : 's'}${r.cost.wall_ms != null ? ` · ${(r.cost.wall_ms / 1000).toFixed(1)}s` : ''}`}
          >
            <span className="replay-seg-label">R{r.round}</span>
            <span className="replay-wire" aria-hidden="true">
              {r.wire.map((w, j) => (
                <i
                  key={w.id}
                  className={w.error || (w.status ?? 0) >= 400 ? 'is-bad' : ''}
                  style={{ left: `${((j + 1) / (r.wire.length + 1)) * 100}%` }}
                />
              ))}
            </span>
            <span className="replay-steps" aria-hidden="true">
              {r.did.map((s, j) => {
                const kind = s.gated ? 'gated' : s.ok === false ? 'bad' : 'ok';
                return (
                  <i
                    key={s.seq}
                    className={`replay-step is-${kind}`}
                    style={{ left: `${((j + 1) / (r.did.length + 1)) * 100}%` }}
                  >
                    {kind === 'bad' ? '✕' : kind === 'gated' ? '◆' : '●'}
                  </i>
                );
              })}
            </span>
          </div>
        ))}
        <span className="replay-head" style={{ left: `${head * 100}%` }} aria-hidden="true" />
      </div>
      <span className="replay-legend" aria-hidden="true">
        <span className="is-ok">● ok</span> <span className="is-bad">✕ failed</span>{' '}
        <span className="is-gated">◆ gated</span> <span>| request</span>
      </span>
    </div>
  );
}
