/**
 * Agent tools the terminal exposes (declared on the `terminal.instance` panel).
 * `terminal.list`/`terminal.read` are ungated reads; `terminal.exec` is **gated**
 * and shell-matched by the backend (the engine's `SHELL_TOOLS` set + A4b specifier
 * logic), so a command's rule/mode decision is purely the permission engine's.
 * Execution is always visible in a real terminal. See docs/architecture/agent-tools.md.
 */
import type { AgentToolDecl } from '../../registry';
import { runCommand } from './index';
import { atPrompt } from './prompt';
import { getTerminal, listTerminals, type TerminalHandle } from './store';

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
      'Run a shell command in a terminal (always visible). Reuses the terminal `id` if given, otherwise opens a new one. Returns the terminal `id` and its recent `output` once the output settles (up to ~12s); `stillRunning: true` means output was still arriving — call terminal.read with that id for more. To run Python, put the code in a file and run `python <file>`; multi-line code typed into a shell runs line by line.',
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
    handler: async (args) => {
      const command = String(args.command ?? '');
      const existing = args.id ? getTerminal(String(args.id)) : null;
      if (args.id && !existing) {
        return {
          ok: false,
          error: `no such terminal: ${String(args.id)}`,
          terminals: listTerminals(),
        };
      }
      let handle: TerminalHandle;
      if (existing) {
        handle = existing;
        handle.write(`${command}\r`);
      } else {
        // A new terminal used to answer `{opened: true}` and nothing else — no id,
        // so the model guessed one ("1"), failed, listed, and only then could read.
        const before = new Set(listTerminals().map((t) => t.id));
        runCommand(command);
        const opened = await waitFor(
          () => listTerminals().find((t) => !before.has(t.id))?.id,
          OPEN_CAP_MS,
        );
        const found = opened ? getTerminal(opened) : null;
        if (!found) {
          return {
            ok: false,
            error: 'opened a terminal but it did not start; check terminal.list',
          };
        }
        handle = found;
        await handle.commandSent;
      }
      const sentAt = performance.now();
      const stillRunning = await settle(handle, sentAt);
      return {
        ok: true,
        id: handle.id,
        output: tail(handle.read()),
        // Not an exit status — a terminal has none to give. Just whether output was
        // still arriving when we stopped waiting; read again with terminal.read.
        ...(stillRunning ? { stillRunning: true } : {}),
      };
    },
  },
];

/** How long to wait for a newly opened terminal pane to mount and register. */
const OPEN_CAP_MS = 5000;
/** Stop waiting for output after this — well inside the backend's 30 s tool timeout. */
const OUTPUT_CAP_MS = 12_000;
/** Scrollback returned with the result, so a long session doesn't flood the model. */
const TAIL_CHARS = 4000;

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function waitFor<T>(probe: () => T | undefined, capMs: number): Promise<T | undefined> {
  const end = performance.now() + capMs;
  for (;;) {
    const v = probe();
    if (v !== undefined || performance.now() >= end) return v;
    await sleep(100);
  }
}

/** Wait for the command to finish. True when it was still going at the cap (a
 *  long-running or interactive program). */
async function settle(handle: TerminalHandle, sentAt: number): Promise<boolean> {
  const end = sentAt + OUTPUT_CAP_MS;
  for (;;) {
    await sleep(100);
    const now = performance.now();
    const last = handle.lastOutputAt();
    if (last > sentAt && atPrompt(handle.read(), now - last)) return false;
    if (now >= end) return true;
  }
}

function tail(text: string): string {
  return text.length > TAIL_CHARS ? `…${text.slice(-TAIL_CHARS)}` : text;
}
