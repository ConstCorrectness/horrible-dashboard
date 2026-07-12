import { useEffect, useMemo, useState } from 'react';

import { toastsStore } from '../../../toasts';
import { fetchReplay, publishReplay, type Replay, type ReplayEvent } from '../games-api';
import { type PublicState } from '../game-ws';
import { claimReplayFocus, onReplayFocus } from '../replay-focus';
import { BoardRenderer } from './GameBoardPanel';

const KIND_ICON: Record<string, string> = {
  assistant: '💬',
  tool_result: '📥',
  chose: '✅',
  fallback: '🎲',
};

/** One seat's reasoning column: every trace step it uploaded, up to the scrub point. */
function TraceColumn({ title, traces }: { title: string; traces: ReplayEvent[] }) {
  return (
    <div className="games-replay-trace-col">
      <div className="games-replay-trace-title">{title}</div>
      {traces.length === 0 ? (
        <div style={{ color: 'var(--text-dim)' }}>no reasoning uploaded</div>
      ) : (
        traces.map((t, i) => (
          <div key={i} className="games-replay-move">
            <div style={{ color: 'var(--text-dim)' }}>move: {t.action_id ?? '?'}</div>
            {(t.steps ?? []).map((s, j) => (
              <div key={j} className="games-trace-step" data-kind={s.kind}>
                <span className="games-trace-icon">{KIND_ICON[String(s.kind)] ?? '·'}</span>
                <div className="games-trace-body">
                  {String(s.content ?? s.result ?? s.action_id ?? '')}
                  {Array.isArray(s.tool_calls) &&
                    (s.tool_calls as { name: string; arguments: string }[]).map((c, k) => (
                      <div key={k} className="games-trace-call">
                        🔧 {c.name}({c.arguments})
                      </div>
                    ))}
                </div>
              </div>
            ))}
          </div>
        ))
      )}
    </div>
  );
}

/**
 * Post-match replay: scrub the game's public states on a timeline and study BOTH
 * agents' reasoning side by side — the info that was private during the match.
 */
export function ReplayViewerPanel() {
  const [replayId, setReplayId] = useState<string | null>(claimReplayFocus());
  const [replay, setReplay] = useState<Replay | null>(null);
  const [error, setError] = useState('');
  const [pos, setPos] = useState(0);

  useEffect(() => onReplayFocus(setReplayId), []);

  useEffect(() => {
    if (!replayId) return;
    setReplay(null);
    setError('');
    fetchReplay(replayId)
      .then((r) => {
        if (r.replay) {
          setReplay(r.replay);
          setPos(Math.max(0, r.replay.events.filter((e) => e.kind === 'public_state').length - 1));
        } else setError(r.error ?? 'replay not found');
      })
      .catch((e) => setError(String(e)));
  }, [replayId]);

  const states = useMemo(
    () => (replay?.events ?? []).filter((e) => e.kind === 'public_state'),
    [replay],
  );
  const traces = useMemo(() => (replay?.events ?? []).filter((e) => e.kind === 'trace'), [replay]);

  if (!replayId) {
    return (
      <div style={{ padding: '1rem', color: 'var(--text-dim)', fontSize: '0.85rem' }}>
        No replay selected. Open one from the Replays browser, the ladder, or the game-over banner.
      </div>
    );
  }
  if (error) {
    return <div style={{ padding: '1rem', fontSize: '0.85rem' }}>⚠ {error}</div>;
  }
  if (!replay) {
    return <div style={{ padding: '1rem', color: 'var(--text-dim)' }}>loading replay…</div>;
  }

  const board = (states[pos]?.state ?? null) as PublicState | null;
  const winnerName = replay.winner !== null ? replay.seats[replay.winner] : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', fontSize: '0.85rem' }}>
      <div
        style={{
          padding: '0.4rem 0.6rem',
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          alignItems: 'center',
          gap: '0.6rem',
          flexWrap: 'wrap',
        }}
      >
        <strong>📼 {replay.game_id}</strong>
        <span style={{ color: 'var(--text-dim)' }}>
          {replay.seats.join(' vs ')} · {winnerName ? `🏆 ${winnerName}` : '🤝 draw'}
        </span>
        {!replay.public && (
          <button
            type="button"
            onClick={() =>
              publishReplay(replay.id).then((r) =>
                r.ok
                  ? toastsStore.add('info', 'Games', 'Replay published')
                  : toastsStore.add('error', 'Games', r.error ?? 'publish failed'),
              )
            }
          >
            Publish
          </button>
        )}
        {replay.public && <span title="anyone can watch this replay">🌐 public</span>}
      </div>
      <div
        style={{ padding: '0.4rem 0.6rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}
      >
        <input
          type="range"
          min={0}
          max={Math.max(0, states.length - 1)}
          value={pos}
          onChange={(e) => setPos(Number(e.target.value))}
          style={{ flex: 1 }}
        />
        <span style={{ color: 'var(--text-dim)', whiteSpace: 'nowrap' }}>
          {pos + 1}/{states.length}
        </span>
      </div>
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        <div style={{ flex: 1, overflow: 'auto', borderRight: '1px solid var(--border)' }}>
          {board ? (
            <BoardRenderer board={board} />
          ) : (
            <div style={{ padding: '1rem', color: 'var(--text-dim)' }}>no board states</div>
          )}
        </div>
        <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'auto' }}>
          <TraceColumn
            title={`${replay.seats[0] ?? 'seat 0'} 💭`}
            traces={traces.filter((t) => t.seat === 0)}
          />
          <TraceColumn
            title={`${replay.seats[1] ?? 'seat 1'} 💭`}
            traces={traces.filter((t) => t.seat === 1)}
          />
        </div>
      </div>
    </div>
  );
}
