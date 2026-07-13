import { useGames, type PublicState } from '../game-ws';

interface VizDoomHud {
  health: number;
  ammo: number;
  score: number;
}

const SEAT_COLORS = ['#60a5fa', '#f87171']; // blue vs red
const DEFAULT_AVATARS = ['🥷', '🤖'];

/**
 * ViZDoom board: the engine (a real native Doom instance per seat, running on the
 * game server) streams a small JPEG of each marine's first-person view in
 * `public_state().frames`. We just draw the two viewports side by side with a HUD
 * overlay — no client-side raycasting. See backend/games_engine/vizdoom_toy.py.
 */
export function VizDoomBoard({ board }: { board: PublicState }) {
  const { matchSeats } = useGames();

  const frames = (board.frames as string[]) ?? [];
  const hud = (board.hud as VizDoomHud[]) ?? [];
  const winner = board.winner as number | null | undefined;
  const tick = (board.tick as number) ?? 0;
  const maxTicks = (board.max_ticks as number) ?? 0;

  return (
    <div className="vzd-board">
      <div className="vzd-viewports">
        {[0, 1].map((seat) => {
          const avatar = matchSeats?.[seat]?.avatar || DEFAULT_AVATARS[seat];
          const name = matchSeats?.[seat]?.display_name || `Marine ${seat}`;
          const h = hud[seat];
          const won = winner === seat;
          const lost = winner != null && winner !== seat;
          return (
            <div
              key={seat}
              className={`vzd-viewport${won ? ' vzd-won' : ''}${lost ? ' vzd-lost' : ''}`}
              style={{ borderColor: SEAT_COLORS[seat] }}
            >
              <div className="vzd-hud" style={{ color: SEAT_COLORS[seat] }}>
                <span className="vzd-hud-name">
                  {avatar} {name}
                </span>
                <span className="vzd-hud-stats">
                  <span className={h && h.health <= 30 ? 'vzd-hp-low' : undefined}>
                    ❤ {h ? Math.round(h.health) : '—'}
                  </span>
                  <span>🔫 {h ? Math.round(h.ammo) : '—'}</span>
                  <span className="vzd-hud-score">★ {h ? Math.round(h.score) : 0}</span>
                </span>
              </div>
              {frames[seat] ? (
                <img className="vzd-frame" src={frames[seat]} alt={`${name} view`} />
              ) : (
                <div className="vzd-frame vzd-frame-empty">connecting…</div>
              )}
              {won && <div className="vzd-badge vzd-badge-won">WIN</div>}
            </div>
          );
        })}
      </div>
      <div className="vzd-clock">
        {winner != null
          ? `${matchSeats?.[winner]?.display_name || `Marine ${winner}`} wins the round`
          : `defend the center · tick ${tick}${maxTicks ? ` / ${maxTicks}` : ''}`}
      </div>
    </div>
  );
}
