/**
 * The games module's **log**: one chronological record of everything that happens
 * while you play — connection/auth, matchmaking, the referee's move-by-move
 * events, rating/XP movement, server errors, and your agent's reasoning.
 *
 * Agent thoughts are a *stream in this log*, not their own surface: "why did my
 * agent do that" and "what did the server actually say" are the same question when
 * you're debugging a harness, and reading them apart meant correlating two panes by
 * eye. `GamesLogPanel` renders it with per-stream filters.
 *
 * This subscribes to the `games` channel independently of game-ws.ts (the ws layer
 * fans one channel out to every handler), so logging stays a passive observer: it
 * can't perturb the state machine that drives the board, and game-ws.ts doesn't have
 * to carry logging calls through every case.
 */
import { useSyncExternalStore } from 'react';

import { subscribeChannel } from '../../ws';
import type { RatingUpdate, SeatProfile, TraceStep } from './game-ws';

/** Which stream an entry belongs to — the log's filter chips. */
export type LogStream = 'agent' | 'match' | 'server' | 'error';

export const STREAM_LABEL: Record<LogStream, string> = {
  agent: 'Agent',
  match: 'Match',
  server: 'Server',
  error: 'Errors',
};

export const STREAM_ICON: Record<LogStream, string> = {
  agent: '💭',
  match: '▦',
  server: '📡',
  error: '⚠',
};

export interface LogEntry {
  id: number;
  ts: number;
  stream: LogStream;
  /** One-line summary — what the row reads as. */
  text: string;
  /** The raw event name, shown as a dim tag. */
  event?: string;
  /** An agent reasoning step (`stream: 'agent'`), rendered as a TraceRow. */
  step?: TraceStep;
  seat?: number;
  /** The raw payload, revealed by expanding the row. */
  detail?: unknown;
}

/** Keep the log bounded — a long series streams a lot of events. */
const LIMIT = 1000;

let entries: LogEntry[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function emit(): void {
  for (const l of listeners) l();
}

function add(e: Omit<LogEntry, 'id' | 'ts'>): void {
  entries = [...entries, { ...e, id: nextId++, ts: Date.now() }].slice(-LIMIT);
  emit();
}

export function clearGamesLog(): void {
  entries = [];
  emit();
}

export function useGamesLog(): LogEntry[] {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => entries,
    () => entries,
  );
}

/** A short, human summary of a trace step for the log's one-line row. */
function traceText(step: TraceStep): string {
  switch (step.kind) {
    case 'assistant': {
      const calls = (step.tool_calls ?? []).map((c) => `${c.name}()`).join(', ');
      if (calls) return `thinking — calling ${calls}`;
      return step.content ? `thinking — ${step.content.slice(0, 80)}` : 'thinking';
    }
    case 'tool_result':
      return `${step.name} returned`;
    case 'chose':
      return `committed ${step.action_id}`;
    case 'fallback':
      return `harness failed — random fallback played ${step.action_id}`;
    default:
      return step.kind;
  }
}

subscribeChannel('games', (msg) => {
  const d = (msg.data ?? {}) as Record<string, unknown>;
  switch (msg.event) {
    case 'error':
      add({
        stream: 'error',
        event: 'error',
        text: String(d.message ?? 'Games error'),
        detail: d,
      });
      break;
    case 'authed': {
      const connected = Boolean(d.connected);
      add({
        stream: 'server',
        event: 'authed',
        text: connected
          ? `connected as ${String(d.account_id ?? '?')}${d.self_play ? ' (self-play)' : ''}`
          : 'disconnected',
        detail: d,
      });
      break;
    }
    case 'match_info': {
      const seats = (d.seats as SeatProfile[] | undefined) ?? [];
      add({
        stream: 'match',
        event: 'match_info',
        text: `match started · ${String(d.game_id ?? '?')} · ${
          seats.map((s) => s.display_name).join(' vs ') || 'seats unknown'
        }`,
        detail: d,
      });
      break;
    }
    case 'your_turn':
      add({
        stream: 'match',
        event: 'your_turn',
        seat: Number(d.seat ?? -1),
        text: `your turn (seat ${String(d.seat ?? '?')}) · ${
          ((d.legal_actions as unknown[] | undefined) ?? []).length
        } legal actions`,
        detail: d,
      });
      break;
    case 'chose':
      add({
        stream: 'match',
        event: 'chose',
        seat: Number(d.seat ?? -1),
        text: `seat ${String(d.seat ?? '?')} played ${String(d.action_id ?? '?')}`,
        detail: d,
      });
      break;
    case 'game_over': {
      const winner = d.winner as number | null | undefined;
      add({
        stream: 'match',
        event: 'game_over',
        text:
          winner === null || winner === undefined
            ? 'game over — draw'
            : `game over — seat ${winner} wins`,
        detail: d,
      });
      break;
    }
    case 'rating_update': {
      const u = d as unknown as RatingUpdate;
      const sign = (u.delta ?? 0) >= 0 ? '+' : '';
      add({
        stream: 'match',
        event: 'rating_update',
        text:
          u.delta === undefined
            ? `xp ${u.xp} · level ${u.level}`
            : `rating ${sign}${u.delta} → ${Math.round(u.rating ?? 0)} (${u.tier ?? '?'})`,
        detail: d,
      });
      break;
    }
    case 'queue_status':
      add({
        stream: 'server',
        event: 'queue_status',
        text: `queued · ${String(d.game_id ?? '?')} · waiting ${String(d.waiting_s ?? 0)}s`,
        detail: d,
      });
      break;
    case 'match_found':
      add({ stream: 'server', event: 'match_found', text: 'match found', detail: d });
      break;
    case 'agent_trace': {
      const step = (d.step as TraceStep) ?? { kind: 'assistant' };
      add({
        stream: 'agent',
        event: 'agent_trace',
        seat: Number(d.seat ?? -1),
        text: traceText(step),
        step,
        detail: d,
      });
      break;
    }
    default:
      break;
  }
});
