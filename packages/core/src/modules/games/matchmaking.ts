/**
 * How a match starts — **one setup object, one entry point**.
 *
 * A match is fully described by three things: the game, who drives *your* seat,
 * and who fills the *other* one. That is what `MatchSetup` is, and `startMatch`
 * is the only thing any button calls.
 *
 * This replaces a flat row of buttons (Play vs My Agent / Test vs Bot / Find Match
 * / Host Table) sitting next to an unrelated "MOVE POLICY" toggle. The two were
 * secretly coupled: `playVsOwnAgent` opened a **self-play** table where this node
 * holds *both* seats, so what it actually did depended on the policy toggle
 * elsewhere on the screen — manual policy meant "you vs your agent", agent policy
 * meant "your agent vs itself". One button, three different games, and the
 * deciding control was under a different heading. Making the opponent a value
 * with its own driver field is what makes the label honest.
 *
 * Flows that start playing immediately reveal the board FIRST — its "Connecting…"
 * state is the immediate feedback. Flows that *wait* (ranked, hosting) do not: the
 * queue banner is their feedback, and revealing an empty board behind a spinner
 * would be worse than staying put. Failures surface as one toast instead of
 * leaving a dead button. See docs/modules/games.mdx.
 */
import { toastsStore } from '../../toasts';
import { DRIVER_LABEL, type Driver } from './driver';
import {
  ensureConnected,
  gamesCreateBotTable,
  gamesCreateTable,
  gamesJoinTable,
  gamesQueueJoin,
  revealBoard,
  watchTable,
} from './game-ws';
import { resolveDriver, resolveOpponent, type Opponent } from './seat';

export interface MatchSetup {
  gameId: string;
  /** Your seat's driver. Persisted per game as `games.policy.<gameId>`. */
  me: Driver;
  opponent: Opponent;
}

/**
 * Ranked is **agent-only**, on purpose.
 *
 * The premise of the module is that the human competes by engineering the
 * harness, not by playing. A person hand-playing the rated ladder against other
 * people's agents is measuring a different thing entirely and would pollute the
 * ELO that every other feature reads. Practice, mirror, open tables and Training
 * all take `manual` happily — this is the one place it is refused, and it is
 * refused with a visible reason rather than a silently disabled button.
 */
export function rankedRefusal(driver: Driver): string | null {
  if (driver === 'agent' || driver === 'script') return null;
  return driver === 'manual'
    ? 'Ranked is agent-only — your harness competes, you don’t. Switch your seat to My Agent or My Script, or play Practice instead.'
    : 'A random seat would feed the ladder noise. Switch to My Agent or My Script for ranked.';
}

/** A one-line description of a setup, generated from its two seats. This is the
 * Fight button's label, and the reason the button can no longer lie about what it
 * is about to do. */
export function describeSetup(setup: MatchSetup): string {
  const me = DRIVER_LABEL[setup.me];
  switch (setup.opponent.kind) {
    case 'ranked':
      return `${me} vs Ranked`;
    case 'bot':
      return `${me} vs Practice Bot`;
    case 'open':
      return `${me} vs whoever joins`;
    case 'mirror':
      return setup.me === setup.opponent.driver
        ? `${me} vs ${DRIVER_LABEL[setup.opponent.driver]} (self-play)`
        : `${me} vs ${DRIVER_LABEL[setup.opponent.driver]}`;
  }
}

function report(err: unknown): void {
  toastsStore.add('error', 'Games', err instanceof Error ? err.message : String(err));
}

/**
 * Start the match this setup describes — the single entry point.
 *
 * `ensureConnected(true)` (self-play: this node holds both seats) fires for
 * `mirror` and nothing else. Every other opponent needs the account seat, because
 * the *server* is filling the other side.
 */
export async function startMatch(setup: MatchSetup): Promise<void> {
  const { gameId, opponent } = setup;

  if (opponent.kind === 'ranked') {
    const refusal = rankedRefusal(setup.me);
    if (refusal) {
      toastsStore.add('warning', 'Ranked', refusal);
      return;
    }
    try {
      await ensureConnected(false);
      gamesQueueJoin(gameId);
    } catch (err) {
      report(err);
    }
    return;
  }

  if (opponent.kind === 'open') {
    try {
      await ensureConnected(false);
      gamesCreateTable(gameId);
    } catch (err) {
      report(err);
    }
    return;
  }

  revealBoard();
  try {
    if (opponent.kind === 'mirror') {
      await ensureConnected(true);
      gamesCreateTable(gameId);
    } else {
      await ensureConnected(false);
      gamesCreateBotTable(gameId, opponent.tier);
    }
  } catch (err) {
    report(err);
  }
}

/**
 * Start a game with the setup the player last chose for it — the "just play this"
 * entry point for surfaces that are not the setup card (the hero's Quick Play, the
 * builder's Test Match, the board's empty state).
 *
 * These used to call `playVsOwnAgent` and so *always* opened a self-play table
 * regardless of what the player had configured. Reading the stored setup means one
 * choice, made once on the card, is what every one of these buttons honours.
 */
export async function startWithSavedSetup(
  gameId: string,
  catalogDefault?: string,
  allowed?: readonly string[],
): Promise<void> {
  await startMatch({
    gameId,
    me: resolveDriver(gameId, catalogDefault, allowed),
    opponent: resolveOpponent(gameId),
  });
}

/** Placement run: the first-run path, which wants the ladder immediately and takes
 * a bot straight away rather than waiting out the backfill deadline. */
export async function startPlacement(gameId: string): Promise<void> {
  const me = resolveDriver(gameId);
  const refusal = rankedRefusal(me);
  if (refusal) {
    toastsStore.add('warning', 'Ranked', refusal);
    return;
  }
  try {
    await ensureConnected(false);
    gamesQueueJoin(gameId, true);
  } catch (err) {
    report(err);
  }
}

/** Take the open seat at someone's table. */
export async function joinTableLive(tableId: string): Promise<void> {
  revealBoard();
  try {
    await ensureConnected(false);
    gamesJoinTable(tableId);
  } catch (err) {
    report(err);
  }
}

/** Spectate a running table. */
export async function watchTableLive(tableId: string): Promise<void> {
  revealBoard();
  try {
    await ensureConnected(false);
    watchTable(tableId);
  } catch (err) {
    report(err);
  }
}
