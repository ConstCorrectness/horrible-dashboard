/**
 * Frontend half of one REPL session: a connection over the shared `/ws` `repl`
 * channel, correlated by the pane instance id. Code goes up; stdout/stderr/result
 * stream down. When the backend kernel's `dash.*` SDK makes a UI call, it arrives
 * here as a relayed `tool_call` — executed against the **same shared relay surface
 * the agent uses** (`executeTool`) and answered with a `tool_result`. The
 * interpreter lives on the backend (backend/modules/repl). See docs/modules/repl.md.
 */
import { sendChannel, subscribeChannel } from '../../ws';
import { executeTool } from '../agent/tool-exec';

export interface CellResult {
  ok: boolean;
  repr?: string | null;
  error?: string | null;
}

export interface ReplCallbacks {
  onStarted?: (banner: string) => void;
  onStdout?: (data: string) => void;
  onStderr?: (data: string) => void;
  onResult?: (result: CellResult) => void;
}

export class ReplSession {
  private unsub: () => void;

  constructor(
    readonly id: string,
    cb: ReplCallbacks,
  ) {
    this.unsub = subscribeChannel('repl', (msg) => {
      const data = (msg.data ?? {}) as Record<string, unknown>;
      if (data.id !== this.id) return;
      switch (msg.event) {
        case 'started':
          cb.onStarted?.(String(data.banner ?? ''));
          break;
        case 'stdout':
          cb.onStdout?.(String(data.data ?? ''));
          break;
        case 'stderr':
          cb.onStderr?.(String(data.data ?? ''));
          break;
        case 'result':
          cb.onResult?.({
            ok: Boolean(data.ok),
            repr: (data.repr ?? null) as string | null,
            error: (data.error ?? null) as string | null,
          });
          break;
        case 'tool_call':
          void this.runToolCall(data);
          break;
      }
    });
  }

  /** Execute a relayed `dash.*` call against the registry and reply with the result. */
  private async runToolCall(data: Record<string, unknown>): Promise<void> {
    const name = String(data.name);
    const args = (data.args ?? {}) as Record<string, unknown>;
    let ok = true;
    let result: unknown;
    let error: string | undefined;
    try {
      result = await executeTool(name, args);
    } catch (e) {
      ok = false;
      error = String(e);
    }
    sendChannel('repl', 'tool_result', { id: this.id, callId: data.callId, ok, result, error });
  }

  start(): void {
    sendChannel('repl', 'start', { id: this.id });
  }

  exec(code: string): void {
    sendChannel('repl', 'exec', { id: this.id, code });
  }

  dispose(): void {
    sendChannel('repl', 'close', { id: this.id });
    this.unsub();
  }
}
