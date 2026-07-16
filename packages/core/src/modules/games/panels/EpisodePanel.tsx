import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react';

import { episodeFromReplay, type Episode, type EpisodeStep } from '../episode';
import { useLiveEpisode } from '../episode-live';
import { fetchReplay, fetchReplays, type ReplaySummary } from '../games-api';
import { TraceRow } from './TraceRow';

/**
 * **Episodes** (`games.episodes`) — an episode as a trajectory you can step through:
 * per-step observation, the action taken, the reasoning behind it, and the reward.
 *
 * The board renders the *current* position and the log renders a *stream*; neither
 * answers "what did my agent see when it made that move?". Scrub the timeline and
 * this does. It reads the live match by default and can load any past replay — the
 * same view either way (see episode.ts, which normalizes both sources).
 *
 * Live episodes pin to the newest step unless you scrub back, at which point the
 * pane holds your position rather than yanking you to the tail mid-read.
 *
 * See docs/modules/games.mdx.
 */

const label: CSSProperties = {
  fontFamily: 'var(--font-mono, monospace)',
  fontSize: '0.62rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: 'var(--text-faint, #666)',
};

const btn: CSSProperties = {
  fontSize: '0.7rem',
  padding: '0.15rem 0.4rem',
  borderRadius: 6,
  border: '1px solid var(--border, #33343a)',
  background: 'transparent',
  color: 'var(--text-dim)',
  cursor: 'pointer',
};

export function EpisodePanel() {
  const liveEpisode = useLiveEpisode();
  const [loaded, setLoaded] = useState<Episode | null>(null);
  const [replays, setReplays] = useState<ReplaySummary[]>([]);
  const [cursor, setCursor] = useState(0);
  // Follow the live tail until the reader scrubs back.
  const [pinned, setPinned] = useState(true);
  const [error, setError] = useState('');

  const episode = loaded ?? liveEpisode;
  const steps = episode.steps;
  const showing = loaded === null;

  useEffect(() => {
    fetchReplays('mine')
      .then((r) => setReplays(r.replays ?? []))
      .catch(() => setReplays([]));
  }, []);

  // A live episode pins to its newest step; scrubbing back unpins.
  useEffect(() => {
    if (showing && pinned && steps.length > 0) setCursor(steps.length - 1);
  }, [showing, pinned, steps.length]);

  const scrub = useCallback(
    (i: number) => {
      setCursor(i);
      if (showing) setPinned(i >= steps.length - 1);
    },
    [showing, steps.length],
  );

  const loadReplay = useCallback(async (id: string) => {
    setError('');
    if (!id) {
      setLoaded(null);
      setCursor(0);
      setPinned(true);
      return;
    }
    const res = await fetchReplay(id);
    if (res.error || !res.replay) {
      setError(res.error ?? 'replay not found');
      return;
    }
    setLoaded(episodeFromReplay(res.replay));
    setCursor(0);
    setPinned(false);
  }, []);

  const step: EpisodeStep | undefined = steps[Math.min(cursor, steps.length - 1)];

  const title = useMemo(() => {
    if (loaded) return `${loaded.gameId ?? 'game'} · replay`;
    if (liveEpisode.gameId) return `${liveEpisode.gameId} · ${liveEpisode.live ? 'live' : 'ended'}`;
    return 'no episode';
  }, [loaded, liveEpisode]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', fontSize: '0.78rem' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '0.35rem 0.6rem',
          borderBottom: '1px solid var(--border)',
        }}
      >
        <span style={{ color: 'var(--text-dim)' }}>🎞 {title}</span>
        <span style={{ flex: 1 }} />
        <select
          value={loaded?.replayId ?? ''}
          onChange={(e) => void loadReplay(e.target.value)}
          style={{
            ...btn,
            maxWidth: 190,
            background: 'var(--surface, #16171d)',
            color: 'var(--text)',
          }}
        >
          <option value="">Live episode</option>
          {replays.map((r) => (
            <option key={r.id} value={r.id}>
              {r.game_id} · {new Date(r.created_at * 1000).toLocaleString()}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <div style={{ padding: '0.4rem 0.6rem', color: 'var(--danger, #f87171)' }}>{error}</div>
      )}

      {steps.length === 0 ? (
        <div style={{ padding: '0.6rem', color: 'var(--text-dim)' }}>
          No steps yet. Start a match and each decision your agent makes lands here — its
          observation, its reasoning, the action it committed, and what it scored. Or pick a past
          replay above.
        </div>
      ) : (
        <>
          {/* Timeline */}
          <div style={{ padding: '0.45rem 0.6rem', borderBottom: '1px solid var(--border)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <button type="button" style={btn} onClick={() => scrub(Math.max(0, cursor - 1))}>
                ‹
              </button>
              <input
                type="range"
                min={0}
                max={steps.length - 1}
                value={Math.min(cursor, steps.length - 1)}
                onChange={(e) => scrub(Number(e.target.value))}
                style={{ flex: 1 }}
              />
              <button
                type="button"
                style={btn}
                onClick={() => scrub(Math.min(steps.length - 1, cursor + 1))}
              >
                ›
              </button>
              <span style={{ ...label, minWidth: 62, textAlign: 'right' }}>
                step {Math.min(cursor, steps.length - 1) + 1}/{steps.length}
              </span>
            </div>
            {showing && episode.live && !pinned && (
              <button
                type="button"
                onClick={() => setPinned(true)}
                style={{ ...btn, marginTop: 5, width: '100%' }}
              >
                ↓ follow live
              </button>
            )}
          </div>

          {step && <StepDetail step={step} episode={episode} />}
        </>
      )}
    </div>
  );
}

function StepDetail({ step, episode }: { step: EpisodeStep; episode: Episode }) {
  const seatName = episode.seats[step.seat] ?? `seat ${step.seat}`;
  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '0.5rem 0.6rem', display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span
          style={{
            padding: '0.1rem 0.4rem',
            borderRadius: 999,
            border: '1px solid var(--accent, #6ea8fe)',
            color: 'var(--accent, #6ea8fe)',
            fontSize: '0.68rem',
          }}
        >
          {seatName}
        </span>
        {step.action !== null ? (
          <span>
            played <code>{step.action}</code>
            {step.timeout && <span style={{ color: 'var(--danger, #f87171)' }}> · timed out</span>}
          </span>
        ) : (
          <span style={{ color: 'var(--text-dim)' }}>deciding…</span>
        )}
        {step.reward !== null && (
          <span
            style={{
              marginLeft: 'auto',
              color: step.reward > 0 ? 'var(--gold, #f5b942)' : 'var(--text-dim)',
              fontFamily: 'var(--font-mono, monospace)',
            }}
          >
            reward {step.reward > 0 ? '+' : ''}
            {step.reward}
          </span>
        )}
      </div>

      {step.legalActions.length > 0 && (
        <Field title={`Legal actions (${step.legalActions.length})`}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {step.legalActions.map((a) => (
              <span
                key={a.id}
                style={{
                  padding: '0.1rem 0.35rem',
                  borderRadius: 5,
                  fontSize: '0.68rem',
                  fontFamily: 'var(--font-mono, monospace)',
                  border: `1px solid ${
                    a.id === step.action ? 'var(--accent, #6ea8fe)' : 'var(--border, #33343a)'
                  }`,
                  color: a.id === step.action ? 'var(--accent, #6ea8fe)' : 'var(--text-dim)',
                }}
              >
                {a.label || a.id}
              </span>
            ))}
          </div>
        </Field>
      )}

      {step.trace.length > 0 && (
        <Field title={`Reasoning (${step.trace.length} steps)`}>
          {step.trace.map((t, i) => (
            <TraceRow key={i} step={t} />
          ))}
        </Field>
      )}

      {step.obs && <Field title="Observation">{<Json value={step.obs} />}</Field>}
      {step.state && <Field title="Resulting state">{<Json value={step.state} />}</Field>}
    </div>
  );
}

function Field({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'grid', gap: 4 }}>
      <span style={label}>{title}</span>
      {children}
    </div>
  );
}

function Json({ value }: { value: unknown }) {
  return (
    <pre
      style={{
        margin: 0,
        padding: '0.4rem',
        borderRadius: 6,
        background: 'var(--bg, #1c1c1c)',
        border: '1px solid var(--border, #33343a)',
        fontSize: '0.68rem',
        color: 'var(--text-dim)',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        maxHeight: 220,
        overflow: 'auto',
      }}
    >
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
