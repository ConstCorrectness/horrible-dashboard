/**
 * How the stepper's keybindings reach it — agentpedia's `bindStepper` shape.
 *
 * The cursor is component state and commands are declared at module load, so the
 * section publishes a handle on mount and clears it on unmount. With no Stepper
 * section mounted every command is a no-op, which is correct: the bindings are
 * scoped to the llama.cpp pane, and its other sections have nothing to step.
 *
 * Never a `keydown` listener in a component — `packages/core/src/keymap/` is the one
 * keyboard authority (docs/architecture/keybindings.mdx).
 */

export interface LayerStepperActions {
  stepInto(): void;
  stepBack(): void;
  stepOver(): void;
  reverseStepOver(): void;
  stepOut(): void;
  continueForward(): void;
  continueBack(): void;
  toggleBreakpoint(): void;
  togglePlay(): void;
  restart(): void;
}

let live: LayerStepperActions | null = null;

export function bindLayerStepper(actions: LayerStepperActions | null): void {
  live = actions;
}

export function layerStepperAction(name: keyof LayerStepperActions): void {
  live?.[name]();
}
