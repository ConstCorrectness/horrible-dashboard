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
 *
 * **Providers are keyed by pane instance *and section*.** A section body renders
 * inside its host's `PaneInstanceContext` (deliberately — that is what puts its
 * provider under a real, enumerable instance id rather than a synthetic one), so
 * a flat instance-id key made two providers on one pane silently overwrite each
 * other: last mount won, and nothing was restored when it unmounted. Splitting
 * the key lets a pane-level provider and the mounted section's provider coexist
 * and merge, which is what a merged pane needs — the host describes the pane, the
 * section describes the tab.
 */
import { createContext, useContext, useEffect, useRef } from 'react';

import type { AgentContextSnapshot } from '@horribledashboard/sdk';

type Provider = () => AgentContextSnapshot;

/**
 * One pane instance's registrations: the pane-level provider (if the host
 * component registers one) plus the section-scoped ones.
 *
 * `sections` holds at most one entry in practice — only the *mounted* section
 * body can register, and React runs the outgoing body's cleanup before the
 * incoming one's effect — but it is a map rather than a single slot so that a
 * switch mid-flight degrades to a stale read of the wrong tab instead of a lost
 * registration, and so a caller that knows the active section can ask for it.
 */
interface PaneProviders {
  pane?: Provider;
  sections: Map<string, Provider>;
}

const providers = new Map<string, PaneProviders>();

/**
 * The live pane instance id for the subtree a pane renders into. The workspace
 * host sets this per pane; `null` outside any pane (so `useAgentContext` is a
 * no-op there).
 */
export const PaneInstanceContext = createContext<string | null>(null);

/**
 * The section id a body is rendering as, or `null` for the pane's own component.
 *
 * `PaneHost` supplies this **only** around a section body it renders itself (one
 * that declared a `component`/`view`). A pane that switches sections internally
 * gets `null` here — its own registration is legitimately pane-level — and an
 * inner body that wants its own slot passes the section id to `useAgentContext`
 * explicitly. Providing it around the host component instead would put the host
 * and its inner bodies back on one key, which is the bug this replaces.
 */
export const SectionInstanceContext = createContext<string | null>(null);

/**
 * Read a pane instance's current snapshot, or `null` if it exposes none.
 *
 * Merges the pane-level snapshot with the mounted section's, section last: where
 * both describe the same key the more specific one wins. Pass `section` to read a
 * named one and ignore any other registration — callers that know which tab is
 * active should, so a provider that outlives its switch can't answer for the tab
 * that replaced it.
 */
export function readAgentContext(
  instanceId: string,
  section?: string,
): AgentContextSnapshot | null {
  const entry = providers.get(instanceId);
  if (!entry) return null;
  const parts: AgentContextSnapshot[] = [];
  if (entry.pane) parts.push(entry.pane());
  if (section === undefined) {
    for (const provider of entry.sections.values()) parts.push(provider());
  } else {
    const provider = entry.sections.get(section);
    if (provider) parts.push(provider());
  }
  if (!parts.length) return null;
  return Object.assign({}, ...parts) as AgentContextSnapshot;
}

/** Whether a pane instance currently exposes agent context (pane-level or section). */
export function hasAgentContext(instanceId: string): boolean {
  const entry = providers.get(instanceId);
  return !!entry && (!!entry.pane || entry.sections.size > 0);
}

/** The section ids of a pane instance that currently expose their own snapshot. */
export function sectionsWithAgentContext(instanceId: string): string[] {
  return [...(providers.get(instanceId)?.sections.keys() ?? [])];
}

// --- Ambient "active" context -------------------------------------------------
// The pane instance the user is actively working in (currently the focused editor
// buffer). A turn attaches this snapshot up front so the agent can act on what the
// user is looking at — e.g. alter the open code — without first discovering it via
// list_open_panes + get_pane_context. The on-demand pull above still covers every
// other pane; this is a convenience push for the focused one only.
let activeInstanceId: string | null = null;

/** Mark a pane instance as the one the user is actively working in. */
export function setActiveContextInstance(instanceId: string): void {
  activeInstanceId = instanceId;
}

/** Clear the active marker if it still points at `instanceId` (call on unmount). */
export function clearActiveContextInstance(instanceId: string): void {
  if (activeInstanceId === instanceId) activeInstanceId = null;
}

export interface ActiveAgentContext {
  instanceId: string;
  snapshot: AgentContextSnapshot;
}

/** The focused pane's live snapshot, or `null` when none is active/mounted. */
export function readActiveAgentContext(): ActiveAgentContext | null {
  if (!activeInstanceId) return null;
  const snapshot = readAgentContext(activeInstanceId);
  return snapshot ? { instanceId: activeInstanceId, snapshot } : null;
}

/**
 * Register the calling pane's agent-context provider for its instance. The latest
 * `provider` is always invoked (kept in a ref) without re-registering each
 * render; the registration is removed on unmount. No-op when rendered outside a
 * pane (no instance id in context).
 *
 * `section` defaults to the enclosing `SectionInstanceContext`, so a section body
 * the host renders needs no argument. Pass it explicitly from a pane that switches
 * sections internally — its bodies render under the host's own (null) section, and
 * without an id they would share the host's slot.
 */
export const useAgentContext: import('@horribledashboard/sdk').UseAgentContext = (
  provider,
  section,
) => {
  const instanceId = useContext(PaneInstanceContext);
  const ambientSection = useContext(SectionInstanceContext);
  const sectionId = section ?? ambientSection;
  const ref = useRef(provider);
  ref.current = provider;
  useEffect(() => {
    if (!instanceId) return;
    const get = (): AgentContextSnapshot => ref.current();
    let entry = providers.get(instanceId);
    if (!entry) {
      entry = { sections: new Map() };
      providers.set(instanceId, entry);
    }
    if (sectionId === null) entry.pane = get;
    else entry.sections.set(sectionId, get);
    return () => {
      const current = providers.get(instanceId);
      if (!current) return;
      // Only clear if still ours (a remount may have replaced it already).
      if (sectionId === null) {
        if (current.pane === get) delete current.pane;
      } else if (current.sections.get(sectionId) === get) {
        current.sections.delete(sectionId);
      }
      if (!current.pane && current.sections.size === 0) providers.delete(instanceId);
    };
  }, [instanceId, sectionId]);
};
