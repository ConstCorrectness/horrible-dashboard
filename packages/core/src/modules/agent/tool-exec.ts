/**
 * The shared **relay surface**: the catalogue of UI operations a backend tool call
 * can run against the registry + layout controller. Both the agent orchestrator
 * (orchestrator-client.ts) and the Python REPL (../repl/client.ts) relay tool
 * calls over the `/ws` socket and execute them here, so any verb one can run, the
 * other can too — one source of truth. See docs/modules/agent-chat.md and
 * docs/modules/repl.md.
 */
import { readAgentContext } from '../../agent-context';
import { registry, type PaneDirection, type SplitDirection } from '../../registry';
import { executeDynamicTool } from './manifest';

const SPLIT_DIRS: readonly SplitDirection[] = ['left', 'right', 'above', 'below'];
const MOVE_DIRS: readonly PaneDirection[] = ['left', 'right', 'above', 'below', 'within'];

function num(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined;
}

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
    case 'split_pane': {
      if (!lc) return { error: 'workspace not ready' };
      const direction = args.direction as SplitDirection;
      if (!SPLIT_DIRS.includes(direction))
        return { error: `direction must be one of ${SPLIT_DIRS.join(', ')}` };
      const newInstanceId = lc.splitPane(String(args.instanceId), direction, String(args.paneId));
      return newInstanceId === null
        ? { error: 'unknown pane instanceId or paneId' }
        : { ok: true, newInstanceId };
    }
    case 'resize_pane': {
      if (!lc) return { error: 'workspace not ready' };
      const ok = lc.resizePane(String(args.instanceId), {
        width: num(args.width),
        height: num(args.height),
      });
      return ok ? { ok } : { error: 'unknown pane instanceId' };
    }
    case 'move_pane': {
      if (!lc) return { error: 'workspace not ready' };
      const direction = args.direction as PaneDirection;
      if (!MOVE_DIRS.includes(direction))
        return { error: `direction must be one of ${MOVE_DIRS.join(', ')}` };
      const ok = lc.movePane(String(args.instanceId), String(args.reference), direction);
      return ok ? { ok } : { error: 'unknown pane instanceId or reference' };
    }
    case 'float_pane':
    case 'dock_pane': {
      if (!lc) return { error: 'workspace not ready' };
      const ok = lc.setPaneFloating(String(args.instanceId), name === 'float_pane');
      return ok ? { ok } : { error: 'pane already in that state, or unknown instanceId' };
    }
    case 'maximize_pane':
    case 'restore_pane': {
      if (!lc) return { error: 'workspace not ready' };
      const ok = lc.maximizePane(String(args.instanceId), name === 'maximize_pane');
      return ok ? { ok } : { error: 'unknown pane instanceId' };
    }
    default: {
      // Not a layout verb — try the dynamic tools the manifest advertised
      // (per-widget/panel agentTools and agent-exposed commands).
      const dynamic = await executeDynamicTool(name, args);
      if (dynamic.handled) return dynamic.result;
      return { error: `unknown tool: ${name}` };
    }
  }
}
