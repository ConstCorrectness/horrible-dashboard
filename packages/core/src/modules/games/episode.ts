/**
 * **Episodes**: a match as a trajectory — the step-by-step sequence of what the
 * agent saw, what it did, what that was worth, and what it was thinking.
 *
 * The board shows you the *current* position and the log shows you a *stream*;
 * neither lets you step back through the episode and ask "what did the observation
 * look like when it made that move?". That's what this is for.
 *
 * One Episode shape, two sources, so the pane renders live and finished matches
 * with the same code:
 *
 * - **live** — `episode-live.ts` builds one off the `games` channel as it plays.
 * - **past** — `episodeFromReplay` here builds one from a replay's event log.
 *
 * A **step** is one seat's decision: the observation it was handed, the action it
 * committed, the reasoning steps in between, and the state that resulted. Rewards
 * land at the end (these games score terminally), so `returns` is on the episode and
 * mirrored onto the last step of each seat.
 *
 * This module is deliberately **pure** — types and builders, no socket, no store —
 * so the trajectory logic is unit-testable without a live backend. The subscribing
 * half lives in episode-live.ts.
 */
import type { TraceStep } from './game-ws';
import type { Replay, ReplayEvent } from './games-api';

export interface EpisodeStep {
  idx: number;
  seat: number;
  /** What this seat was handed before deciding (null for a spectated seat). */
  obs: Record<string, unknown> | null;
  legalActions: { id: string; label: string }[];
  /** What it committed (null while the step is still in flight). */
  action: string | null;
  /** Its reasoning for this step, in order. */
  trace: TraceStep[];
  /** The public state after the action, when the source reports one. */
  state: Record<string, unknown> | null;
  /** Terminal reward for this seat — set only on its final step. */
  reward: number | null;
  timeout?: boolean;
}

export interface Episode {
  /** null for the live episode; the replay id for a loaded one. */
  replayId: string | null;
  gameId: string | null;
  live: boolean;
  steps: EpisodeStep[];
  seats: string[];
  winner: number | null;
  returns: Record<string, number>;
  startedAt: number | null;
}

export function emptyEpisode(): Episode {
  return {
    replayId: null,
    gameId: null,
    live: true,
    steps: [],
    seats: [],
    winner: null,
    returns: {},
    startedAt: null,
  };
}

/** Attach terminal returns to each seat's final step, so scrubbing to the end of
 * the episode shows what the trajectory was actually worth. Shared by both sources
 * (episode-live.ts applies it on `game_over`). */
export function applyReturns(steps: EpisodeStep[], returns: Record<string, number>): EpisodeStep[] {
  const lastBySeat = new Map<number, number>();
  steps.forEach((s) => lastBySeat.set(s.seat, s.idx));
  return steps.map((s) => {
    const reward = returns[String(s.seat)];
    return lastBySeat.get(s.seat) === s.idx && reward !== undefined ? { ...s, reward } : s;
  });
}

// ---- past episodes -----------------------------------------------------------

/** Rebuild an Episode from a finished replay's event log. The replay records the
 * same events the live channel streams (`public_state` | `action` | `trace` |
 * `game_over`), so the two sources converge on one shape — with one difference:
 * a replay has no `your_turn`, so a step opens on its seat's first event. */
export function episodeFromReplay(replay: Replay): Episode {
  const steps: EpisodeStep[] = [];
  let winner: number | null = replay.winner;
  let returns: Record<string, number> = replay.returns ?? {};

  // The step a seat is currently deciding, keyed by seat.
  const open = new Map<number, EpisodeStep>();
  const openFor = (seat: number): EpisodeStep => {
    const existing = open.get(seat);
    if (existing) return existing;
    const step: EpisodeStep = {
      idx: steps.length,
      seat,
      obs: null,
      legalActions: [],
      action: null,
      trace: [],
      state: null,
      reward: null,
    };
    steps.push(step);
    open.set(seat, step);
    return step;
  };
  const commit = (step: EpisodeStep) => {
    steps[step.idx] = step;
    open.delete(step.seat);
  };

  for (const ev of replay.events as ReplayEvent[]) {
    const seat = Number(ev.seat ?? -1);
    switch (ev.kind) {
      case 'trace': {
        const step = openFor(seat);
        steps[step.idx] = {
          ...step,
          trace: [...step.trace, ...((ev.steps as TraceStep[] | undefined) ?? [])],
        };
        open.set(seat, steps[step.idx]);
        break;
      }
      case 'action': {
        const step = openFor(seat);
        commit({
          ...step,
          action: ev.action_id ?? null,
          timeout: Boolean(ev.timeout),
        });
        break;
      }
      case 'public_state': {
        const last = steps[steps.length - 1];
        if (last && last.state === null) {
          steps[last.idx] = { ...last, state: (ev.state as Record<string, unknown>) ?? null };
          if (open.has(last.seat)) open.set(last.seat, steps[last.idx]);
        }
        break;
      }
      case 'game_over':
        winner = (ev.winner as number | null) ?? winner;
        returns = (ev.returns as Record<string, number>) ?? returns;
        break;
      default:
        break;
    }
  }

  return {
    replayId: replay.id,
    gameId: replay.game_id,
    live: false,
    steps: applyReturns(steps, returns),
    seats: replay.seats ?? [],
    winner,
    returns,
    startedAt: replay.created_at ? replay.created_at * 1000 : null,
  };
}
