/**
 * Coalesces a stream of small updates to one value into at most one write per
 * frame.
 *
 * A model streams a token at a time, and the chat used to write the transcript
 * once per token — a store update, a render of the pane and a markdown parse for
 * each. Nothing can be seen between two tokens that land inside one frame, so
 * the deltas are queued and applied together.
 *
 * Order is the invariant: `now` runs everything queued before it, so an event
 * that must not lag (a tool call starting, the final answer, an error) is never
 * applied ahead of text that arrived first.
 */

export type Patch<T> = (value: T) => T;

/** Arrange for `run` to be called soon. Returns a cancel. */
export type Schedule = (run: () => void) => () => void;

export interface PatchBatcher<T> {
  /** Queue a patch for the next flush. */
  defer(fn: Patch<T>): void;
  /** Apply everything queued, then this patch, immediately. */
  now(fn: Patch<T>): void;
  /** Apply everything queued. A no-op when the queue is empty. */
  flush(): void;
}

/** Longest a queued delta waits when no animation frame arrives. */
export const FLUSH_FALLBACK_MS = 50;

/**
 * Next animation frame, or `FLUSH_FALLBACK_MS` — whichever comes first.
 * `requestAnimationFrame` does not fire in a backgrounded tab or a hidden pane,
 * and a transcript that stops filling in until you look at it again would be
 * worse than the renders this saves.
 */
export const frameSchedule: Schedule = (run) => {
  let settled = false;
  let raf = 0;
  const stop = () => {
    settled = true;
    if (raf && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(raf);
    clearTimeout(timer);
  };
  const go = () => {
    if (settled) return;
    stop();
    run();
  };
  const timer = setTimeout(go, FLUSH_FALLBACK_MS);
  if (typeof requestAnimationFrame === 'function') raf = requestAnimationFrame(go);
  return stop;
};

export function createPatchBatcher<T>(
  apply: (fn: Patch<T>) => void,
  schedule: Schedule = frameSchedule,
): PatchBatcher<T> {
  let pending: Patch<T>[] = [];
  let cancel: (() => void) | null = null;

  const flush = () => {
    cancel?.();
    cancel = null;
    if (pending.length === 0) return;
    const fns = pending;
    pending = [];
    apply((value) => fns.reduce((acc, fn) => fn(acc), value));
  };

  return {
    defer(fn) {
      pending.push(fn);
      if (!cancel) cancel = schedule(flush);
    },
    now(fn) {
      pending.push(fn);
      flush();
    },
    flush,
  };
}
