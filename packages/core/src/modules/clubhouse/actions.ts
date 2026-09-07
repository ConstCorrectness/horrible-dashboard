/**
 * How a keybinding reaches the live room.
 *
 * Module commands are declared at module load and run outside React, but muting the
 * mic is a method on the Agora track held in `useClubhouseVoice`'s session. Karaoke
 * solves the same problem by keeping its state on the server; there is no server
 * state to reach here, so the pane publishes a handle and the commands call through
 * it — the same shape as the model designer's `designer/actions.ts`.
 *
 * Two consequences, both load-bearing:
 *
 * - **The pane is a singleton** (`clubhouse.account` declares it), so there is
 *   exactly one handle. A second rooms pane would silently overwrite the first's.
 * - **Not in a room means the command does nothing.** That is correct rather than a
 *   gap: the handle's absence *is* how "there is no mic to toggle" is expressed, so
 *   there is no joined-state flag here to drift out of step with the truth.
 *
 * Never install a `keydown` listener in a component: `packages/core/src/keymap/` is
 * the one keyboard authority. See docs/architecture/keybindings.mdx.
 */

export interface ClubhouseActions {
  /** Push-to-talk, as a latch: mic on becomes mic off. Feeds STT and the room alike. */
  toggleMic(): void;
}

let live: ClubhouseActions | null = null;

/** Called by the rooms pane when it holds a joined room, and with `null` when not. */
export function bindClubhouse(actions: ClubhouseActions | null): void {
  live = actions;
}

/** Run one action if a room is live. A no-op otherwise, by design. */
export function clubhouseAction(name: keyof ClubhouseActions): void {
  live?.[name]();
}

/** Whether a room is currently listening — for tests, not for branching. */
export function clubhouseIsLive(): boolean {
  return live !== null;
}
