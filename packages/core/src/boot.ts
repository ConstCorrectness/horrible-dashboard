/**
 * Boot progress, reported by the entry point and rendered by the boot splash.
 *
 * The splash shows what the app is **actually** doing rather than an animation
 * on a timer. That is the whole reason this store exists: a fake progress bar
 * that always takes 800ms tells the user nothing when the real boot hangs on a
 * backend that is not answering, and hides the one moment they most need
 * information. Every step here is a real thing that either finished or did not.
 *
 * A store rather than React state because `boot()` runs before anything renders.
 */

/** The phases in order. `ready` means the shell may mount. */
export type BootPhase = 'starting' | 'ready' | 'failed';

export interface BootStep {
  id: string;
  label: string;
  /** Milliseconds the step took. Absent while it is still running. */
  ms?: number;
  /**
   * The step did not succeed. **Not** fatal by itself: a plugin that failed to
   * load or a backend that is down degrades the app rather than stopping it, and
   * a splash that treats every hiccup as a crash is one users learn to ignore.
   */
  error?: string;
}

export interface BootState {
  phase: BootPhase;
  steps: BootStep[];
  /** Set only when boot itself threw — the shell will not mount. */
  fatal?: string;
}

let state: BootState = { phase: 'starting', steps: [] };
const listeners = new Set<() => void>();

function publish(next: BootState): void {
  // New reference every publish; `useSyncExternalStore` compares by identity.
  state = next;
  listeners.forEach((l) => l());
}

export const bootStore = {
  getSnapshot(): BootState {
    return state;
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};

/**
 * Run `work` as a named boot step, recording how long it took and whether it
 * failed. Returns whatever `work` returned; a rejection is **recorded and
 * swallowed**, because these steps are individually non-fatal — see `BootStep`.
 */
export async function bootStep<T>(
  id: string,
  label: string,
  work: () => T | Promise<T>,
): Promise<T | undefined> {
  const started = performance.now();
  publish({ ...state, steps: [...state.steps, { id, label }] });
  const finish = (patch: Partial<BootStep>) => {
    publish({
      ...state,
      steps: state.steps.map((s) => (s.id === id ? { ...s, ...patch } : s)),
    });
  };
  try {
    const result = await work();
    finish({ ms: Math.round(performance.now() - started) });
    return result;
  } catch (err: unknown) {
    finish({ ms: Math.round(performance.now() - started), error: String(err) });
    return undefined;
  }
}

export function bootReady(): void {
  publish({ ...state, phase: 'ready' });
}

export function bootFailed(error: string): void {
  publish({ ...state, phase: 'failed', fatal: error });
}

/** Test-only, and the dev-time reset for a hot reload of the entry module. */
export function resetBootForTests(): void {
  publish({ phase: 'starting', steps: [] });
}
