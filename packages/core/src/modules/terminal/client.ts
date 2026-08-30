/**
 * Frontend half of one terminal: a session over the shared `/ws` `terminal`
 * channel, correlated by id (the pane instance id). Input/resize go up, output
 * streams down to the xterm instance. The PTY lives on the backend
 * (backend/modules/terminal). See docs/modules/terminal.md.
 */
import { sendChannel, subscribeChannel } from '../../ws';

export class TerminalSession {
  private unsub: () => void;

  constructor(
    readonly id: string,
    onOutput: (data: string) => void,
    onExit: () => void,
    onError?: (message: string) => void,
    /**
     * The shell the backend actually spawned. It reports a fallback rather than
     * hiding one, so a pane whose picker says "Git Bash" over a PowerShell prompt
     * is impossible — see `manager.py`.
     */
    onStarted?: (shell: string | null, requested: string | null) => void,
  ) {
    this.unsub = subscribeChannel('terminal', (msg) => {
      const data = (msg.data ?? {}) as {
        id?: string;
        data?: string;
        message?: string;
        shell?: string | null;
        requestedShell?: string | null;
      };
      if (data.id !== this.id) return;
      if (msg.event === 'output') onOutput(data.data ?? '');
      else if (msg.event === 'started') onStarted?.(data.shell ?? null, data.requestedShell ?? null);
      else if (msg.event === 'exit') onExit();
      // Spawn/IO failures (e.g. the shell isn't found on this host) — surface
      // them instead of leaving a blank, dead pane.
      else if (msg.event === 'error') onError?.(data.message ?? 'terminal error');
    });
  }

  /** `shell` is an **id** from `GET /api/terminal/shells`, never a path. */
  start(cols: number, rows: number, cwd?: string, shell?: string): void {
    sendChannel('terminal', 'start', { id: this.id, cols, rows, cwd, shell });
  }

  input(data: string): void {
    sendChannel('terminal', 'input', { id: this.id, data });
  }

  resize(cols: number, rows: number): void {
    sendChannel('terminal', 'resize', { id: this.id, cols, rows });
  }

  kill(): void {
    sendChannel('terminal', 'kill', { id: this.id });
  }

  dispose(): void {
    this.unsub();
  }
}
