/**
 * The four ways a match starts, shared by every button that starts one (hub game
 * cards, ranked card, board empty state, first-run hero, table rows). Each flow
 * reveals the board FIRST — its "Connecting…" state is the immediate feedback —
 * then connects implicitly in the right mode and acts. Failures surface as one
 * toast instead of leaving a dead button.
 */
import { toastsStore } from '../../toasts';
import {
  ensureConnected,
  gamesCreateTable,
  gamesJoinTable,
  gamesQueueJoin,
  revealBoard,
  watchTable,
} from './game-ws';

function report(err: unknown): void {
  toastsStore.add('error', 'Games', err instanceof Error ? err.message : String(err));
}

/** Casual match against your own agent: a self-play table on this node. */
export async function playVsOwnAgent(gameId: string): Promise<void> {
  revealBoard();
  try {
    await ensureConnected(true);
    gamesCreateTable(gameId);
  } catch (err) {
    report(err);
  }
}

/** Ranked (or placement) queue — needs the account seat, not self-play. */
export async function findRankedMatch(
  gameId: string,
  difficulty = 'standard',
  placement = false,
): Promise<void> {
  try {
    await ensureConnected(false);
    gamesQueueJoin(gameId, difficulty, placement);
  } catch (err) {
    report(err);
  }
}

/** Host an OPEN table (account seat, no sparring bot) for another human to
 * join — the two-machines flow. The board reveals itself when the match starts. */
export async function hostOpenTable(gameId: string): Promise<void> {
  try {
    await ensureConnected(false);
    gamesCreateTable(gameId);
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
