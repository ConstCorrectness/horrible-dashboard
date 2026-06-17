/**
 * Chat slash commands: `/`-prefixed inputs that run **locally** in the chat widget
 * (no model turn) and render their output as an ephemeral system message. `/tools`
 * introspects the agent's live tool catalog over the WS; the rest are quick local
 * utilities. New subsystems (MCP, skills) get an honest placeholder until they land.
 * See docs/modules/agent-chat.md.
 */
import { registry } from '../../registry';
import { requestAgentTools } from './orchestrator-client';

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
