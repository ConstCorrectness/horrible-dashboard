import { openPane, revealSection } from '../../layout/controller';
import type { ModuleManifest } from '../../registry';
import { SUITE_SECTIONS, SUITE_VIEW, whenSuiteSection } from '../interp/ids';
import { setSubSection } from '../interp/SubSections';
import { stepperAction } from './actions';

/** Open agentpedia at one of its tabs: the suite's Runs section, one level down. */
export function openAgentpedia(tab: 'runs' | 'harness' | 'forks' | 'duels' = 'runs'): void {
  setSubSection(SUITE_SECTIONS.runs, tab);
  revealSection(SUITE_SECTIONS.runs, SUITE_VIEW);
}

/**
 * Step into one recorded turn. The hub reads `turnId` from its pane's params, and
 * its pane is the suite now — so the param goes on the suite, then Runs opens.
 */
export function openAgentpediaTurn(turnId: string): void {
  openPane(SUITE_VIEW, { params: { turnId } });
  openAgentpedia('runs');
}

/**
 * Agentpedia: one agent turn, steppable.
 *
 * The node already recorded three halves of every turn and never put them beside
 * each other. `agent_turns` says what the model was **shown**. The telemetry ring
 * says what went over the **wire**. `traj_runs`/`traj_steps` say what the agent
 * **did**. Reading a turn meant three panes and a lot of matching by timestamp.
 *
 * So this module **owns no store**. Everything it shows is joined at read time from
 * modules that already own it, which is also why it can exist at all: the
 * `turn_id`/`round` stamp on every `IoEvent` is what makes the wire half matchable,
 * and adding it was three lines in the loop.
 *
 * The name is the point of comparison: a Neuronpedia feature page gives a *feature*
 * a permanent address you can cite and compare. A harness deserves the same — the
 * thing that decides how an agent behaves should not be an untracked implementation
 * detail of a run.
 *
 * See docs/modules/agentpedia.mdx.
 */
export const agentpediaModule: ModuleManifest = {
  id: 'agentpedia',
  title: 'Agentpedia',
  category: 'agents',
  // The hub is the interpretability suite's Runs section now (its four tabs are
  // `tabs.ts`, one level down). Reading a turn belongs beside the prompt it
  // assembled and the model it ran on, which is the suite's whole subject.
  commands: [
    {
      id: 'agentpedia.open',
      title: 'Agentpedia: Step through an agent turn',
      run: () => openAgentpedia('runs'),
    },
    // The scrubbing verbs. They call through `actions.ts`, which the stepper
    // publishes while mounted — with no turn open they are no-ops, which is the
    // correct behaviour rather than a gap.
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
  // Scoped to the pane *and* guarded with `!textInput`: unguarded, an arrow key
  // pressed while a filter box has focus would scrub the round out from under the
  // cursor. A binding naming `paneFocus` already beats an unscoped global, so the
  // pane does not need to take capture — which matters, because a stepper that
  // swallowed the keyboard would break the palette.
  //
  //
  // And scoped to the suite's Runs section, not only the pane: the suite keeps
  // the stepper mounted behind its other tools, and an arrow pressed in the lens
  // must not scrub a turn nobody is looking at.
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
