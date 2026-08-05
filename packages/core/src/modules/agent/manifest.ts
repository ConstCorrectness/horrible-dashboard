/**
 * The agent **capability manifest**: the frontend owns the dynamic tool catalog
 * (agent-exposed commands + per-widget/panel `agentTools`) and pushes it to the
 * backend orchestrator over the `/ws` `agent` channel. The backend merges it with
 * its static LAYOUT_TOOLS into the model's tool list each turn. Re-pushed on every
 * (re)connect and whenever the registry changes, since the backend holds the
 * manifest per connection and forgets it on disconnect.
 *
 * Handlers never cross the wire — only the serialized schema does. See
 * docs/architecture/agent-tools.md and backend/modules/agent/orchestrator.py.
 */
import type { AgentToolDecl, JSONSchema } from '@horribledashboard/sdk';

import { registry } from '../../registry';
import { onSocketOpen, sendChannel } from '../../ws';

/** What the backend receives per tool (the handler-free projection of a decl). */
export interface SerializedTool {
  name: string;
  description: string;
  params?: JSONSchema;
  sideEffect?: boolean;
  specifierTemplate?: string;
  kind: 'agentTool' | 'command';
}

/** Every `agentTools` entry across registered panels and widgets. */
function allAgentTools(): AgentToolDecl[] {
  return [...registry.panels, ...registry.widgets].flatMap((d) => d.agentTools ?? []);
}

/**
 * Tool names declared more than once across the registry, each with the count.
 *
 * A duplicate is never benign. `executeDynamicTool` resolves by `find`, so the
 * first registration wins and every later one is uncallable — silently, and with
 * the winner decided by module registration order rather than by anything a reader
 * of either module could see. Two `notebook.*` sets (the reactive notebook's and
 * the training notebook's) shipped this way: same names, different stores,
 * different session argument, and calls landing in whichever module `main.tsx`
 * happened to register first.
 */
export function duplicateToolNames(tools: ReadonlyArray<{ name: string }>): Map<string, number> {
  const counts = new Map<string, number>();
  for (const t of tools) counts.set(t.name, (counts.get(t.name) ?? 0) + 1);
  return new Map([...counts].filter(([, n]) => n > 1));
}

/** Build the manifest from the current registry. Handlers are dropped. */
export function serializeManifest(): SerializedTool[] {
  const tools: SerializedTool[] = allAgentTools().map((t) => ({
    name: t.name,
    description: t.description,
    params: t.params,
    sideEffect: t.sideEffect,
    specifierTemplate: t.specifierTemplate,
    kind: 'agentTool',
  }));
  for (const c of registry.commands) {
    if (!c.agent) continue;
    tools.push({
      name: c.id,
      description: c.agent.description,
      params: c.agent.params,
      sideEffect: c.agent.sideEffect,
      kind: 'command',
    });
  }
  // ERROR, not warn, and named: the failure mode is a tool that is simply never
  // reached, which from the outside is indistinguishable from a model choosing not
  // to call it. This is the only trace.
  for (const [name, count] of duplicateToolNames(tools)) {
    console.error(
      `agent manifest: tool "${name}" is declared ${count} times; ` +
        'only the first registration is callable. Namespace it by owning module.',
    );
  }
  return tools;
}

/**
 * Resolve a relayed dynamic tool call to its frontend handler. Returns the
 * handler's result, or `null` if `name` is not a known dynamic tool (the caller
 * falls back to the layout-tool switch). Agent-exposed commands run via the
 * registry; their args are ignored until parameterized-commands-as-tools lands.
 */
export async function executeDynamicTool(
  name: string,
  args: Record<string, unknown>,
): Promise<{ handled: boolean; result?: unknown }> {
  const tool = allAgentTools().find((t) => t.name === name);
  if (tool) return { handled: true, result: await tool.handler(args) };
  const command = registry.commands.find((c) => c.id === name && c.agent);
  if (command) {
    await registry.runCommand(name);
    return { handled: true, result: { ok: true } };
  }
  return { handled: false };
}

let started = false;

/** Begin pushing the manifest on (re)connect and on registry change. Idempotent. */
export function initAgentManifestSync(): void {
  if (started) return;
  started = true;
  const push = (): void => {
    sendChannel('agent', 'manifest', { tools: serializeManifest() });
  };
  onSocketOpen(push);
  registry.onChange(push);
}
