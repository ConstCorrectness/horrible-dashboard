import { type ModuleManifest } from '../../registry';
import { PermissionsSettings } from './PermissionsSettings';

/** See docs/modules/agent-chat.md. First slice: onboarding + one-shot ask on the home view. */
// No commands yet: the home view (where the ask bar lives) is reached via the
// shell's own `shell.home` command. The settings section surfaces the permission
// mode + rule lists that gate the agent's side-effecting tools (see agent-tools).
export const agentModule: ModuleManifest = {
  id: 'agent',
  title: 'Agent',
  settingsSections: [
    { id: 'agent.permissions', title: 'Agent permissions', component: PermissionsSettings },
  ],
};

export * from './api';
export { askAgent, type AgentCallbacks } from './orchestrator-client';
export { initAgentManifestSync, serializeManifest, type SerializedTool } from './manifest';
export {
  initApprovalListener,
  respondApproval,
  useApprovals,
  type ApprovalDecision,
  type PendingApproval,
} from './approvals';
