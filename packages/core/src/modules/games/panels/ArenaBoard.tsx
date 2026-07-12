import { useEffect, useRef, useState } from 'react';

import { type PublicState } from '../game-ws';

interface Tick {
  p: [number, number][];
  s: number[];
  pellets: [number, number][];
}
interface RoundLog {
  round: number;
  scores: number[];
  winner: number | null;
  forfeits: boolean[];
  ticks: Tick[];
}

const CELL = 26;
const COLORS = ['#6ea8fe', '#e5a13f'];

/** Arena: edit-phase status live; per-round tick logs play back on a canvas
 * (scrub + autoplay) so you can study how your bot lost and iterate. */
export function ArenaBoard({ board }: { board: PublicState }) {
  const logs = (board.round_logs as RoundLog[]) ?? [];
  const grid = Number(board.grid ?? 9);
  const wins = (board.round_wins as number[]) ?? [0, 0];
  const phase = String(board.phase ?? 'edit');
  const [roundIdx, setRoundIdx] = useState(0);
  const [tick, setTick] = useState(0);
  const [playing, setPlaying] = useState(true);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Follow the newest round as it lands.
  useEffect(() => {
    if (logs.length > 0) setRoundIdx(logs.length - 1);
  }, [logs.length]);

  const round = logs[roundIdx];
  const ticks = round?.ticks ?? [];

  useEffect(() => {
    if (!playing || ticks.length === 0) return;
    const id = setInterval(() => setTick((t) => (t + 1 < ticks.length ? t + 1 : t)), 80);
    return () => clearInterval(id);
  }, [playing, ticks.length, roundIdx]);

  useEffect(() => setTick(0), [roundIdx]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const frame = ticks[tick];
    if (!canvas || !frame) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const size = grid * CELL;
    ctx.clearRect(0, 0, size, size);
    ctx.strokeStyle = 'rgba(128,128,128,0.25)';
    for (let i = 0; i <= grid; i++) {
      ctx.beginPath();
      ctx.moveTo(i * CELL, 0);
      ctx.lineTo(i * CELL, size);
      ctx.moveTo(0, i * CELL);
      ctx.lineTo(size, i * CELL);
      ctx.stroke();
    }
    ctx.fillStyle = '#9b8b3f';
    for (const [px, py] of frame.pellets) {
      ctx.beginPath();
      ctx.arc(px * CELL + CELL / 2, py * CELL + CELL / 2, 4, 0, Math.PI * 2);
      ctx.fill();
    }
    frame.p.forEach(([x, y], i) => {
      ctx.fillStyle = COLORS[i];
      ctx.beginPath();
      ctx.arc(x * CELL + CELL / 2, y * CELL + CELL / 2, CELL / 2 - 3, 0, Math.PI * 2);
      ctx.fill();
    });
  }, [tick, ticks, grid]);

  return (
    <div
      style={{
        padding: '0.7rem',
        fontSize: '0.85rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
      }}
    >
      <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <strong>🤖 Arena</strong>
        <span className="games-tier-chip">
          phase: {phase} · round {board.round as number}/{board.rounds as number}
        </span>
        <span className="games-series-pips">🏁 {wins.join('–')}</span>
        {phase === 'compete' && (
          <span style={{ color: 'var(--accent, #6ea8fe)' }}>⚙️ simulating…</span>
        )}
      </div>

      {logs.length === 0 ? (
        <div style={{ color: 'var(--text-dim)' }}>
          Submit your <code>bot(obs)</code> in the harness. The first round's fight will play here.
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', flexWrap: 'wrap' }}>
            {logs.map((r, i) => (
              <button
                key={i}
                type="button"
                className={i === roundIdx ? 'games-tab-active' : undefined}
                onClick={() => setRoundIdx(i)}
              >
                R{r.round} {r.winner === null ? '=' : r.winner === 0 ? '🔵' : '🟠'}{' '}
                {r.scores.join(':')}
              </button>
            ))}
          </div>
          <canvas
            ref={canvasRef}
            width={grid * CELL}
            height={grid * CELL}
            style={{ border: '1px solid var(--border)', borderRadius: 6, alignSelf: 'flex-start' }}
          />
          {round && (
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <button type="button" onClick={() => setPlaying((p) => !p)}>
                {playing ? '⏸' : '▶'}
              </button>
              <input
                type="range"
                min={0}
                max={Math.max(0, ticks.length - 1)}
                value={tick}
                onChange={(e) => {
                  setPlaying(false);
                  setTick(Number(e.target.value));
                }}
                style={{ flex: 1 }}
              />
              <span style={{ color: 'var(--text-dim)', whiteSpace: 'nowrap' }}>
                tick {tick + 1}/{ticks.length}
              </span>
              {ticks[tick] && (
                <span style={{ fontWeight: 700 }}>
                  <span style={{ color: COLORS[0] }}>{ticks[tick].s[0]}</span> :{' '}
                  <span style={{ color: COLORS[1] }}>{ticks[tick].s[1]}</span>
                </span>
              )}
              {round.forfeits.some(Boolean) && (
                <span style={{ color: '#e5534b' }}>
                  {round.forfeits[0] ? 'seat 0' : 'seat 1'} crashed
                </span>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
