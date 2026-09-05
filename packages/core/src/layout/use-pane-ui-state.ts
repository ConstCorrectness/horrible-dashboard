/**
 * View state that belongs to a **pane**, not to a mounted component.
 *
 * The companion to `pane-lifetime`, one level down. That module keeps expensive
 * resources alive across an unmount — a PTY, a browser engine, a kernel. This keeps
 * the cheap things beside them: where the pane was scrolled to, which cell was being
 * edited, where the cursor sat. Nobody loses work when those are dropped, but a
 * notebook that jumps back to the top every time you glance at another tab is a
 * notebook you stop using tabs with, and the fix is small enough that there is no
 * reason for it to be per-pane bespoke.
 *
 * It lives in `pane-lifetime` (`paneUiBag`) rather than in a store of its own,
 * because that module is the one place that already knows when a pane genuinely
 * closed, moved workspace or was reset away. A parallel store would have to mirror
 * every one of those paths, and would silently keep whichever one it missed.
 *
 * Deliberately **not** persisted to the layout blob. `serialize.ts` carries what the
 * user arranged; a scroll offset is where they happened to be looking, and writing it
 * would dirty the workspace (and drive the 600ms autosave) on every wheel tick.
 */
import { useCallback, useContext, useEffect, useRef, useState } from 'react';

import { PaneInstanceContext } from '../agent-context';
import { paneSessionKey, paneUiBag } from './pane-lifetime';
import { layoutStore } from './store';

type Bag = Map<string, unknown>;

/** This pane's bag, or null outside a pane — matching `usePaneSession`. */
function bagFor(instanceId: string | null): Bag | null {
  if (!instanceId) return null;
  return paneUiBag(paneSessionKey(layoutStore.getSnapshot().workspaceId, instanceId));
}

/**
 * A piece of this pane's view state, seeded from whatever the last mount left.
 *
 * Shaped like `useState` because it replaces one: the setter both re-renders and
 * writes through, so there is no way to update the value and forget to remember it.
 * `initial` is only consulted the first time the pane is ever mounted.
 */
export function usePaneUiState<T>(key: string, initial: T): [T, (value: T) => void] {
  const instanceId = useContext(PaneInstanceContext);
  const bag = bagFor(instanceId);
  const [value, setValue] = useState<T>(() => (bag?.has(key) ? (bag.get(key) as T) : initial));
  const set = useCallback(
    (next: T) => {
      setValue(next);
      bagFor(instanceId)?.set(key, next);
    },
    [instanceId, key],
  );
  return [value, set];
}

/**
 * Keep a scroll container's position across unmounts.
 *
 * Restored in a layout effect rather than on `useState` init: the element has no
 * scrollable content until its children have laid out, so an earlier write is
 * silently clamped to 0 and the pane still jumps to the top.
 *
 * Saved on every scroll rather than in a cleanup, because a workspace switch tears
 * the tree down and a cleanup that reads `el.scrollTop` can find the element already
 * detached — where it reads 0, which is exactly the value that looks like success.
 */
export function usePaneScroll<E extends HTMLElement>(key = 'scroll') {
  const instanceId = useContext(PaneInstanceContext);
  const ref = useRef<E | null>(null);
  const restored = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const bag = bagFor(instanceId);
    const saved = bag?.get(key);
    if (!restored.current && typeof saved === 'number') {
      restored.current = true;
      el.scrollTop = saved;
    }
    const onScroll = () => bagFor(instanceId)?.set(key, el.scrollTop);
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  });

  return ref;
}
