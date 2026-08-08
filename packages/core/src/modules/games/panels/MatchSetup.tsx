import { useCallback, useEffect, useMemo, useState } from 'react';

import { useAccount } from '../../../useAccount';
import { gameAccent, gameIcon } from '../game-identity';
import { gamesQueueLeave, useGames } from '../game-ws';
import { fetchLeaderboard, type GameCatalogEntry } from '../games-api';
import {
  DRIVERS,
  DRIVER_HINT,
  DRIVER_ICON,
  DRIVER_LABEL,
  driverFromPolicy,
  type Driver,
} from '../driver';
import { describeSetup, rankedRefusal, startMatch, type MatchSetup } from '../matchmaking';
import {
  resolveBotTier,
  resolveDriver,
  resolveMirrorDriver,
  resolveOpponent,
  setBotTier,
  setDriver,
  setMirrorDriver,
  setOpponentKind,
  type Opponent,
  type OpponentKind,
} from '../seat';

/**
 * **The match setup card** — the Play section's centrepiece and the one place a
 * match is configured.
 *
 * It is two seats and a button. The left seat is *how you play* (your driver), the
 * right seat is *who you play*, and the button's label is generated from both. That
 * shape is doing real work: the screen it replaces had a MOVE POLICY toggle
 * (rendered twice), a PLAY MODE toggle, a QUEUE DIFFICULTY control (also rendered
 * twice, once as toggles and once as a dropdown) and four start buttons, none of
 * which said how they combined. "Play vs My Agent" in particular opened a
 * *self-play* table whose behaviour depended on a toggle elsewhere on the page.
 *
 * Two rules keep it honest:
 * - The FIGHT label is `describeSetup(setup)`, never a fixed string. If the two
 *   seats change, the promise on the button changes with them.
 * - The opponent's own driver is a *field on the opponent* (mirror), not a global.
 *   "You vs Your Agent" and "Your Agent vs Itself" are now different setups rather
 *   than the same button on a different day.
 *
 * See docs/modules/games.mdx.
 */

const BOT_TIERS: { value: string; label: string; name: string }[] = [
  { value: 'bronze', label: '🥉 Easy', name: 'Rusty' },
  { value: 'silver', label: '🥈 Medium', name: 'Circuit' },
  { value: 'gold', label: '🥇 Hard', name: 'Aurum' },
  { value: 'platinum', label: '💠 Expert', name: 'Nemesis' },
];

const OPPONENTS: { kind: OpponentKind; icon: string; label: string; hint: string }[] = [
  {
    kind: 'ranked',
    icon: '🏁',
    label: 'Ranked',
    hint: 'The ladder. ELO finds someone your strength; a practice bot fills in if nobody is about.',
  },
  {
    kind: 'bot',
    icon: '🤖',
    label: 'Practice Bot',
    hint: 'A server-hosted opponent at a difficulty you pick. Unrated.',
  },
  {
    kind: 'open',
    icon: '👤',
    label: 'Open Table',
    hint: 'Host a seat and wait for a human to take it. Unrated.',
  },
  {
    kind: 'mirror',
    icon: '🪞',
    label: 'Mirror',
    hint: 'Self-play: this node holds both seats. Pick what drives the other one.',
  },
];

/** Your standing in one game, read the way the profile page reads it — off the
 * leaderboard, since the ladder is the server's copy of the truth. Null while it
 * loads, or when you have never played this game. */
interface GameRating {
  rating: number | null;
  tier: string | null;
  wins: number;
  losses: number;
  draws: number;
}

function useGameRating(gameId: string): GameRating | null {
  const { accountId: liveAccountId, lastRating } = useGames();
  const { account } = useAccount();
  // The socket's id when a play session is up, else the signed-in node's own id.
  // Connection is implicit, so a freshly-opened client has no socket yet and this
  // would otherwise sit on "···" until the first match.
  const accountId = liveAccountId ?? account?.id ?? null;
  const [row, setRow] = useState<GameRating | null>(null);

  // Refetch when a rated game for this game finishes, so the badge moves with the
  // result rather than going stale until the pane remounts.
  const stamp = lastRating?.game_id === gameId ? lastRating.rating : null;

  useEffect(() => {
    if (!accountId) return;
    let live = true;
    fetchLeaderboard(gameId)
      .then((lb) => {
        if (!live) return;
        const me = lb.entries.find((e) => e.account_id === accountId);
        setRow(
          me
            ? {
                rating: me.rating,
                tier: me.tier ?? null,
                wins: me.wins,
                losses: me.losses,
                draws: me.draws,
              }
            : { rating: null, tier: null, wins: 0, losses: 0, draws: 0 },
        );
      })
      .catch(() => {
        if (live) setRow(null);
      });
    return () => {
      live = false;
    };
  }, [accountId, gameId, stamp]);

  return row;
}

