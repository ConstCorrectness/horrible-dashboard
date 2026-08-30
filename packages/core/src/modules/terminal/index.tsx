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
  /** A shell **id** from `GET /api/terminal/shells` — never a path. */
  shell?: string;
}

/** Open a new terminal, optionally rooted at `cwd` (e.g. file explorer's
 * "open terminal here") and running a specific shell. */
export function openTerminal(opts?: { cwd?: string; shell?: string }): void {
  registry.openPanel('terminal.instance', {
    params: { cwd: opts?.cwd, shell: opts?.shell },
  });
}

/** Open a new terminal and run `command` in it (visibly). */
export function runCommand(command: string, opts?: RunCommandOpts): void {
  registry.openPanel('terminal.instance', {
    params: { initialCommand: command, cwd: opts?.cwd, shell: opts?.shell },
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
      role: 'tool',
      editor: true,
      icon: '❯',
      // A guest sees that a terminal is open and where. Scrollback needs
      // the `terminal` grant and a content protocol, both of which land
      // with co-editing — declaring `mirror` now does not leak a byte of it.
      share: { mode: 'mirror' },
      defaultDock: 'bottom',
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
  // Scoped to the terminal pane: while a terminal is focused, mod+k clears it
  // (the iTerm/VS Code convention), shadowing the global palette shortcut; pressed
  // anywhere else, mod+k still opens the command palette. See core/keybindings.
  keybindings: [{ key: 'mod+k', command: 'terminal.clear', scope: 'terminal.instance' }],
  settings: [
    {
      key: 'terminal.fontFamily',
      title: 'Terminal font',
      description:
        'Monospace font for terminal panes. Falls back to the system monospace if the chosen font is not installed.',
      type: 'enum',
      enumValues: [
        'Monospace',
        'Cascadia Code',
        'Cascadia Mono',
        'Consolas',
        'Fira Code',
        'JetBrains Mono',
        'Menlo',
        'Courier New',
      ],
      default: 'Monospace',
    },
    {
      key: 'terminal.fontSize',
      title: 'Terminal font size',
      description: 'Font size, in pixels, for terminal panes.',
      type: 'number',
      default: 13,
    },
    {
      key: 'terminal.shell',
      title: 'Default shell',
      // A free-text id rather than an enum: the valid values are whatever this
      // machine has, which the backend discovers at runtime, and a settings enum
      // is a static declaration. The picker in the pane is the discoverable way
      // to set it; this is the standing default it writes.
      description:
        "Shell id for new terminals — e.g. `git-bash`, `pwsh`, `powershell`, `cmd`, `wsl:Ubuntu`, `zsh`. Blank means this machine's default. The terminal pane's shell button lists what is actually installed; an id that is not, falls back to the default and says so.",
      type: 'string',
      default: '',
    },
  ],
};
