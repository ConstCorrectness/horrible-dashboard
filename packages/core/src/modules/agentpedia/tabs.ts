/**
 * Agentpedia's sub-sections. It was a pane with these as its sections; inside the
 * interpretability suite it is the Runs section, and these are the tabs one level
 * down (`interp/SubSections.tsx`). `AgentpediaHub` still switches on
 * `usePaneSection()`, which the suite scopes to this list.
 */
export const AGENTPEDIA_TABS = [
  { id: 'runs', label: 'Runs' },
  { id: 'harness', label: 'Harness' },
  { id: 'forks', label: 'Forks' },
  { id: 'duels', label: 'Duels' },
] as const;
