/**
 * The pre-desktop home screen — avatar, ask bar, connector tiles — as a
 * backdrop, painted over a wallpaper of its own.
 *
 * This is how `home` survives the desktop refactor rather than being deleted:
 * the surface people were used to landing on is still there, now with windows
 * floating over it. It is `interactive`, because its whole point is the ask bar.
 *
 * **It collapses**, and collapsing it is the whole floating-desktop transition.
 * A backdrop is not a window, so it has no title bar and the taskbar has no
 * button for it — which left the home screen as the one surface in the app with
 * no way to get it out of the way short of knowing that the desktop's
 * right-click menu can swap the backdrop entirely. Swapping is also the wrong
 * verb: it throws the ask bar away to hide the greeting. So the home screen
 * minimizes the way every other surface does, to a strip that keeps the one
 * control worth keeping.
 *
 * **It carries an underlay.** The desktop has one backdrop slot and the home
 * screen is in it, so until now collapsing revealed a flat theme-coloured void
 * rather than a desktop — the destination of the transition did not exist.
 * `resolveHomeUnderlay` picks a second, decorative backdrop to paint behind
 * `HomeView`, stored per desktop in splash's own params, so collapsing lands on a
 * genuine floating desktop: wallpaper, taskbar, ask-bar strip. The underlay is
 * painted in **both** states, not swapped in on collapse — the landing screen has
 * always been translucent over whatever is behind it, and a wallpaper that
 * appeared only once you minimized would read as a rendering glitch.
 *
 * The collapsed flag and the underlay id both live in the **backdrop's own
 * `params`**, which means they are per-desktop and persisted with the rest of the
 * layout: a desktop you use for work stays collapsed, and one you land on stays
 * open, without a global setting that would make the two disagree. The model side
 * is `core/layout/home-surface.ts`.
 */
import { HOME_BACKDROP_ID, registry, resolveHomeUnderlay, setHomeCollapsed } from '@horrible/core';
import { useCallback, useSyncExternalStore } from 'react';

import { HomeView } from '../../HomeView';

/** The id this backdrop is registered under. Re-exported from core, where the
 *  params model needs to name it too, rather than declared a second time. */
export const SPLASH_BACKDROP_ID = HOME_BACKDROP_ID;

export {
  homeUnderlayId,
  isHomeCollapsed,
  setHomeCollapsed,
  setHomeUnderlay,
  toggleHomeCollapsed,
} from '@horrible/core';

/**
 * The wallpaper behind the home screen, resolved live.
 *
 * Subscribed to `registry.onChange` for the same reason `Desktop` is: a plugin
 * that registers a backdrop after boot should be usable here without a reload.
 * The `usable` predicate is what keeps the recursion and pointer-fight cases out
 * — see `resolveHomeUnderlay`, which owns that rule so it can be tested.
 */
function useUnderlay(params?: Record<string, unknown>) {
  const subscribe = useCallback((listener: () => void) => registry.onChange(listener), []);
  const id = useSyncExternalStore(
    subscribe,
    useCallback(
      () =>
        resolveHomeUnderlay(params, (candidate) => {
          const decl = registry.backdrop(candidate);
          return !!decl && !decl.interactive;
        }),
      [params],
    ),
  );
  return id ? registry.backdrop(id)?.component : undefined;
}

export function SplashBackdrop({ params }: { params?: Record<string, unknown> }) {
  const collapsed = params?.collapsed === true;
  const Under = useUnderlay(params);
  return (
    <div className="os-backdrop-splash" data-collapsed={collapsed ? 'true' : undefined}>
      {/* Decoration, and strictly behind: `aria-hidden` and `pointer-events:
          none` in CSS, so the home screen's own controls — and, once collapsed,
          the desktop's right-click menu — reach the surface unimpeded. The
          desktop's params are passed straight through, which is what lets the
          `image` provider read the `url`/`fit`/`dim` the wallpaper picker
          already writes there. */}
      {Under && (
        <div className="os-backdrop-splash-under" aria-hidden="true">
          <Under params={params} />
        </div>
      )}
      <HomeView collapsed={collapsed} onCollapsedChange={setHomeCollapsed} />
    </div>
  );
}
