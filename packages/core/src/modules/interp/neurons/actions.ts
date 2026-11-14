/**
 * How the Neurons section's keybindings reach its scene — the stepper's
 * `bindLayerStepper` shape. The scene is component state and commands are declared
 * at module load, so the section publishes a handle on mount and clears it on
 * unmount; with none mounted every command is a no-op.
 *
 * Never a `keydown` listener in a component: `packages/core/src/keymap/` is the one
 * keyboard authority.
 */

export const NEURON_VERBS = [
  ['left', 'Select the neuron to the left'],
  ['right', 'Select the neuron to the right'],
  ['up', 'Select the neuron above'],
  ['down', 'Select the neuron below'],
  ['orbitLeft', 'Orbit left'],
  ['orbitRight', 'Orbit right'],
  ['orbitUp', 'Orbit up'],
  ['orbitDown', 'Orbit down'],
  ['previousLayer', 'Previous layer'],
  ['nextLayer', 'Next layer'],
  ['zoomIn', 'Zoom in'],
  ['zoomOut', 'Zoom out'],
  ['inspect', 'Inspect the selected neuron (toggle)'],
  ['resetView', 'Reset the view'],
  ['addSelected', 'Add the selected neuron to the recipe'],
  ['undo', 'Undo the last recipe change'],
] as const;

export type NeuronVerb = (typeof NEURON_VERBS)[number][0];

/** Keys, scoped to the section and guarded against text inputs by the manifest. */
export const NEURON_KEYS: readonly (readonly [string, NeuronVerb])[] = [
  ['left', 'left'],
  ['right', 'right'],
  ['up', 'up'],
  ['down', 'down'],
  ['alt+left', 'orbitLeft'],
  ['alt+right', 'orbitRight'],
  ['alt+up', 'orbitUp'],
  ['alt+down', 'orbitDown'],
  ['pageup', 'previousLayer'],
  ['pagedown', 'nextLayer'],
  ['=', 'zoomIn'],
  ['-', 'zoomOut'],
  ['i', 'inspect'],
  ['r', 'resetView'],
  ['a', 'addSelected'],
  ['mod+z', 'undo'],
];

export type NeuronActions = Record<NeuronVerb, () => void>;

let live: NeuronActions | null = null;

export function bindNeuronActions(actions: NeuronActions | null): void {
  live = actions;
}

export function neuronAction(verb: NeuronVerb): void {
  live?.[verb]();
}
