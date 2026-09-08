/**
 * The home surface's two states, and the wallpaper that is always behind it.
 *
 * The desktop has exactly **one** backdrop slot, and `splash` — the home screen:
 * avatar, ask bar, connector tiles, workspace launcher — occupies it by default.
 * That made "put the home screen away and use the desktop" impossible to express
 * honestly. Collapsing it left a flat theme-coloured void, because the wallpaper
 * that would have been there is exactly the thing splash displaced; and swapping
 * the backdrop to a wallpaper is the wrong verb, since it throws the ask bar away
 * in order to hide the greeting.
 *
 * So the home surface carries its **own underlay**: a second, decorative backdrop
 * painted behind it, stored in splash's own params. Collapsing the home screen
 * then reveals a real floating desktop — wallpaper, taskbar, and the ask bar
 * docked at the bottom — and expanding it brings the landing screen back over the
 * top of the same wallpaper. Nothing is swapped and nothing is lost.
 *
 * The state lives in the **backdrop's params**, not in a setting, for the reason
 * mode does: it is a property of one desktop. A desktop you work in stays
 * collapsed and one you land on stays open, with no global key for the two to
 * disagree over. (It is also why this cannot be a setting even in principle:
 * `GET /api/settings` hands the whole bag to every plugin, and a per-desktop
 * value has nowhere in it to live.)
 *
 * This is core rather than `packages/ui`, where the splash component itself
 * lives, because it is layout-model logic — the shape of a `BackdropRef`'s params
 * — and because `packages/ui` has no test runner. The renderer imports it.
 */
import { layoutStore } from './store';
import { setBackdrop } from './controller';

/**
 * The id the home surface is registered under.
 *
 * `DEFAULT_BACKDROP` happens to equal it, but the two are different facts — one
 * is "which provider draws the home screen", the other "what a desktop shows
 * when never told otherwise" — so this is not an alias for it. See `types.ts`.
 */
export const HOME_BACKDROP_ID = 'splash';

/**
 * The wallpaper behind the home screen on a desktop that has never chosen one.
 *
 * `aurora` was `DEFAULT_BACKDROP` until the home surface took the slot, so it is
 * the look this desktop already reads as, and it is drawn from the live theme
 * rather than bundling any imagery.
 */
export const DEFAULT_HOME_UNDERLAY = 'aurora';

/** The params `splash` stores. Loose at the boundary — this is a persisted blob
 *  and an older or newer build may have written something else. */
export interface HomeSurfaceParams {
  collapsed?: boolean;
  /** Backdrop id painted behind the home screen. `'none'` for a flat surface. */
  under?: string;
}

function backdropRef() {
  return layoutStore.getSnapshot().frame.backdrop;
}

/** The active desktop's splash params, or undefined when it shows something else. */
function homeParams(): Record<string, unknown> | undefined {
  const current = backdropRef();
  return current.id === HOME_BACKDROP_ID ? current.params : undefined;
}

/**
 * Write one field of the home surface's params.
 *
 * Merges rather than replaces: `setBackdrop` takes a whole `BackdropRef`, so
 * writing `{ collapsed }` alone would silently drop the underlay choice — and
 * writing `{ under }` alone would silently un-collapse the desktop.
 */
function patchHome(patch: HomeSurfaceParams): void {
  setBackdrop({ id: HOME_BACKDROP_ID, params: { ...homeParams(), ...patch } });
}

/** Whether the active desktop's home screen is collapsed. False when the desktop
 *  is showing some other backdrop entirely — there is nothing collapsed about a
 *  home screen that is not on. */
export function isHomeCollapsed(): boolean {
  return homeParams()?.collapsed === true;
}

/** Collapse or restore the home screen on the active desktop. */
export function setHomeCollapsed(collapsed: boolean): void {
  patchHome({ collapsed });
}

export function toggleHomeCollapsed(): void {
  setHomeCollapsed(!isHomeCollapsed());
}

/** Choose the wallpaper painted behind the home screen on the active desktop. */
export function setHomeUnderlay(under: string): void {
  patchHome({ under });
}

/** The underlay id stored for the active desktop, before it is resolved against
 *  what is actually registered. */
export function homeUnderlayId(): string {
  const stored = homeParams()?.under;
  return typeof stored === 'string' && stored ? stored : DEFAULT_HOME_UNDERLAY;
}

/**
 * Which backdrop should be painted behind the home screen, given what the desktop
 * stored and what the registry can offer.
 *
 * `usable` answers "is this id a registered, non-interactive backdrop". Two ids
 * are refused and both fail badly rather than visibly:
 *
 * - **The home surface itself.** `splash` under `splash` mounts `HomeView` inside
 *   `HomeView`, forever. A stored `under: 'splash'` is one bad menu wiring away,
 *   and the symptom would be a hung tab, not a wrong wallpaper.
 * - **Any other interactive backdrop.** `board` under the home screen puts two
 *   surfaces that both want the pointer in the same square of screen, with the
 *   lower one unreachable — it would look like the widgets had stopped working.
 *
 * An unregistered id (a plugin that is gone) falls back to the default the same
 * way `Desktop` falls back for the main slot: losing a wallpaper is not a reason
 * to look broken. Returns null only when the default is unavailable too, which is
 * the honest "paint nothing" answer.
 */
export function resolveHomeUnderlay(
  params: Record<string, unknown> | undefined,
  usable: (id: string) => boolean,
): string | null {
  const stored = params?.under;
  const wanted = typeof stored === 'string' && stored ? stored : DEFAULT_HOME_UNDERLAY;
  // 'none' is a registered provider that renders null. Honour it rather than
  // treating "the user asked for a flat surface" as an unset value and
  // overriding it with the default.
  if (wanted !== HOME_BACKDROP_ID && usable(wanted)) return wanted;
  if (DEFAULT_HOME_UNDERLAY !== wanted && usable(DEFAULT_HOME_UNDERLAY)) {
    return DEFAULT_HOME_UNDERLAY;
  }
  return null;
}
