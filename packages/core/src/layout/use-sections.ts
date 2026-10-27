/**
 * React access to a pane's **sections** — the in-pane tabs declared by
 * `SectionDecl`. The tab strip itself is host chrome (packages/ui renders it), so
 * this is what a pane component uses when it wants to switch bodies internally
 * rather than declare a `component`/`view` per section.
 *
 * Lives in core alongside `usePaneParams` and `useAgentContext`, for the same
 * reason: feature modules live in core and must not import ui.
 */
import { createContext, useCallback, useContext, useSyncExternalStore } from 'react';

import { PaneInstanceContext } from '../agent-context';
import { activeSectionOf, setPaneSection } from './controller';
import { findPaneAnywhere } from './model';
import { layoutStore } from './store';

/** The active section of an arbitrary pane instance, or undefined. */
export function sectionOfInstance(instanceId: string | null): string | undefined {
  if (!instanceId) return undefined;
  const pane = findPaneAnywhere(layoutStore.getSnapshot().frame, instanceId)?.pane;
  return pane ? activeSectionOf(pane) : undefined;
}

export interface PaneSections {
  /** The section this pane is showing, or undefined when it declares none. */
  section: string | undefined;
  /** Switch sections. Ignored for an id the view doesn't declare. */
  setSection: (section: string) => void;
}

/**
 * The calling pane's active section and a setter.
 *
 * Subscribed to the layout store rather than local state, so the tab strip, a
 * keybinding, `show("friends")` and the pane's own buttons all move one value —
 * and it persists with the layout. The module-level singletons this replaces
 * (games' `hub-section`) drifted from the strip and forgot on reload.
 */
export function usePaneSection(): PaneSections {
  const scoped = useContext(SectionScopeContext);
  const instanceId = useContext(PaneInstanceContext);
  const section = useSyncExternalStore(
    layoutStore.subscribe,
    () => sectionOfInstance(instanceId),
    () => undefined,
  );
  const setSection = useCallback(
    (next: string) => {
      if (instanceId) setPaneSection(instanceId, next);
    },
    [instanceId],
  );
  return scoped ?? { section, setSection };
}

/**
 * Sections one level down. A pane that switched its own sections internally
 * (`usePaneSection()`) can become a *section of another pane* without being
 * rewritten: the new host provides this context, and the component's sub-tabs
 * then read and move the host's sub-section value instead of the pane's own —
 * which is now the host's, and names the outer tab.
 */
export const SectionScopeContext = createContext<PaneSections | null>(null);
