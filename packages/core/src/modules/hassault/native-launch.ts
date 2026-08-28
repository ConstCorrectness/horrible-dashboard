/**
 * Watching a native-client launch, from a component that may not survive it.
 *
 * A launch is a **job on the node** (`_LAUNCH_JOBS` in `routes.py`), and this is
 * the client half of that. Two failures it exists to close, both of which read
 * as the same thing on screen — a button stuck on "Launching…" forever:
 *
 * 1. **A launch can take minutes.** When the client has been edited since it was
 *    last built the route compiles it first, and a cold `cargo build --release`
 *    of that crate builds `wgpu`. The POST now answers `phase: 'building'`
 *    instead of not answering, and this polls until it is done.
 * 2. **The pane does not survive a tab switch.** Every hassault surface lives in
 *    a frame tab, and an inactive tab is unmounted — so the promise the launch
 *    was awaiting is dropped along with the state that was tracking it. Which is
 *    why this reads `nativeLaunchStatus()` **on mount** rather than only after a
 *    press: coming back to the tab picks the same job up again.
 *
 * Deliberately free of any layout, so the menu row and the in-game panel share
 * one implementation instead of each growing their own half-correct copy.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { launchNativeFps, nativeLaunchStatus } from './api';
import type { LaunchNativeOptions, LaunchNativeResult } from './api';

/** How often a running launch is asked about. */
const POLL_MS = 1000;

/** The phases that are still going, and so are worth polling. */
function pending(result: LaunchNativeResult | null): boolean {
  return result?.phase === 'building' || result?.phase === 'starting';
}

export interface NativeLaunch {
  /** The last thing the node said, or `null` before anything has been asked. */
  result: LaunchNativeResult | null;
  /** A launch is in flight — including one started before this mount. */
  busy: boolean;
  /** Something to put on screen: the node's message, or the phase's own. */
  message: string | null;
  launch: (opts: LaunchNativeOptions) => Promise<void>;
}

export function useNativeLaunch(): NativeLaunch {
  const [result, setResult] = useState<LaunchNativeResult | null>(null);
  // Mutable rather than state: a poll that answers after the pane is gone must
  // not set state, and the check for that must not itself re-run the effect.
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  /**
   * Whether this mount has seen a launch that was still going.
   *
   * The reason a *finished* job is not adopted on sight: `_LAUNCH_JOBS` keeps the
   * last launch for the life of the backend, so a pane mounted an hour later
   * would otherwise come up announcing a pid that exited long ago — the same
   * stale-message bug the process poll already fixed once. A job that is running
   * when we find it is ours to report, and so is whatever it becomes.
   */
  const watching = useRef(false);

  const adopt = useCallback((next: LaunchNativeResult) => {
    if (!alive.current) return;
    // `idle` is the node saying nothing has been launched from here. Adopting it
    // would put a message on screen where there is nothing to report.
    if (next.phase === 'idle') {
      setResult(null);
      return;
    }
    if (pending(next)) watching.current = true;
    else if (!watching.current) return;
    setResult(next);
  }, []);

  const refresh = useCallback(async () => {
    try {
      adopt(await nativeLaunchStatus());
    } catch {
      // An unreachable node is not a reason to spin. The next press asks again,
      // and a poll that retried through a dead backend would leave a button
      // permanently "launching" — the exact state this hook exists to end.
    }
  }, [adopt]);

  // **On mount.** This is the tab-switch half: a pane is unmounted when its tab
  // loses focus, so a build started before that has nothing left watching it.
  // Asking the node on the way back in picks the same job up.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const busy = pending(result);

  // And for as long as one is running. Keyed on `busy` rather than on the whole
  // result, so an answer that changes nothing does not restart the timer.
  useEffect(() => {
    if (!busy) return undefined;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [busy, refresh]);

  const launch = useCallback(
    async (opts: LaunchNativeOptions) => {
      // Optimistic, and not a guess: the request is on its way, so the button is
      // busy before the answer arrives. It also arms the poll above, which is
      // what carries a launch that turns out to be a build.
      watching.current = true;
      setResult({ launched: false, connect_args: [], phase: 'starting' });
      try {
        adopt(await launchNativeFps(opts));
      } catch (err) {
        adopt({
          launched: false,
          connect_args: [],
          phase: 'failed',
          message: err instanceof Error ? err.message : 'Could not reach this node',
        });
      }
    },
    [adopt],
  );

  return { result, busy, message: result?.message ?? null, launch };
}