function SeatChips<T extends string>({
  options,
  value,
  onPick,
  accent,
}: {
  options: { value: T; icon: string; label: string; hint: string; disabled?: string }[];
  value: T;
  onPick: (next: T) => void;
  accent: string;
}) {
  return (
    <div className="games-seat-chips">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={`games-seat-chip${o.value === value ? ' selected' : ''}${
            o.disabled ? ' unavailable' : ''
          }`}
          style={{ '--seat-accent': accent } as React.CSSProperties}
          // Not `disabled`: an unavailable option must still be hoverable, or the
          // reason it is unavailable is unreachable.
          aria-disabled={o.disabled ? true : undefined}
          title={o.disabled ?? o.hint}
          onClick={() => !o.disabled && onPick(o.value)}
        >
          <span aria-hidden>{o.icon}</span>
          <span className="games-seat-chip-label">{o.label}</span>
        </button>
      ))}
    </div>
  );
}

export function MatchSetupCard({ game }: { game: GameCatalogEntry }) {
  const gameId = game.id;
  const accent = gameAccent(gameId);
  const { queue } = useGames();
  const standing = useGameRating(gameId);

  // Seat state lives in settings (per game) and is mirrored here so the card is
  // responsive; `setDriver` and friends are the writes that make it stick.
  const [me, setMe] = useState<Driver>(() => resolveDriver(gameId, game.default_policy));
  const [opponent, setOpponent] = useState<Opponent>(() => resolveOpponent(gameId));

  // Switching games in the library re-reads that game's saved seats rather than
  // carrying the previous game's over.
  useEffect(() => {
    setMe(resolveDriver(gameId, game.default_policy));
    setOpponent(resolveOpponent(gameId));
  }, [gameId, game.default_policy]);

  const pickDriver = useCallback(
    (next: Driver) => {
      setMe(next);
      setDriver(gameId, next);
    },
    [gameId],
  );

  const pickOpponent = useCallback(
    (kind: OpponentKind) => {
      setOpponentKind(gameId, kind);
      setOpponent(
        kind === 'bot'
          ? { kind, tier: resolveBotTier(gameId) }
          : kind === 'mirror'
            ? { kind, driver: resolveMirrorDriver(gameId) }
            : { kind },
      );
    },
    [gameId],
  );

  // A game may forbid a driver outright (a realtime game has no sensible manual
  // seat). The catalog has always carried this; nothing used to read it.
  const allowed = game.allowed_policies;
  const driverOptions = useMemo(
    () =>
      DRIVERS.map((d) => ({
        value: d,
        icon: DRIVER_ICON[d],
        label: DRIVER_LABEL[d],
        hint: DRIVER_HINT[d],
        disabled:
          allowed && !allowed.some((p) => driverFromPolicy(p) === d)
            ? `${game.name} does not support the ${DRIVER_LABEL[d]} seat.`
            : undefined,
      })),
    [allowed, game.name],
  );

  const setup: MatchSetup = { gameId, me, opponent };
  const refusal = opponent.kind === 'ranked' ? rankedRefusal(me) : null;
  const queued = queue?.gameId === gameId ? queue : null;
  const rated = opponent.kind === 'ranked';

  const opponentFace =
    opponent.kind === 'mirror'
      ? DRIVER_ICON[opponent.driver]
      : (OPPONENTS.find((o) => o.kind === opponent.kind)?.icon ?? '?');
  const opponentName =
    opponent.kind === 'mirror'
      ? DRIVER_LABEL[opponent.driver]
      : opponent.kind === 'bot'
        ? (BOT_TIERS.find((t) => t.value === opponent.tier)?.name ?? 'Bot')
        : opponent.kind === 'ranked'
          ? queued
            ? 'Searching…'
            : 'Unknown'
          : 'Whoever joins';

  return (
    <div className="games-vs-card" style={{ '--vs-accent': accent } as React.CSSProperties}>
      <div className="games-vs-header">
        <span className="games-vs-glyph" aria-hidden>
          {gameIcon(gameId)}
        </span>
        <span className="games-vs-title">{game.name}</span>
        <Standing standing={standing} />
      </div>

      <div className="games-vs-arena">
        <div className="games-vs-seat">
          <div className="games-vs-portrait mine">
            <span aria-hidden>{DRIVER_ICON[me]}</span>
          </div>
          <div className="games-vs-name">{DRIVER_LABEL[me]}</div>
          <div className="games-vs-sub">{me === 'manual' ? 'human seat' : 'your harness'}</div>
          <SeatChips options={driverOptions} value={me} onPick={pickDriver} accent={accent} />
        </div>

        <div className="games-vs-divider" aria-hidden>
          <span className="games-vs-bolt">VS</span>
        </div>

        <div className="games-vs-seat">
          <div className={`games-vs-portrait theirs${queued ? ' searching' : ''}`}>
            <span aria-hidden>{opponentFace}</span>
          </div>
          <div className="games-vs-name">{opponentName}</div>
          <div className="games-vs-sub">{rated ? 'rated' : 'unrated'}</div>
          <SeatChips
            options={OPPONENTS.map((o) => ({
              value: o.kind,
              icon: o.icon,
              label: o.label,
              hint: o.hint,
            }))}
            value={opponent.kind}
            onPick={pickOpponent}
            accent={accent}
          />

          {opponent.kind === 'bot' && (
            <SeatChips
              options={BOT_TIERS.map((t) => ({
                value: t.value,
                icon: t.label.slice(0, 2),
                label: t.name,
                hint: `${t.label} — rated around ${
                  { bronze: 1000, silver: 1150, gold: 1300, platinum: 1450 }[t.value]
                } MMR.`,
              }))}
              value={opponent.tier}
              onPick={(tier) => {
                setBotTier(gameId, tier);
                setOpponent({ kind: 'bot', tier });
              }}
              accent={accent}
            />
          )}

          {opponent.kind === 'mirror' && (
            <SeatChips
              options={driverOptions}
              value={opponent.driver}
              onPick={(driver) => {
                setMirrorDriver(gameId, driver);
                setOpponent({ kind: 'mirror', driver });
              }}
              accent={accent}
            />
          )}
        </div>
      </div>

      <Stakes standing={standing} queued={queued} rated={rated} />

      {refusal && <div className="games-vs-refusal">⚠ {refusal}</div>}

      {queued ? (
        <div className="games-vs-searching">
          <span className="games-radar-scan">
            <span className="games-radar-line" />
          </span>
          <span className="games-vs-searching-text">
            Searching · {formatWait(queued.waitingS)} · ±{Math.round(queued.window)} MMR
            {queued.pool !== undefined && queued.pool > 1 ? ` · ${queued.pool} in queue` : ''}
          </span>
          <button type="button" className="games-vs-leave" onClick={() => gamesQueueLeave()}>
            Leave queue
          </button>
        </div>
      ) : (
        <button
          type="button"
          className={`games-vs-fight${refusal ? ' blocked' : ''}`}
          onClick={() => void startMatch(setup)}
        >
          <span className="games-vs-fight-label">FIGHT</span>
          <span className="games-vs-fight-sub">{describeSetup(setup)}</span>
        </button>
      )}
    </div>
  );
}

