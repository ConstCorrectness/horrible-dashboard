/**
 * Per-pane-instance agent context: a pane optionally exposes a JSON snapshot of
 * its current state/selection that the agent reads **on demand** (pull, not a
 * push bus). The host (the workspace) supplies each pane instance's id through
 * `PaneInstanceContext`; a pane component registers its provider with the
 * `useAgentContext` hook. The agent's `get_pane_context` read tool resolves a
 * provider by instance id and returns its snapshot. See
 * docs/architecture/agent-tools.md.
 *
 * Lives in core (not ui) so feature modules — which live in core — can import the
 * hook without a core→ui cycle; ui only supplies the instance id via the context.
 */
import { createContext, useContext, useEffect, useRef } from 'react';

import type { AgentContextSnapshot } from '@horribledashboard/sdk';

const providers = new Map<string, () => AgentContextSnapshot>();

/**
 * The live pane instance id for the subtree a pane renders into. The workspace
 * host sets this per pane; `null` outside any pane (so `useAgentContext` is a
 * no-op there).
 */
export const PaneInstanceContext = createContext<string | null>(null);

/** Read a pane instance's current snapshot, or `null` if it exposes none. */
export function readAgentContext(instanceId: string): AgentContextSnapshot | null {
  const provider = providers.get(instanceId);
  return provider ? provider() : null;
}

/** Whether a pane instance currently exposes agent context. */
export function hasAgentContext(instanceId: string): boolean {
  return providers.has(instanceId);
}

/**
 * Register the calling pane's agent-context provider for its instance. The latest
 * `provider` is always invoked (kept in a ref) without re-registering each
 * render; the registration is removed on unmount. No-op when rendered outside a
 * pane (no instance id in context).
 */
export const useAgentContext: import('@horribledashboard/sdk').UseAgentContext = (provider) => {
  const instanceId = useContext(PaneInstanceContext);
  const ref = useRef(provider);
  ref.current = provider;
  useEffect(() => {
    if (!instanceId) return;
    const get = (): AgentContextSnapshot => ref.current();
    providers.set(instanceId, get);
    return () => {
      // Only clear if still ours (a remount may have replaced it already).
      if (providers.get(instanceId) === get) providers.delete(instanceId);
    };
  }, [instanceId]);
};
