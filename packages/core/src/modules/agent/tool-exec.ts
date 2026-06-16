/**
 * The shared **relay surface**: the catalogue of UI operations a backend tool call
 * can run against the registry + layout controller. Both the agent orchestrator
 * (orchestrator-client.ts) and the Python REPL (../repl/client.ts) relay tool
 * calls over the `/ws` socket and execute them here, so any verb one can run, the
 * other can too — one source of truth. See docs/modules/agent-chat.md and
 * docs/modules/repl.md.
 */
import { readAgentContext } from '../../agent-context';
import { registry } from '../../registry';
import { executeDynamicTool } from './manifest';

/** Display title for a pane id (panel or widget), falling back to the id. */
export function paneTitle(id: string): string {
  const decl =
    registry.panels.find((p) => p.id === id) ?? registry.widgets.find((w) => w.id === id);
  return decl?.title ?? id;
}

/** Execute one relayed tool call against the registry/layout controller. */
export async function executeTool(name: string, args: Record<string, unknown>): Promise<unknown> {
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
