/**
 * Terminal module: xterm.js panes over backend PTYs (panel `terminal.instance`,
 * bottom dock). Commands manage terminals; `runCommand` opens one pre-running a
 * command (visibly — never hidden execution). The agent's gated `terminal.exec`
 * tool is D3. See docs/modules/terminal.md.
 */
import { registry, type ModuleManifest } from '../../registry';
import { terminalAgentTools } from './agentTools';
import { TerminalPane } from './TerminalPane';
import { getActiveTerminal, siblingTerminal } from './store';

export interface RunCommandOpts {
  cwd?: string;
}

/** Open a new terminal, optionally rooted at `cwd` (e.g. file explorer's
 * "open terminal here"). */
export function openTerminal(opts?: { cwd?: string }): void {
  registry.openPanel('terminal.instance', { params: { cwd: opts?.cwd } });
}

/** Open a new terminal and run `command` in it (visibly). */
export function runCommand(command: string, opts?: RunCommandOpts): void {
  registry.openPanel('terminal.instance', {
    params: { initialCommand: command, cwd: opts?.cwd },
  });
}

function killActive(): void {
  const active = getActiveTerminal();
  if (active) registry.layoutController?.closePane(active.id);
}

function focusSibling(step: number): void {
  const next = siblingTerminal(step);
  if (next) {
    registry.layoutController?.focusPane(next.id);
    next.focus();
  }
}

export const terminalModule: ModuleManifest = {
  id: 'terminal',
  title: 'Terminal',
  panels: [
    {
      id: 'terminal.instance',
      title: 'Terminal',
      component: TerminalPane,
      defaultPlacement: 'bottom',
      agentTools: terminalAgentTools,
      // Not a singleton: each open is its own PTY.
    },
  ],
  commands: [
    {
      id: 'terminal.new',
      title: 'Terminal: New terminal',
      run: () => registry.openPanel('terminal.instance'),
    },
    {
      id: 'terminal.clear',
      title: 'Terminal: Clear active',
      run: () => getActiveTerminal()?.clear(),
    },
    { id: 'terminal.kill', title: 'Terminal: Kill active', run: killActive },
    { id: 'terminal.focusNext', title: 'Terminal: Focus next', run: () => focusSibling(1) },
    { id: 'terminal.focusPrev', title: 'Terminal: Focus previous', run: () => focusSibling(-1) },
  ],
};
