/**
 * Chat slash commands: `/`-prefixed inputs that run **locally** in the chat widget
 * (no model turn) and render their output as an ephemeral system message. `/tools`
 * introspects the agent's live tool catalog over the WS; the rest are quick local
 * utilities. New subsystems (MCP, skills) get an honest placeholder until they land.
 * See docs/modules/agent-chat.md.
 */
import { registry } from '../../registry';
import { requestAgentTools } from './orchestrator-client';
import { getSetting, resetSetting, setSetting } from '../../settings';

/** Hooks the chat widget passes in so commands can act on its state. */
export interface SlashContext {
  /** Start a fresh chat session (Part 1 sessions). */
  newSession: () => void | Promise<void>;
}

export interface SlashCommand {
  /** Command name without the leading slash. */
  name: string;
  description: string;
  /** Returns text to show as a system message. */
  run: (args: string, ctx: SlashContext) => string | Promise<string>;
}

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    name: 'help',
    description: 'List the available slash commands.',
    run: () =>
      ['Slash commands:', ...SLASH_COMMANDS.map((c) => `  /${c.name} — ${c.description}`)].join(
        '\n',
      ),
  },
  {
    name: 'tools',
    description: "Show the agent's current tool catalog.",
    run: async () => {
      const tools = await requestAgentTools();
      if (tools.length === 0) return 'No tools reported (is the backend connected?).';
      const fmt = (kind: 'layout' | 'widget') =>
        tools
          .filter((t) => t.source === kind)
          .map((t) => `  • ${t.name} — ${t.description}`)
          .join('\n');
      const layout = fmt('layout');
      const widget = fmt('widget');
      return [
        `Agent tools (${tools.length}):`,
        layout && `\nLayout & workspace:\n${layout}`,
        widget && `\nWidget & command tools:\n${widget}`,
      ]
        .filter(Boolean)
        .join('\n');
    },
  },
  {
    name: 'panes',
    description: 'List the panes currently open in the workspace.',
    run: () => {
      const panes = registry.layoutController?.listOpenPanes() ?? [];
      if (panes.length === 0) return 'No panes open.';
      return [
        'Open panes:',
        ...panes.map((p) => `  • ${p.title} (${p.id})${p.hasContext ? ' — agent-readable' : ''}`),
      ].join('\n');
    },
  },
  {
    name: 'clear',
    description: 'Start a new chat session.',
    run: async (_args, ctx) => {
      await ctx.newSession();
      return 'Started a new session.';
    },
  },
  {
    name: 'mcp',
    description: 'List connected MCP servers.',
    run: () => 'No MCP servers configured yet.',
  },
  {
    name: 'skills',
    description: 'List available skills.',
    run: () => 'No skills available yet.',
  },
  {
    name: 'llm',
    description: 'Manage orchestrator LLM settings: /llm, /llm set <param> <val>, /llm reset [param]',
    run: async (args) => {
      const parts = args.trim().split(/\s+/).filter(Boolean);
      const keys = {
        model: { key: 'agent.orchestrator.model', type: 'string', name: 'Model override' },
        temperature: { key: 'agent.orchestrator.temperature', type: 'number', name: 'Temperature' },
        temp: { key: 'agent.orchestrator.temperature', type: 'number', name: 'Temperature' },
        contextsize: { key: 'agent.orchestrator.contextSize', type: 'number', name: 'Context size' },
        context_size: { key: 'agent.orchestrator.contextSize', type: 'number', name: 'Context size' },
        ctx: { key: 'agent.orchestrator.contextSize', type: 'number', name: 'Context size' },
        maxtokens: { key: 'agent.orchestrator.maxTokens', type: 'number', name: 'Max tokens' },
        max_tokens: { key: 'agent.orchestrator.maxTokens', type: 'number', name: 'Max tokens' },
        max_predict: { key: 'agent.orchestrator.maxTokens', type: 'number', name: 'Max tokens' },
        topp: { key: 'agent.orchestrator.topP', type: 'number', name: 'Top P' },
        top_p: { key: 'agent.orchestrator.topP', type: 'number', name: 'Top P' },
      } as const;

      if (parts.length === 0) {
        const items = [
          { name: 'Model override', key: 'agent.orchestrator.model', defaultVal: 'Configured model' },
          { name: 'Temperature', key: 'agent.orchestrator.temperature', defaultVal: '0.0' },
          { name: 'Context size', key: 'agent.orchestrator.contextSize', defaultVal: 'Default' },
          { name: 'Max tokens', key: 'agent.orchestrator.maxTokens', defaultVal: 'Default' },
          { name: 'Top P', key: 'agent.orchestrator.topP', defaultVal: 'Default' },
        ];
        const lines = items.map((item) => {
          const val = getSetting(item.key);
          const valStr = val !== undefined ? String(val) : `${item.defaultVal} (default)`;
          return `  • ${item.name} (${item.key}): ${valStr}`;
        });
        return ['Orchestrator LLM settings overrides:', ...lines].join('\n');
      }

      const action = parts[0].toLowerCase();
      if (action === 'set') {
        if (parts.length < 3) {
          return 'Error: /llm set <param> <value> requires both parameter name and value. Example: `/llm set temperature 0.5`';
        }
        const paramName = parts[1].toLowerCase();
        const param = keys[paramName as keyof typeof keys];
        if (!param) {
          return `Error: Unknown parameter "${parts[1]}". Supported parameters: model, temperature, context_size, max_tokens, top_p`;
        }
        const rawVal = parts.slice(2).join(' ');
        let val: string | number;
        if (param.type === 'number') {
          const num = Number(rawVal);
          if (isNaN(num)) {
            return `Error: Value for parameter "${param.name}" must be a number. Got: "${rawVal}"`;
          }
          val = num;
        } else {
          val = rawVal;
        }
        await setSetting(param.key, val);
        return `Successfully set ${param.name} (${param.key}) to ${val}.`;
      }

      if (action === 'reset' || action === 'clear') {
        if (parts.length === 1) {
          await resetSetting('agent.orchestrator.model');
          await resetSetting('agent.orchestrator.temperature');
          await resetSetting('agent.orchestrator.contextSize');
          await resetSetting('agent.orchestrator.maxTokens');
          await resetSetting('agent.orchestrator.topP');
          return 'Successfully reset all orchestrator LLM overrides to defaults.';
        }
        const paramName = parts[1].toLowerCase();
        const param = keys[paramName as keyof typeof keys];
        if (!param) {
          return `Error: Unknown parameter "${parts[1]}". Supported parameters: model, temperature, context_size, max_tokens, top_p`;
        }
        await resetSetting(param.key);
        return `Successfully reset ${param.name} (${param.key}) override.`;
      }

      return 'Unknown subcommand. Try `/llm`, `/llm set <param> <val>`, or `/llm reset [param]`.';
    },
  },
];

const ALIASES: Record<string, string> = { new: 'clear', '?': 'help' };

/** Commands whose name matches the typed `/prefix` (for the suggestion list). */
export function matchSlash(input: string): SlashCommand[] {
  if (!input.startsWith('/')) return [];
  const prefix = input.slice(1).split(/\s+/, 1)[0].toLowerCase();
  return SLASH_COMMANDS.filter((c) => c.name.startsWith(prefix));
}

/** Run a `/command` input, returning the text to show. Unknown → a help hint. */
export async function runSlash(input: string, ctx: SlashContext): Promise<string> {
  const trimmed = input.trim().slice(1);
  const [rawName, ...rest] = trimmed.split(/\s+/);
  const name = ALIASES[rawName.toLowerCase()] ?? rawName.toLowerCase();
  const cmd = SLASH_COMMANDS.find((c) => c.name === name);
  if (!cmd) return `Unknown command: /${rawName}. Try /help.`;
  return cmd.run(rest.join(' '), ctx);
}
