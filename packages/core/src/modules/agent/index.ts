import { registry, type ModuleManifest } from '../../registry';
import { ChatWidget } from './ChatWidget';
import { OrchestratorSettings } from './OrchestratorSettings';
import { PermissionsSettings } from './PermissionsSettings';
import { resetSetting, setSetting, type SettingValue } from '../../settings';

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
      agentTools: [
        {
          name: 'agent.setHyperparameters',
          description:
            "Update the agent's model hyperparameters (temperature, contextSize, maxTokens, topP, or model override) for subsequent orchestrator turns. To clear an override, pass null or omit it.",
          params: {
            type: 'object',
            properties: {
              model: {
                type: 'string',
                description: 'Model name override to use, or null to reset to the configured model',
              },
              temperature: {
                type: 'number',
                description: 'Sampling temperature (e.g. 0.0 to 1.0)',
              },
              contextSize: {
                type: 'number',
                description: 'Maximum context window size in tokens (num_ctx)',
              },
              maxTokens: {
                type: 'number',
                description: 'Maximum tokens to generate per turn (max_tokens / num_predict)',
              },
              topP: {
                type: 'number',
                description: 'Top P nucleus sampling value',
              },
            },
          },
          sideEffect: true,
          handler: async (args) => {
            const keys = {
              model: 'agent.orchestrator.model',
              temperature: 'agent.orchestrator.temperature',
              contextSize: 'agent.orchestrator.contextSize',
              maxTokens: 'agent.orchestrator.maxTokens',
              topP: 'agent.orchestrator.topP',
            } as const;

            const updated: string[] = [];
            for (const [key, settingKey] of Object.entries(keys)) {
              if (key in args) {
                const val = args[key];
                if (val === null || val === undefined) {
                  await resetSetting(settingKey);
                  updated.push(`${key} (reset)`);
                } else {
                  await setSetting(settingKey, val as SettingValue);
                  updated.push(`${key}=${val}`);
                }
              }
            }

            if (updated.length === 0) {
              return { ok: false, error: 'no parameters specified' };
            }
            return { ok: true, message: `Updated settings: ${updated.join(', ')}` };
          },
        },
      ],
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
  // The orchestrator model is a dropdown of the provider's live models (not a static
  // enum), so it's a custom section rather than a declarative SettingDecl; temperature
  // rides along with it. See OrchestratorSettings.
  settingsSections: [
    { id: 'agent.orchestrator', title: 'Agent orchestrator', component: OrchestratorSettings },
    { id: 'agent.permissions', title: 'Agent permissions', component: PermissionsSettings },
  ],
};

export * from './api';
export * from './sessions';
export {
  askAgent,
  initAgentRelay,
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
