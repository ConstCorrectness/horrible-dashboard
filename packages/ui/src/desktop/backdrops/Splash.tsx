/**
 * The pre-desktop home screen — avatar, ask bar, connector tiles — as a
 * backdrop.
 *
 * This is how `home` survives the desktop refactor rather than being deleted:
 * the surface people were used to landing on is still there, now with windows
 * floating over it. It is `interactive`, because its whole point is the ask bar.
 *
 * **It collapses.** A backdrop is not a window, so it has no title bar and the
 * taskbar has no button for it — which left the home screen as the one surface
 * in the app with no way to get it out of the way short of knowing that the
 * desktop's right-click menu can swap the backdrop entirely. Swapping is also
 * the wrong verb: it throws the ask bar away to hide the greeting. So the home
 * screen minimizes the way every other surface does, to a strip that keeps the
 * one control worth keeping.
 *
 * The collapsed flag lives in the **backdrop's own `params`**, which means it is
 * per-desktop and persisted with the rest of the layout: a desktop you use for
 * work stays collapsed, and one you land on stays open, without a global setting
 * that would make the two disagree.
 */
import { layoutStore, setBackdrop } from '@horrible/core';

import { HomeView } from '../../HomeView';

/** The id this backdrop is registered under. Named once; `setHomeCollapsed`
 * has to re-issue it and a second literal is a second thing to keep in step. */
export const SPLASH_BACKDROP_ID = 'splash';

/**
 * Collapse or restore the home screen on the active desktop.
 *
 * Merges into the existing params rather than replacing them: `setBackdrop`
 * takes a whole `BackdropRef`, so writing `{ collapsed }` alone would silently
 * drop anything else a future version of this backdrop stores there.
 */
export function setHomeCollapsed(collapsed: boolean): void {
  const current = layoutStore.getSnapshot().frame.backdrop;
  setBackdrop({
    id: SPLASH_BACKDROP_ID,
    params: { ...(current.id === SPLASH_BACKDROP_ID ? current.params : undefined), collapsed },
  });
}

/** Whether the active desktop's home screen is collapsed. False when the
 * desktop is showing some other backdrop entirely — there is nothing collapsed
 * about a home screen that is not on. */
export function isHomeCollapsed(): boolean {
  const current = layoutStore.getSnapshot().frame.backdrop;
  return current.id === SPLASH_BACKDROP_ID && current.params?.collapsed === true;
}

export function toggleHomeCollapsed(): void {
  setHomeCollapsed(!isHomeCollapsed());
}

export function SplashBackdrop({ params }: { params?: Record<string, unknown> }) {
  const collapsed = params?.collapsed === true;
  return (
    <div className="os-backdrop-splash" data-collapsed={collapsed ? 'true' : undefined}>
      <HomeView collapsed={collapsed} onCollapsedChange={setHomeCollapsed} />
    </div>
  );
}
