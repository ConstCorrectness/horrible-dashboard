import type { PublicState } from '../game-ws';

// Suit glyph + color class from a card string like "As" / "Td" / "7h".
const SUITS: Record<string, { glyph: string; red: boolean }> = {
  s: { glyph: '♠', red: false },
  h: { glyph: '♥', red: true },
  d: { glyph: '♦', red: true },
  c: { glyph: '♣', red: false },
};

/** One playing card. `card` null renders a face-down back; 'empty' a dashed slot. */
function PCard({ card }: { card: string | null | 'empty' }) {
  if (card === 'empty') return <div className="games-pcard games-pcard--empty" />;
  if (card === null) return <div className="games-pcard games-pcard--back" />;
  const suit = SUITS[card[1]] ?? SUITS.s;
  return (
    <div className={`games-pcard${suit.red ? ' games-pcard--red' : ''}`}>
      <span className="games-pcard-rank">{card[0] === 'T' ? '10' : card[0]}</span>
      <span className="games-pcard-suit">{suit.glyph}</span>
    </div>
  );
}

const SEAT_NAMES = ['Button', 'Big Blind'];

interface SeatRowProps {
  seat: number;
  state: PublicState;
}

function SeatRow({ seat, state }: SeatRowProps) {
  const stacks = (state.stacks as number[] | undefined) ?? [0, 0];
  const bets = (state.bets as number[] | undefined) ?? [0, 0];
  const revealed = (state.revealed as (string[] | null)[] | undefined) ?? [null, null];
  const handNames = (state.hand_names as (string | null)[] | undefined) ?? [null, null];
  const folded = state.folded as number | null | undefined;
  const winner = state.winner as number | null | undefined;
  const over = winner !== null && winner !== undefined;
  const isTurn = state.turn === seat;
  const hole = revealed[seat];

  const cls = [
    'games-poker-seat',
    isTurn ? 'games-poker-seat--active' : '',
    folded === seat ? 'games-poker-seat--folded' : '',
    over && winner === seat ? 'games-poker-seat--winner' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={cls}>
      <div style={{ display: 'flex', gap: '0.25rem' }}>
        {/* Hole cards: revealed at showdown, otherwise face-down backs. */}
        {(hole ?? [null, null]).map((c, i) => (
          <PCard key={c ?? `back-${seat}-${i}`} card={c} />
        ))}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem', flex: 1 }}>
        <span style={{ fontWeight: 700 }}>
          {SEAT_NAMES[seat] ?? `Seat ${seat}`}
          {over && winner === seat ? ' 🏆' : ''}
          {folded === seat ? ' · folded' : ''}
        </span>
        <span style={{ color: 'var(--text-dim)', fontSize: '0.78rem' }}>
          stack {stacks[seat]}
          {bets[seat] > 0 ? ` · bet ${bets[seat]}` : ''}
          {handNames[seat] ? ` · ${handNames[seat]}` : ''}
        </span>
      </div>
    </div>
  );
}

/**
 * The hold'em table: opponent seat on top, five community-card slots + the pot in
 * the middle, our seat below. Hole cards render as backs until the showdown
 * reveals them (the server only ever sends hidden state to its owner). Spectator
 * only — betting comes from the agents.
 */
export function PokerBoard({ board }: { board: PublicState }) {
  const community = (board.board as string[] | undefined) ?? [];
  const slots: (string | 'empty')[] = [
    ...community,
    ...Array<'empty'>(Math.max(0, 5 - community.length)).fill('empty'),
  ];
  return (
    <div className="games-poker">
      <SeatRow seat={1} state={board} />
      <div className="games-poker-board">
        {slots.map((c, i) => (
          <PCard key={c === 'empty' ? `slot-${i}` : c} card={c} />
        ))}
      </div>
      <div>
        <span className="games-poker-pot">pot {(board.pot as number | undefined) ?? 0}</span>
        <span style={{ color: 'var(--text-dim)', marginLeft: '0.6rem', fontSize: '0.78rem' }}>
          {String(board.street ?? '')}
        </span>
      </div>
      <SeatRow seat={0} state={board} />
    </div>
  );
}
