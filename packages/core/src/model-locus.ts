/**
 * The **model-locus bus** — "which part of the model am I looking at", the way
 * `locus.ts` is "which code am I looking at". A single, global, ephemeral
 * `{ modelSha, traceId, layer, position, tokenId }`: a cell in a lens grid, a
 * block in the model explorer, or the tensor a `dash.lens` sweep just printed.
 *
 * Same shape and same reason as the code locus: it is the shared spine several
 * surfaces hang off, so it belongs in core rather than in one of them. Click
 * layer 15 in the lens grid and the model explorer reveals `blk.15`'s tensors,
 * without either pane importing the other or knowing it exists.
 *
 * One deliberate difference from `locus.ts`: the `/ws` mirror is **inbound only**.
 * The code locus is bidirectional because two panes publish to it from two
 * windows; here every publisher that matters is either in this tab (the grid, the
 * explorer — a local store notifies them synchronously) or on the backend
 * (`dash.lens.focus`, the `lens.*` agent tools). Sending our own clicks up would
 * buy nothing and cost a frame per mouse-move over a grid, so `setModelLocus`
 * does not send, there is no `lens` entry in `app.py`'s inbound channel ladder,
 * and consequently there is no self-echo to suppress — which is why this has no
 * `origin` field where the code locus needs one.
 *
 * See docs/modules/llamacpp.mdx.
 */
import { useSyncExternalStore } from 'react';

import { subscribeChannel, type WsMessage } from './ws';

export interface ModelLocus {
  /** The weights being looked at. Two traces of different models are unrelated. */
  modelSha?: string;
  /** The trace a lens reading came from, when there is one. */
  traceId?: string;
  /** Decoder block. `-1` is the embedding — a real value, not "unset". */
  layer?: number;
  /** Token position within the traced pass. */
  position?: number;
  /** A vocabulary token being tracked across the grid. */
  tokenId?: number;
  /** Who drove this ('lens' | 'explorer' | 'dash' | 'agent'). */
  source?: string;
}

let current: ModelLocus = {};
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((l) => l());
}

/**
 * Publish a model locus from `source`. Followers are notified synchronously.
 *
 * Merges rather than replaces: the grid knows the layer and position but not
 * which vocabulary token is pinned, and the pin control knows the token but not
 * where the cursor is. A replace would make every publisher responsible for
 * restating facts it does not own, and the first one to forget would silently
 * clear a pin.
 */
export function setModelLocus(locus: ModelLocus, source: string): void {
  current = { ...current, ...locus, source };
  emit();
}

/** Clear the locus (no model in view). */
export function clearModelLocus(): void {
  current = {};
  emit();
}

let wired = false;
function ensureWired(): void {
  if (wired) return;
  wired = true;
  subscribeChannel('lens', (msg: WsMessage) => {
    if (msg.event !== 'locus') return;
    const data = msg.data as ModelLocus | undefined;
    if (!data) return;
    // Replace, not merge: this one came from a publisher that stated the whole
    // locus it meant, and merging would leave a stale layer from a click under
    // an agent's "look at the embedding".
    current = data;
    emit();
  });
}

/** The current model locus (empty object if none set yet). */
export function getModelLocus(): ModelLocus {
  return current;
}

/** Subscribe to changes; returns an unsubscribe. Lazily wires the /ws mirror. */
export function subscribeModelLocus(listener: () => void): () => void {
  ensureWired();
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** React hook: the current model locus, re-rendering on every change. */
export function useModelLocus(): ModelLocus {
  return useSyncExternalStore(subscribeModelLocus, getModelLocus, getModelLocus);
}
