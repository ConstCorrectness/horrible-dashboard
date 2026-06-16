/**
 * Frontend half of the agent orchestrator. The backend brain runs the
 * tool-calling loop and relays layout tool calls over the `agent` WS channel;
 * this executes them against the registry and replies with results. See
 * docs/modules/agent-chat.md and backend/modules/agent/orchestrator.py.
 */
import { readAgentContext } from '../../agent-context';
import { registry } from '../../registry';
import { sendChannel, subscribeChannel } from '../../ws';
import { executeDynamicTool } from './manifest';

export interface AgentCallbacks {
  /** The model's final natural-language reply for the turn. */
  onAnswer?: (text: string) => void;
  /** A human-readable note for each mutating tool the agent ran. */
  onAction?: (text: string) => void;
  onError?: (message: string) => void;
}

function paneTitle(id: string): string {
  const decl =
    registry.panels.find((p) => p.id === id) ?? registry.widgets.find((w) => w.id === id);
  return decl?.title ?? id;
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

/** Execute one relayed tool call against the registry/layout controller. */
async function executeTool(name: string, args: Record<string, unknown>): Promise<unknown> {
  const lc = registry.layoutController;
  switch (name) {
    case 'list_available_panes':
      return {
        panels: registry.panels.map((p) => ({ id: p.id, title: p.title })),
        widgets: registry.widgets.map((w) => ({ id: w.id, title: w.title })),
      };
    case 'list_workspaces':
      return lc ? await lc.listWorkspaces() : { error: 'workspace not ready' };
    case 'list_open_panes':
      return lc ? { panes: lc.listOpenPanes() } : { error: 'workspace not ready' };
    case 'get_pane_context': {
      const snapshot = readAgentContext(String(args.instanceId));
      return snapshot === null
        ? { error: `no agent context for pane: ${String(args.instanceId)}` }
        : { context: snapshot };
    }
    case 'open_pane':
      registry.openPanel(String(args.id));
      return { ok: true, opened: args.id };
    case 'close_pane':
      return { closed: lc?.closePane(String(args.id)) ?? false };
    case 'create_workspace':
      return lc ? await lc.createWorkspace(String(args.name)) : { error: 'workspace not ready' };
    case 'switch_workspace':
      registry.switchWorkspace(String(args.id));
      return { ok: true, switched: args.id };
    default: {
      // Not a layout verb — try the dynamic tools the manifest advertised
      // (per-widget/panel agentTools and agent-exposed commands).
      const dynamic = await executeDynamicTool(name, args);
      if (dynamic.handled) return dynamic.result;
      return { error: `unknown tool: ${name}` };
    }
  }
}

/** Run one agent turn over the WS `agent` channel; resolves when the turn ends. */
export function askAgent(prompt: string, cb: AgentCallbacks): Promise<void> {
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
    sendChannel('agent', 'ask', { turnId, prompt });
  });
}
