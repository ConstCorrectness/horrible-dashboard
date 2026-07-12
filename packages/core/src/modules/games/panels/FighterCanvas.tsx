import { useEffect, useRef } from 'react';

import { type PublicState } from '../game-ws';

interface FighterFrame {
  x: number;
  y: number;
  hp: number;
  meter: number;
  facing: number;
  anim: string;
  stun: number;
}

const W = 640;
const H = 260;
const GROUND = H - 40;
const COLORS = ['#6ea8fe', '#e5a13f'];

/**
 * Renders the fighter's per-tick frame with interpolation between the ~1s server
 * ticks, so the ~3 frames/sec network cadence reads as smooth motion. Pure
 * spectator view (works for players and observers alike).
 */
export function FighterCanvas({ board }: { board: PublicState }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Interpolation state: previous + target frames and the time we received target.
  const prev = useRef<FighterFrame[] | null>(null);
  const target = useRef<FighterFrame[] | null>(null);
  const targetAt = useRef<number>(0);

  const players = (board.p as FighterFrame[]) ?? [];
  const stageW = Number(board.stage_w ?? 400);
  const maxHp = Number(board.max_hp ?? 100);
  const wins = (board.round_wins as number[]) ?? [0, 0];

  // On each new frame, shift target→prev and set the new target.
  useEffect(() => {
    if (players.length < 2) return;
    prev.current = target.current ?? players;
    target.current = players;
    targetAt.current = performance.now();
  }, [board.tick, players]);

  useEffect(() => {
    let raf = 0;
    const draw = () => {
      const canvas = canvasRef.current;
      const ctx = canvas?.getContext('2d');
      if (!ctx || !canvas) {
        raf = requestAnimationFrame(draw);
        return;
      }
      const a = prev.current;
      const b = target.current;
      ctx.clearRect(0, 0, W, H);
      // floor
      ctx.strokeStyle = 'rgba(128,128,128,0.4)';
      ctx.beginPath();
      ctx.moveTo(0, GROUND);
      ctx.lineTo(W, GROUND);
      ctx.stroke();
      if (a && b) {
        const t = Math.min(1, (performance.now() - targetAt.current) / 700);
        b.forEach((tf, i) => {
          const af = a[i] ?? tf;
          const x = (af.x + (tf.x - af.x) * t) * (W / stageW);
          const y = af.y + (tf.y - af.y) * t;
          const px = x;
          const py = GROUND - y - 40;
          ctx.fillStyle = COLORS[i];
          ctx.globalAlpha = tf.stun > 0 ? 0.6 : 1;
          ctx.fillRect(px - 12, py, 24, 40);
          // facing / action pip
          ctx.fillStyle = '#fff';
          ctx.fillRect(px - 12 + (tf.facing > 0 ? 16 : 4), py + 8, 4, 4);
          ctx.globalAlpha = 1;
          if (['light', 'heavy', 'special'].includes(tf.anim)) {
            ctx.fillStyle = tf.anim === 'special' ? '#ff5db1' : '#ffd24a';
            ctx.fillRect(px + tf.facing * 14 - 6, py + 12, 22, 8);
          }
        });
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [stageW]);

  const hpBar = (i: number) => {
    const hp = players[i]?.hp ?? maxHp;
    const meter = players[i]?.meter ?? 0;
    return (
      <div style={{ flex: 1 }}>
        <div
          style={{ height: 12, background: 'var(--border)', borderRadius: 6, overflow: 'hidden' }}
        >
          <div
            style={{
              width: `${(hp / maxHp) * 100}%`,
              height: '100%',
              background: COLORS[i],
              transition: 'width 0.2s',
              marginLeft: i === 1 ? 'auto' : 0,
            }}
          />
        </div>
        <div
          style={{
            height: 5,
            marginTop: 2,
            background: 'var(--border)',
            borderRadius: 4,
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              width: `${meter}%`,
              height: '100%',
              background: '#ff5db1',
              marginLeft: i === 1 ? 'auto' : 0,
            }}
          />
        </div>
      </div>
    );
  };

  return (
    <div style={{ padding: '0.6rem', display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
      <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'center' }}>
        {hpBar(0)}
        <span style={{ fontWeight: 800, fontFamily: 'monospace' }}>
          {String(board.timer ?? '')}
        </span>
        {hpBar(1)}
      </div>
      <div style={{ textAlign: 'center', color: 'var(--text-dim)', fontSize: '0.75rem' }}>
        round {String(board.round ?? 1)} · 🏁 {wins.join('–')}
      </div>
      <canvas
        ref={canvasRef}
        width={W}
        height={H}
        style={{ border: '1px solid var(--border)', borderRadius: 6, width: '100%', maxWidth: W }}
      />
    </div>
  );
}
