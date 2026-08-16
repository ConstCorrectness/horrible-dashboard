/**
 * The desktop surface: the backdrop, and the right-click menu over it.
 *
 * It renders nothing of the windows themselves — `WindowLayer` is a sibling that
 * floats over every shell view, so windows survive a trip to the tiling frame or
 * back. What lives here is only what belongs *behind* them.
 *
 * See docs/architecture/desktop-shell.mdx.
 */
import { useCallback, useSyncExternalStore } from 'react';
import { layoutStore, openContextMenu, registry, type BackdropDecl } from '@horrible/core';

import { DEFAULT_BACKDROP_ID } from './backdrops';

/**
 * Re-render when the module set changes, so a plugin that registers a backdrop
 * after boot is picked up. Cheap: `registry.onChange` fires only on registration.
 */
function useBackdrop(id: string): BackdropDecl | undefined {
  const subscribe = useCallback((listener: () => void) => registry.onChange(listener), []);
  const getSnapshot = useCallback(() => registry.backdrop(id), [id]);
  return useSyncExternalStore(subscribe, getSnapshot);
}

export function Desktop() {
  const { frame } = useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot);
  const requested = frame.backdrop.id;
  // A desktop saved against a backdrop whose plugin is gone falls back rather
  // than rendering blank — losing a wallpaper is not a reason to look broken.
  const decl = useBackdrop(requested);
  const fallback = useBackdrop(DEFAULT_BACKDROP_ID);
  const active = decl ?? fallback;
  const Body = active?.component;

  const onContextMenu = useCallback((e: React.MouseEvent) => {
    // Empty desktop only. A right-click that bubbled out of an interactive
    // backdrop's content — a board widget, the ask bar — belongs to that
    // content, and swallowing it would take away the browser's own menu on a
    // text field for no gain.
    const target = e.target as HTMLElement;
    if (target !== e.currentTarget && !target.classList.contains('os-desktop-surface')) return;
    if (openContextMenu(e, { kind: 'desktop' })) e.preventDefault();
  }, []);

  return (
    <div className="os-desktop" onContextMenu={onContextMenu}>
      {/* `pointer-events: none` in CSS unless the provider declared itself
          interactive, so a decorative backdrop never eats a click. */}
      <div className={`os-desktop-backdrop${active?.interactive ? ' is-interactive' : ''}`}>
        {Body ? <Body params={frame.backdrop.params} /> : null}
      </div>
      {/* Over a decorative backdrop, this collects the clicks the backdrop is
          declining, so the desktop menu works across the whole surface. Skipped
          for an interactive one, which would otherwise be sealed behind it. */}
      {!active?.interactive && <div className="os-desktop-surface" />}
    </div>
  );
}
