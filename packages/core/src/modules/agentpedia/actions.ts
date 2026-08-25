/**
 * How the round-scrubbing keybindings reach the stepper.
 *
 * `←`/`→` move between rounds, but which round is showing is component state, and
 * commands are declared at module load outside React. Same shape as the model
 * designer's `bindDesigner`, for the same reason and with the same two
 * consequences: the pane is a singleton so there is exactly one handle, and an
 * unmounted pane makes the commands no-ops — which is correct, since the bindings
 * are pane-scoped anyway.
 *
 * Never install a `keydown` listener in a component; `packages/core/src/keymap/` is
 * the one keyboard authority. See docs/architecture/keybindings.mdx.
 */

export interface StepperActions {
  /** Previous round of the open turn. Stops at the first — a turn has no round -1. */
  prevRound(): void;
  /** Next round. Stops at the last. */
  nextRound(): void;
}

let live: StepperActions | null = null;

/** Called by the stepper on mount, and with `null` on unmount. */
export function bindStepper(actions: StepperActions | null): void {
  live = actions;
}

/** Run one action if a stepper is mounted. A no-op otherwise, by design. */
export function stepperAction(name: keyof StepperActions): void {
  live?.[name]();
}
