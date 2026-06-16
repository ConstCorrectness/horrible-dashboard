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
  ) {
    this.unsub = subscribeChannel('terminal', (msg) => {
      const data = (msg.data ?? {}) as { id?: string; data?: string };
      if (data.id !== this.id) return;
      if (msg.event === 'output') onOutput(data.data ?? '');
      else if (msg.event === 'exit') onExit();
    });
  }

  start(cols: number, rows: number, cwd?: string): void {
    sendChannel('terminal', 'start', { id: this.id, cols, rows, cwd });
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
