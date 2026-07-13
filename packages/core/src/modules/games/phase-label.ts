/**
 * One place that answers "why is nothing moving?" — maps each game's phase
 * fields in the spectator `PublicState` to a short human label for the board's
 * status banner. Returns null when the turn banner alone tells the story
 * (plain alternating board games).
 */
import { type PublicState } from './game-ws';

export function phaseLabel(board: PublicState): string | null {
  switch (board.game) {
    case 'arena': {
      const phase = String(board.phase ?? 'edit');
      if (phase === 'edit') return 'edit phase — agents write their bots';
      const round = Number(board.round ?? 0);
      const rounds = Number(board.rounds ?? 0);
      return `round ${round}/${rounds} · ⚙️ simulating`;
    }
    case 'bug_hunt': {
      if (board.grading) return '🧪 verifying a fix…';
      const attempts = (board.attempts as unknown[][] | undefined) ?? [[], []];
      const total = attempts.reduce((n, a) => n + a.length, 0);
      return `hunting — ${total} attempt${total === 1 ? '' : 's'} so far`;
    }
    case 'holdem': {
      const street = String(board.street ?? '');
      return street ? `street: ${street}` : null;
    }
    case 'rag_race': {
      if (board.scores !== undefined) return null; // graded — the board shows results
      const submitted = (board.submitted as boolean[] | undefined) ?? [];
      const done = submitted.filter(Boolean).length;
      return `racing — ${done}/${submitted.length || 2} submitted`;
    }
    case 'code_golf': {
      if (board.grading) return '🧪 grading against hidden tests…';
      const submitted = (board.submitted as boolean[] | undefined) ?? [];
      const done = submitted.filter(Boolean).length;
      return `writing solutions — ${done}/${submitted.length || 2} submitted`;
    }
    case 'test_duel': {
      if (board.grading) return '🧪 running all suites…';
      return `phase: ${String(board.phase ?? 'impl')}`;
    }
    default:
      return null; // tictactoe / connect_four / fighter: the turn banner suffices
  }
}
