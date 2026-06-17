import { registry, type ModuleManifest } from '../../registry';
import { ChatWidget } from './ChatWidget';
import { PermissionsSettings } from './PermissionsSettings';

/** See docs/modules/agent-chat.md. The home view hosts onboarding + a one-shot ask
 * bar; the `agent.chat` widget is the multi-turn conversational pane. The settings
 * section surfaces the permission mode + rule lists that gate side-effecting tools. */
export const agentModule: ModuleManifest = {
  id: 'agent',
  title: 'Agent',
  widgets: [
    {
      id: 'agent.chat',
      title: 'Agent',
      component: ChatWidget,
      defaultPlacement: 'right',
    },
  ],
  commands: [
    {
      id: 'agent.openChat',
      title: 'Agent: Open chat',
      run: () => registry.openPanel('agent.chat'),
    },
  ],
  settings: [
    {
      key: 'agent.avatarAnimation',
      title: 'Agent avatar animation',
      description:
        'Show the animated 3D avatar in the Agent pane, cycling through its moods. Turn off for a plain text agent pane.',
      type: 'boolean',
      default: true,
    },
  ],
  settingsSections: [
    { id: 'agent.permissions', title: 'Agent permissions', component: PermissionsSettings },
  ],
};

export * from './api';
export * from './sessions';
export {
  askAgent,
  requestAgentTools,
  type AgentCallbacks,
  type AgentToolInfo,
  type AgentTurn,
} from './orchestrator-client';
export { initAgentManifestSync, serializeManifest, type SerializedTool } from './manifest';
export {
  initApprovalListener,
  respondApproval,
  useApprovals,
  type ApprovalDecision,
  type PendingApproval,
} from './approvals';
