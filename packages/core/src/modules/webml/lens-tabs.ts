/**
 * The window lens lab's views. It was the `webml.lens` pane with these as its
 * sections; inside the interpretability suite they are tabs of the Lens section,
 * beside the llama.cpp trace lens. `LensLabPanel` still switches on
 * `usePaneSection()`, which the suite scopes to the Lens section's tab.
 */
export const LENS_LAB_TABS = [
  { id: 'reply', label: 'Window: reply' },
  { id: 'guess', label: 'Window: guess' },
  { id: 'compare', label: 'Window: compare' },
] as const;
