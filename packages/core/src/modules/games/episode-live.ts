/**
 * The **live episode**: the trajectory of the match happening right now, assembled
 * off the `games` channel as it plays. The pure Episode shape and the replay-based
 * builder live in episode.ts; this is the subscribing half, kept separate so that
 * logic stays unit-testable without opening a socket.
 *
 * Like games-log.ts, this subscribes independently of game-ws.ts (the ws layer fans
 * one channel out to every handler), so it observes without perturbing the state
 * machine that drives the board.
 */
import { useSyncExternalStore } from 'react';

import { subscribeChannel } from '../../ws';
import { applyReturns, emptyEpisode, type Episode, type EpisodeStep } from './episode';
import type { TraceStep } from './game-ws';

let live: Episode = emptyEpisode();
const listeners = new Set<() => void>();

function emit(): void {
  for (const l of listeners) l();
}

function setLive(patch: Partial<Episode>): void {
  live = { ...live, ...patch };
  emit();
}

export function useLiveEpisode(): Episode {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => live,
    () => live,
  );
}

/** The step `seat` is currently deciding — its latest step, if still undecided. */
function openStep(seat: number): EpisodeStep | undefined {
  for (let i = live.steps.length - 1; i >= 0; i--) {
    const s = live.steps[i];
    if (s.seat === seat) return s.action === null ? s : undefined;
  }
  return undefined;
}

function replaceStep(step: EpisodeStep): void {
  setLive({ steps: live.steps.map((s) => (s.idx === step.idx ? step : s)) });
}

subscribeChannel('games', (msg) => {
  const d = (msg.data ?? {}) as Record<string, unknown>;
  switch (msg.event) {
    case 'match_info':
      // A new match: start a fresh trajectory.
      live = {
        ...emptyEpisode(),
        gameId: (d.game_id as string) ?? null,
        seats: ((d.seats as { display_name?: string }[] | undefined) ?? []).map(
          (s) => s.display_name ?? '?',
        ),
        startedAt: Date.now(),
      };
      emit();
      break;
    case 'your_turn': {
      const seat = Number(d.seat ?? -1);
      setLive({
        steps: [
          ...live.steps,
          {
            idx: live.steps.length,
            seat,
            obs: (d.observation as Record<string, unknown>) ?? null,
            legalActions: (d.legal_actions as { id: string; label: string }[]) ?? [],
            action: null,
            trace: [],
            state: null,
            reward: null,
          },
        ],
      });
      break;
    }
    case 'agent_trace': {
      const step = openStep(Number(d.seat ?? -1));
      if (!step) break;
      replaceStep({
        ...step,
        trace: [...step.trace, (d.step as TraceStep) ?? { kind: 'assistant' }],
      });
      break;
    }
    case 'chose': {
      const step = openStep(Number(d.seat ?? -1));
      if (!step) break;
      replaceStep({ ...step, action: String(d.action_id ?? ''), timeout: Boolean(d.timeout) });
      break;
    }
    case 'public_state': {
      // Attribute the resulting state to the most recent step.
      const last = live.steps[live.steps.length - 1];
      if (last && last.state === null) {
        replaceStep({ ...last, state: (d.state as Record<string, unknown>) ?? null });
      }
      break;
    }
    case 'game_over': {
      const returns = (d.returns as Record<string, number>) ?? {};
      setLive({
        live: false,
        winner: (d.winner as number | null) ?? null,
        returns,
        steps: applyReturns(live.steps, returns),
      });
      break;
    }
    default:
      break;
  }
});
