import { registry, type ModuleManifest } from '../../registry';
import { stepperAction } from './actions';
import { AgentpediaHub } from './AgentpediaHub';

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
  panels: [
    {
      // `document`: you read one turn for a long stretch, usually beside the chat
      // or the code that produced it — the same call trajectories made, for the
      // same reason. A widget takes an area alone, which is wrong for a pane whose
      // workflow is "read the round, change the harness, run it again".
      id: 'agentpedia.hub',
      title: 'Agentpedia',
      component: AgentpediaHub,
      role: 'document',
      icon: '📖',
      singleton: true,
      sections: [
        { id: 'runs', label: 'Runs', icon: '▤', key: 'r', default: true },
        { id: 'harness', label: 'Harness', icon: '⚖', key: 'h' },
        { id: 'forks', label: 'Forks', icon: '⑂', key: 'f' },
      ],
    },
  ],
  commands: [
    {
      id: 'agentpedia.open',
      title: 'Agentpedia: Step through an agent turn',
      run: () => registry.openPanel('agentpedia.hub'),
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
  // The section keys (`r`/`h`/`f`) are declared on the sections themselves, not
  // here: the host's tab strip owns them, and a second binding would be a second
  // authority over one value.
  keybindings: [
    {
      key: 'left',
      command: 'agentpedia.prevRound',
      when: "paneFocus == 'agentpedia.hub' && !textInput",
    },
    {
      key: 'right',
      command: 'agentpedia.nextRound',
      when: "paneFocus == 'agentpedia.hub' && !textInput",
    },
  ],
};
