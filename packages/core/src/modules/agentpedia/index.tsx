import type { ModuleManifest } from '../../registry';
import { SUITE_SECTIONS, whenSuiteSection } from '../interp/ids';
import { openSuiteTab } from '../interp/stage';
import { stepperAction } from './actions';

/** Open agentpedia at one of its sub-tabs inside the IDE's Runs workspace. */
export function openAgentpedia(tab: 'runs' | 'harness' | 'forks' | 'duels' = 'runs'): void {
  openSuiteTab(SUITE_SECTIONS.runs, { subTab: tab });
}

/**
 * Step into one recorded turn in the 4-column Turn Debugger, preserving any
 * open IDE tabs or side-by-side split on `interpretability.suite`.
 */
export function openAgentpediaTurn(turnId: string): void {
  openSuiteTab(SUITE_SECTIONS.runs, { subTab: 'runs', params: { turnId } });
}

export const agentpediaModule: ModuleManifest = {
  id: 'agentpedia',
  title: 'Agentpedia',
  category: 'agents',
  commands: [
    {
      id: 'agentpedia.open',
      title: 'Agentpedia: Step through an agent turn',
      run: () => openAgentpedia('runs'),
    },
    {
      id: 'agentpedia.prevRound',
      title: 'Agentpedia: Previous round',
      run: () => stepperAction('prevRound'),
    },
    {
      id: 'agentpedia.nextRound',
      title: 'Agentpedia: Next round',
      run: () => stepperAction('nextRound'),
    },
  ],
  keybindings: [
    {
      key: 'left',
      command: 'agentpedia.prevRound',
      when: `${whenSuiteSection(SUITE_SECTIONS.runs)} && !textInput`,
    },
    {
      key: 'right',
      command: 'agentpedia.nextRound',
      when: `${whenSuiteSection(SUITE_SECTIONS.runs)} && !textInput`,
    },
  ],
};
