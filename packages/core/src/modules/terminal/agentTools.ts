/**
 * Agent tools the terminal exposes (declared on the `terminal.instance` panel).
 * `terminal.list`/`terminal.read` are ungated reads; `terminal.exec` is **gated**
 * and shell-matched by the backend (the engine's `SHELL_TOOLS` set + A4b specifier
 * logic), so a command's rule/mode decision is purely the permission engine's.
 * Execution is always visible in a real terminal. See docs/architecture/agent-tools.md.
 */
import type { AgentToolDecl } from '../../registry';
import { runCommand } from './index';
import { getTerminal, listTerminals } from './store';

export const terminalAgentTools: AgentToolDecl[] = [
  {
    name: 'terminal.list',
    description: 'List the open terminals (id and whether active).',
    sideEffect: false,
    handler: () => ({ terminals: listTerminals() }),
  },
  {
    name: 'terminal.read',
    description: 'Read the recent output (scrollback) of a terminal by id.',
    params: {
      type: 'object',
      properties: { id: { type: 'string', description: 'Terminal id from terminal.list' } },
      required: ['id'],
    },
    sideEffect: false,
    handler: (args) => {
      const handle = getTerminal(String(args.id));
      return handle ? { id: handle.id, output: handle.read() } : { error: 'no such terminal' };
    },
  },
  {
    name: 'terminal.exec',
    description:
      'Run a shell command in a terminal (always visible). Reuses the terminal `id` if given, otherwise opens a new one. Read its output afterwards with terminal.read.',
    params: {
      type: 'object',
      properties: {
        command: { type: 'string', description: 'The shell command to run' },
        id: { type: 'string', description: 'Existing terminal id; omit to open a new terminal' },
      },
      required: ['command'],
    },
    sideEffect: true,
    specifierTemplate: '{command}',
    handler: (args) => {
      const command = String(args.command ?? '');
      const existing = args.id ? getTerminal(String(args.id)) : null;
      if (existing) {
        existing.write(`${command}\r`);
        return { ok: true, id: existing.id };
      }
      runCommand(command);
      return { ok: true, opened: true };
    },
  },
];
