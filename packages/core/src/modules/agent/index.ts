import { registry, type ModuleManifest } from '../../registry';
import { ChatWidget } from './ChatWidget';
import { ApiKeysSettings } from './ApiKeysSettings';
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
      role: 'tool',
      icon: '🤖',
      // The transcript itself is not shared here; a guest only learns the
      // agent pane is open. Sharing what was said needs the `agent` grant.
      share: { mode: 'mirror' },
      defaultDock: 'right',
      // Chat needs more room than a tree/outline tool; the user's own resize
      // overrides this permanently once they drag it.
      defaultDockSize: 420,
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
    {
      key: 'agent.workspaceContext',
      title: 'Send workspace context',
      description:
        'Attach a snapshot of the panes visible in the current workspace to every turn, so the agent knows what you are looking at without asking. Turn off to send only the focused pane.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'agent.workspaceContextPanes',
      title: 'Workspace context: panes',
      description:
        'How many visible panes may contribute their snapshot to a turn. This rides on every turn, so raising it costs context on all of them.',
      type: 'number',
      default: 6,
    },
    {
      key: 'agent.workspaceContextBudget',
      title: 'Workspace context: characters per pane',
      description:
        "Size limit for each pane's snapshot. Oversized fields are clipped with a note pointing the agent at get_pane_context for the full read.",
      type: 'number',
      default: 1500,
    },
    {
      key: 'agent.activeBufferBudget',
      title: 'Focused buffer: characters',
      description:
        'Size limit for the focused editor buffer attached to every turn. Beyond this the agent is told the code is truncated and must re-read it before rewriting the whole file. 0 = no limit.',
      type: 'number',
      default: 24000,
    },
  ],
  // The orchestrator model is a dropdown of the provider's live models (not a static
  // enum), so it's a custom section rather than a declarative SettingDecl; temperature
  // rides along with it. See OrchestratorSettings.
  settingsSections: [
    { id: 'agent.orchestrator', title: 'Agent orchestrator', component: OrchestratorSettings },
    // API keys are a custom section for the same reason the model dropdown is, and a
    // stronger one: a key can never be a SettingDecl, because `GET /api/settings`
    // hands the whole settings bag to the browser and to every plugin. See
    // ApiKeysSettings.
    { id: 'agent.apiKeys', title: 'Model provider API keys', component: ApiKeysSettings },
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
