/**
 * The PROMPT group: what the model was shown, and what an agent run did with it.
 * Both bodies are the panes they used to be, framed by the suite's state strip.
 */
import { AgentpediaHub } from '../../agentpedia/AgentpediaHub';
import { AGENTPEDIA_TABS } from '../../agentpedia/tabs';
import { InterpretabilityPanel } from '../../interpretability/view';
import { SUITE_SECTIONS } from '../ids';
import { SubSections } from '../SubSections';
import { SuiteFrame } from '../SuiteFrame';

export function ContextBody() {
  return (
    <SuiteFrame title="Context window">
      <InterpretabilityPanel />
    </SuiteFrame>
  );
}

export function RunsBody() {
  return (
    <SuiteFrame title="Agent runs">
      <SubSections scope={SUITE_SECTIONS.runs} tabs={AGENTPEDIA_TABS}>
        <AgentpediaHub />
      </SubSections>
    </SuiteFrame>
  );
}
