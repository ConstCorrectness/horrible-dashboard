/**
 * The chat's three modes, and the slash commands that switch between them.
 *
 * Each is one of the backend's permission modes (`permissions.Mode`), picked per
 * chat and sent with every `ask`, where it outranks the roster agent's default:
 *
 * - **Plan** (`plan`): read-only. Tools that act are hidden from the model, which
 *   investigates, asks, and finishes with a plan for the user to approve.
 * - **Agent** (`default`): acts, and asks before anything with a side effect that
 *   is not already allowed.
 * - **Auto** (`autonomous`): acts without asking, short of deny rules and the
 *   circuit breakers.
 *
 * A chat with no mode of its own (`null`) follows the Settings default. The fourth
 * backend mode, `acceptEdits`, stays a Settings-only choice; a chat following it
 * shows as Agent, which is what it is closest to.
 */

export type AgentMode = 'plan' | 'default' | 'autonomous';

export interface ModeInfo {
  mode: AgentMode;
  label: string;
  /** One line for the picker menu. */
  hint: string;
  /** The composer's placeholder in this mode. */
  placeholder: string;
}

export const MODES: ModeInfo[] = [
  {
    mode: 'plan',
    label: 'Plan',
    hint: 'Read-only research that ends in a plan you approve',
    placeholder: 'Plan with the agent…',
  },
  {
    mode: 'default',
    label: 'Agent',
    hint: 'Acts, and asks before anything with side effects',
    placeholder: 'Ask the agent…',
  },
  {
    mode: 'autonomous',
    label: 'Auto',
    hint: 'Acts without asking — only deny rules stop it',
    placeholder: 'Tell the agent what to do…',
  },
];

export function modeInfo(mode: AgentMode): ModeInfo {
  return MODES.find((m) => m.mode === mode) ?? MODES[1];
}

/** Read a stored or global mode value into one the picker shows. */
export function asAgentMode(value: unknown): AgentMode | null {
  if (value === 'plan' || value === 'default' || value === 'autonomous') return value;
  // `acceptEdits` — the Settings-only mode — reads as Agent.
  if (value === 'acceptEdits') return 'default';
  return null;
}

/** Shift+Tab's order: Agent → Plan → Auto → Agent. */
export function nextMode(mode: AgentMode): AgentMode {
  return mode === 'default' ? 'plan' : mode === 'plan' ? 'autonomous' : 'default';
}

/** The slash command for each mode. `/agent` already switches roster agents, so
 *  Agent mode answers to `/act`; `/mode <name>` takes any of the labels too. */
export const MODE_COMMANDS: Record<string, AgentMode> = {
  plan: 'plan',
  act: 'default',
  auto: 'autonomous',
};

const BY_NAME: Record<string, AgentMode> = {
  ...MODE_COMMANDS,
  agent: 'default',
  default: 'default',
  autonomous: 'autonomous',
};

/**
 * `/plan`, `/act`, `/auto`, or `/mode <name>`, with anything typed after it — so
 * `/plan add a dark-mode toggle` switches and sends in one go. Null for any other
 * input, including `/mode` with no name it recognises.
 */
export function parseModeCommand(input: string): { mode: AgentMode; rest: string } | null {
  const match = /^\/(\S+)(?:\s+([\s\S]*))?$/.exec(input.trim());
  if (!match) return null;
  const name = match[1].toLowerCase();
  const rest = (match[2] ?? '').trim();
  if (name in MODE_COMMANDS) return { mode: MODE_COMMANDS[name], rest };
  if (name !== 'mode') return null;
  const [target, ...more] = rest.split(/\s+/);
  const mode = BY_NAME[target?.toLowerCase() ?? ''];
  return mode ? { mode, rest: more.join(' ').trim() } : null;
}
