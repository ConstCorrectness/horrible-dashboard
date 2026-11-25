/**
 * The interpretability IDE's addresses, in a leaf file of constants.
 *
 * Every module whose pane folded into the IDE (interpretability, llama.cpp,
 * agentpedia, trajectories, webml, evals) still owns its commands and keybindings,
 * and they now need to name the suite and its sections. Importing the suite's
 * manifest for that would import every body the suite lazily loads; this file
 * imports nothing.
 */

/** The one window. */
export const SUITE_VIEW = 'interpretability.suite';

/** Rail sections, in progressive IDE rail order. */
export const SUITE_SECTIONS = {
  home: 'home',
  playground: 'playground',
  runtime: 'runtime',
  evals: 'evals',
  context: 'context',
  runs: 'runs',
  model: 'model',
  attention: 'attention',
  lens: 'lens',
  stepper: 'stepper',
  traces: 'traces',
  neurons: 'neurons',
  heads: 'heads',
  features: 'features',
  steer: 'steer',
  axis: 'axis',
  graph: 'graph',
  nla: 'nla',
  design: 'design',
  library: 'library',
} as const;

export type SuiteSection = (typeof SUITE_SECTIONS)[keyof typeof SUITE_SECTIONS];

/** A `when` clause: the suite is focused and showing `section`. */
export function whenSuiteSection(section: SuiteSection): string {
  return `paneFocus == '${SUITE_VIEW}' && paneSection == '${section}'`;
}