function formatWait(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** Rating, tier and record for the selected game. `placement` is a real state, not
 * a missing rating: the server masks the number until the placement games are in,
 * so showing "1200" there would be a fabrication. */
function Standing({ standing }: { standing: GameRating | null }) {
  if (!standing) return <span className="games-vs-standing loading">···</span>;
  const played = standing.wins + standing.losses + standing.draws;
  if (standing.rating === null) {
    return (
      <span className="games-vs-standing">
        <span className="games-vs-tier">PLACEMENT</span>
        <span className="games-vs-record">{played} played</span>
      </span>
    );
  }
  return (
    <span className="games-vs-standing">
      <span className="games-vs-tier">{(standing.tier ?? 'unrated').toUpperCase()}</span>
      <span className="games-vs-mmr">{Math.round(standing.rating)} MMR</span>
      <span className="games-vs-record">
        {standing.wins}W · {standing.losses}L · {standing.draws}D
      </span>
    </span>
  );
}

/**
 * The stakes strip: what this match is worth and how busy the pool is.
 *
 * Only rendered for rated setups, because for an unrated one the honest answer is
 * "nothing", and a row of zeroes reads as a bug. The delta preview arrives with
 * `queue_status`; before queueing we show the ±16 that ELO gives an even match at
 * K=32, which is what a rating-window pairing is by construction.
 */
function Stakes({
  standing,
  queued,
  rated,
}: {
  standing: GameRating | null;
  queued: {
    pool?: number;
    medianWaitS?: number;
    deltaPreview?: { win: number; loss: number };
  } | null;
  rated: boolean;
}) {
  if (!rated) return null;
  const preview = queued?.deltaPreview;
  const win = preview ? preview.win : 16;
  const loss = preview ? preview.loss : -16;
  return (
    <div className="games-vs-stakes">
      <span>
        <b className="win">+{win}</b> on a win
      </span>
      <span>
        <b className="loss">{loss}</b> on a loss
      </span>
      {queued?.pool !== undefined && <span>{queued.pool} in queue</span>}
      {queued?.medianWaitS !== undefined && <span>median wait {queued.medianWaitS}s</span>}
      {standing?.rating === null && <span className="games-vs-placement-note">placement run</span>}
    </div>
  );
}
