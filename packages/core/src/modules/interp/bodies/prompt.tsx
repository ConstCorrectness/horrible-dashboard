/**
 * The Agents & Prompts group: exact prompt composition (`ContextBody`) and the
 * unified Agent Runs & Trajectories workspace (`RunsBody`).
 */
import { InterpretabilityPanel } from '../../interpretability/view';
import { SuiteFrame } from '../SuiteFrame';
import { RunsWorkspace } from './RunsWorkspace';

export function ContextBody() {
  return (
    <SuiteFrame title="Context window">
      <InterpretabilityPanel />
    </SuiteFrame>
  );
}

export function RunsBody() {
  return (
    <SuiteFrame title="Runs & trajectories">
      <RunsWorkspace />
    </SuiteFrame>
  );
}
