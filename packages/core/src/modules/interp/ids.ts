/**
 * The interpretability suite's addresses, in a leaf file of constants.
 *
 * Every module whose pane folded into the suite (interpretability, llama.cpp,
 * agentpedia, webml) still owns its commands and keybindings, and they now need
 * to name the suite and its sections. Importing the suite's manifest for that
 * would import every body the suite lazily loads; this file imports nothing.
 */

/** The one window. */
export const SUITE_VIEW = 'interpretability.suite';

/** Rail sections, in rail order. */
export const SUITE_SECTIONS = {
  home: 'home',
  context: 'context',
  runs: 'runs',
  model: 'model',
  attention: 'attention',
  design: 'design',
  traces: 'traces',
  stepper: 'stepper',
  lens: 'lens',
  heads: 'heads',
} as const;

export type SuiteSection = (typeof SUITE_SECTIONS)[keyof typeof SUITE_SECTIONS];

/** A `when` clause: the suite is focused and showing `section`. */
export function whenSuiteSection(section: SuiteSection): string {
  return `paneFocus == '${SUITE_VIEW}' && paneSection == '${section}'`;
}
