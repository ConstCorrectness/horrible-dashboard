/**
 * The seam a command uses to hand the traces section a prompt.
 *
 * `TracesSection` owns its prompt in local state, and a command firing from the
 * palette has no reference to a mounted component — the same problem the editor
 * solves for Save As with `setActiveSaveAs`, solved the same way: a module-level
 * value plus a subscription.
 *
 * It must work in **both** directions of the race, which is why there is a pending
 * value *and* a subscription. Revealing the traces section mounts it fresh, so a
 * prompt pushed before the reveal has no listener yet and is read on mount
 * (`takePendingPrompt`); a prompt pushed while the section is already open has a
 * listener and no mount to read it. Either alone drops half the cases.
 *
 * `take` clears as it reads: a prompt is a one-shot instruction, and leaving it
 * set means every later visit to the section silently resurrects someone's old
 * selection over what they were typing.
 */

/** A prompt plus where it came from — the section shows the origin, because a
 * textarea that silently repopulated itself is indistinguishable from a bug. */
export interface TracePrompt {
  prompt: string;
  label: string;
}

let pending: TracePrompt | null = null;
const listeners = new Set<(next: TracePrompt) => void>();

/** Push a prompt at the traces section, whether or not it is mounted. */
export function sendTracePrompt(next: TracePrompt): void {
  pending = next;
  listeners.forEach((listener) => listener(next));
}

/** Read and clear the prompt waiting for a section that is about to mount. */
export function takePendingPrompt(): TracePrompt | null {
  const next = pending;
  pending = null;
  return next;
}

export function subscribeTracePrompt(listener: (next: TracePrompt) => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
