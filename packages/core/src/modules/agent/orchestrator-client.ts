/**
 * Frontend half of the agent orchestrator. The backend brain runs the
 * tool-calling loop and relays layout tool calls over the `agent` WS channel;
 * this executes them against the registry and replies with results. See
 * docs/modules/agent-chat.md and backend/modules/agent/orchestrator.py.
 */
import { readActiveAgentContext } from '../../agent-context';
import { sendChannel, subscribeChannel } from '../../ws';
import { executeTool, paneTitle } from './tool-exec';

export interface AgentCallbacks {
  /** The model's final natural-language reply for the turn (authoritative). */
  onAnswer?: (text: string) => void;
  /** A streamed answer-content delta (append as it arrives). */
  onToken?: (delta: string) => void;
  /** A streamed reasoning/thinking delta (`reasoning_content`/`thinking`). */
  onReasoning?: (delta: string) => void;
  /** A human-readable note for each mutating tool the agent ran. */
  onAction?: (text: string) => void;
  onError?: (message: string) => void;
}

/** A prior conversation turn replayed to the backend so a turn has context. */
export interface AgentTurn {
  role: 'user' | 'assistant';
  content: string;
}

/** One entry of the agent's live tool catalog (for the `/tools` slash command). */
export interface AgentToolInfo {
  name: string;
  description: string;
  /** `layout` = built-in app verb; `widget` = a manifest-pushed widget/command tool. */
  source: 'layout' | 'widget';
}

/** Ask the backend for the exact tool catalog the model sees (static layout verbs
 * plus this connection's pushed manifest). Resolves on the `tools` reply. */
export function requestAgentTools(timeoutMs = 5000): Promise<AgentToolInfo[]> {
  return new Promise<AgentToolInfo[]>((resolve) => {
    let done = false;
    const finish = (tools: AgentToolInfo[]) => {
      if (done) return;
      done = true;
      unsub();
      resolve(tools);
    };
    const unsub = subscribeChannel('agent', (msg) => {
      if (msg.event !== 'tools') return;
      const data = (msg.data ?? {}) as { tools?: AgentToolInfo[] };
      finish(Array.isArray(data.tools) ? data.tools : []);
    });
    sendChannel('agent', 'list_tools', {});
    setTimeout(() => finish([]), timeoutMs);
  });
}

/** A short log line for mutating tools; read-only `list_*` tools stay silent. */
function describe(name: string, args: Record<string, unknown>): string | null {
  switch (name) {
    case 'open_pane':
      return `Opened ${paneTitle(String(args.id))}`;
    case 'close_pane':
      return `Closed ${paneTitle(String(args.id))}`;
    case 'create_workspace':
      return `Created workspace “${String(args.name)}”`;
    case 'switch_workspace':
      return 'Switched workspace';
    default:
      return null;
  }
}

/** Run one agent turn over the WS `agent` channel; resolves when the turn ends.
 * `history` replays prior user/assistant turns so the conversation is multi-turn. */
export function askAgent(prompt: string, cb: AgentCallbacks, history?: AgentTurn[]): Promise<void> {
  return new Promise<void>((resolve) => {
    const turnId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const unsub = subscribeChannel('agent', (msg) => {
      const data = (msg.data ?? {}) as Record<string, unknown>;
      if (data.turnId !== turnId) return;
      void (async () => {
        switch (msg.event) {
          case 'tool_call': {
            const name = String(data.name);
            const args = (data.args ?? {}) as Record<string, unknown>;
            const note = describe(name, args);
            if (note) cb.onAction?.(note);
            let ok = true;
            let result: unknown;
            let error: string | undefined;
            try {
              result = await executeTool(name, args);
            } catch (e) {
              ok = false;
              error = String(e);
            }
            sendChannel('agent', 'tool_result', { turnId, callId: data.callId, ok, result, error });
            break;
          }
          case 'token':
            cb.onToken?.(String(data.delta ?? ''));
            break;
          case 'reasoning':
            cb.onReasoning?.(String(data.delta ?? ''));
            break;
          case 'answer':
            cb.onAnswer?.(String(data.text ?? ''));
            break;
          case 'error':
            cb.onError?.(String(data.message ?? 'agent error'));
            unsub();
            resolve();
            break;
          case 'done':
            unsub();
            resolve();
            break;
        }
      })();
    });
    // Attach the focused editor buffer (if any) so the backend can give the model
    // the open code up front — it can then alter it without a discovery round-trip.
    const context = readActiveAgentContext();
    sendChannel('agent', 'ask', { turnId, prompt, history: history ?? [], context });
  });
}
