/**
 * The **code-locus bus** — the emacs `point`, for code. A single, global, ephemeral
 * "what code am I looking at": `{ path, range?, symbol? }`. Every coding pane
 * publishes to it (`setLocus`) and follows it (`useLocus`), so one click in the
 * outline drives the editor, and the editor's cursor drives the outline — without any
 * pane knowing about another.
 *
 * A core primitive (not a module), because it's the shared spine multiple modules
 * hang off. Updates mirror over the `code` `/ws` channel so `dash.code`, the agent,
 * and other windows follow. Two guards keep it from feeding back on itself (the same
 * hazard the reveal/toggle buses hit): a per-client `origin` id drops our own echoes
 * off the socket, and each update carries the `source` pane so a follower can ignore
 * updates it drove itself. See docs/modules/code.mdx.
 */
import { useSyncExternalStore } from 'react';

import { subscribeChannel, sendChannel, type WsMessage } from './ws';

export interface LocusPosition {
  line: number; // 1-based
  column: number; // 1-based
}
export interface LocusRange {
  start: LocusPosition;
  end: LocusPosition;
}
export interface Locus {
  path?: string;
  root?: string;
  range?: LocusRange;
  symbol?: string;
  /** The pane/agent that drove this update ('editor' | 'outline' | 'dash' | …). */
  source?: string;
  /** Client instance id, for self-echo suppression across the /ws mirror. */
  origin?: string;
}

const myOrigin = Math.random().toString(36).slice(2);
let current: Locus = {};
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((l) => l());
}

/**
 * Publish a locus from `source`. Notifies local followers synchronously and mirrors
 * over `/ws` so dash / the agent / other windows follow.
 */
export function setLocus(locus: Locus, source: string): void {
  current = { ...locus, source, origin: myOrigin };
  emit();
  sendChannel('code', 'locus', current);
}

/** Apply a locus that arrived from the backend / another window — no re-send. */
function applyRemote(locus: Locus): void {
  current = locus;
  emit();
}

let wired = false;
function ensureWired(): void {
  if (wired) return;
  wired = true;
  subscribeChannel('code', (msg: WsMessage) => {
    if (msg.event !== 'locus') return;
    const data = msg.data as Locus | undefined;
    if (!data || data.origin === myOrigin) return; // ignore our own echo
    applyRemote(data);
  });
}

/** The current locus (empty object if none set yet). */
export function getLocus(): Locus {
  return current;
}

/** Subscribe to locus changes; returns an unsubscribe. Lazily wires the /ws mirror. */
export function subscribeLocus(listener: () => void): () => void {
  ensureWired();
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** React hook: the current locus, re-rendering the component on every change. */
export function useLocus(): Locus {
  return useSyncExternalStore(subscribeLocus, getLocus, getLocus);
}
