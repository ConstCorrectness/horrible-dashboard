/**
 * Which page an agent turn is driving, so the browser can say so and offer a way
 * to take over.
 *
 * Every browser tool marks the engine it resolves (engines.ts `agentEngine`) with
 * the calling turn (agent-turn.ts `relayTurn`). The mark lasts until that turn ends
 * (the `agent` channel's `done`), so the page stays marked while the model thinks
 * between steps, and not a moment past the turn. A turn whose `done` never arrives
 * (a dropped socket) expires {@link STALE_MS} after its last action.
 *
 * Taking over is the turn's Stop: {@link stopDriving} cancels the turn on the
 * backend, exactly as the chat's own Stop does, and the person carries on in the
 * same page. Ask the agent to continue afterwards and it reads the page as it is.
 */
import { useSyncExternalStore } from 'react';

import { cancelTurn } from '../../agent-turn';
import { subscribeChannel } from '../../ws';

/** A turn silent this long is treated as gone even without its `done`. */
const STALE_MS = 90_000;

/** The headless engine's key; a native page's key is its webview id. */
export const SERVER_PAGE = 'server';

interface Mark {
  turnId: string;
  at: number;
}

const marks = new Map<string, Mark>();
const listeners = new Set<() => void>();
let version = 0;
let listening = false;

function changed(): void {
  version += 1;
  listeners.forEach((fn) => fn());
}

function listen(): void {
  if (listening) return;
  listening = true;
  subscribeChannel('agent', (msg) => {
    if (msg.event !== 'done') return;
    const turnId = (msg.data as { turnId?: unknown } | undefined)?.turnId;
    if (typeof turnId === 'string') clearTurn(turnId);
  });
}

/** Turn `turnId` is acting on page `page`. A null turn (a person) marks nothing. */
export function markDriving(page: string, turnId: string | null): void {
  if (!turnId) return;
  listen();
  const prior = marks.get(page);
  marks.set(page, { turnId, at: Date.now() });
  if (prior?.turnId !== turnId) changed();
}

function clearTurn(turnId: string): void {
  let any = false;
  for (const [page, mark] of marks) {
    if (mark.turnId === turnId) {
      marks.delete(page);
      any = true;
    }
  }
  if (any) changed();
}

/** The turn driving `page` now, or null. */
export function drivingTurn(page: string | null): string | null {
  if (!page) return null;
  const mark = marks.get(page);
  if (!mark) return null;
  if (Date.now() - mark.at > STALE_MS) {
    marks.delete(page);
    return null;
  }
  return mark.turnId;
}

/** Stop the turn driving `page` and hand the page back to the person. */
export function stopDriving(page: string): void {
  const turnId = drivingTurn(page);
  if (!turnId) return;
  cancelTurn(turnId);
  clearTurn(turnId);
}

/** Re-renders when any page's driver changes; returns the turn driving `page`. */
export function useDrivingTurn(page: string | null): string | null {
  useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    () => version,
  );
  return drivingTurn(page);
}

/** Test-only. */
export function resetDrivingForTests(): void {
  marks.clear();
  changed();
}
