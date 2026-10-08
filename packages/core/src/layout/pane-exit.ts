/**
 * A pane's exit, played before it is removed.
 *
 * `closePaneGuarded` is the single close path (buttons, keys, the taskbar, agent
 * tools), so it is the one place an exit can be shown for every close. The shell
 * registers an animator; core only waits for it — briefly and never longer than
 * `PANE_EXIT_MAX_MS`, so a stalled animation cannot hold a close hostage. It runs
 * after the close guard has said yes: a pane that vetoes never plays its exit.
 */

/** Plays the exit of `instanceId`; resolves when it is done (or at once). */
export type PaneExitAnimator = (instanceId: string) => Promise<void> | void;

/** The longest a close waits for its exit. */
export const PANE_EXIT_MAX_MS = 200;

let animator: PaneExitAnimator | null = null;

/** Install the animator; returns an uninstaller that removes only this one. */
export function setPaneExitAnimator(fn: PaneExitAnimator): () => void {
  animator = fn;
  return () => {
    if (animator === fn) animator = null;
  };
}

/** Play `instanceId`'s exit, if anything animates it. Never throws. */
export async function runPaneExit(instanceId: string): Promise<void> {
  const play = animator;
  if (!play) return;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      Promise.resolve(play(instanceId)),
      new Promise<void>((resolve) => {
        timer = setTimeout(resolve, PANE_EXIT_MAX_MS);
      }),
    ]);
  } catch {
    // An animation that fails to run is no reason not to close.
  } finally {
    clearTimeout(timer);
  }
}
