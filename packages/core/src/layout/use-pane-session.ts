/**
 * React access to `pane-lifetime`'s pane-scoped resources.
 *
 * Split from the store so that stays React-free and headless-testable, the same
 * split `use-sections.ts` makes against the controller.
 */
import { useContext } from 'react';

import { PaneInstanceContext } from '../agent-context';
import { paneSession, paneSessionKey } from './pane-lifetime';
import { layoutStore } from './store';

/**
 * The calling pane's long-lived resource, created once and reused across every
 * unmount/remount. Returns `null` outside a pane (no instance id in context),
 * matching `useAgentContext`'s no-op behaviour there.
 *
 * `create` is called at most once per pane. Its `dispose` runs when the pane is
 * **closed** — not when this component unmounts, which is the whole point (see
 * `pane-lifetime`). Anything that must happen per *mount* (attaching to a DOM
 * node, observing a resize) belongs in a normal `useEffect` beside this call.
 *
 * The workspace id is read at call time, which is correct because a pane only
 * ever renders inside its own workspace.
 */
export function usePaneSession<T>(create: () => T, dispose: (value: T) => void): T | null {
  const key = usePaneSessionKey();
  if (!key) return null;
  return paneSession(key, create, dispose);
}

/**
 * This pane's session key, for resources that cannot be built during render.
 *
 * A terminal needs a live DOM node to `open()` into, which only exists once refs
 * are attached — so it calls `paneSession` itself inside its mount effect. The key
 * is the part that has to come from React.
 */
export function usePaneSessionKey(): string | null {
  const instanceId = useContext(PaneInstanceContext);
  if (!instanceId) return null;
  return paneSessionKey(layoutStore.getSnapshot().workspaceId, instanceId);
}
