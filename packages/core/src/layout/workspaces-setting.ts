/**
 * Whether **workspaces** are on: the named, pre-arranged desktops (AI Research,
 * Training, Lab…), the tiling paradigm they bring, and every surface that switches
 * between them — the top tab strip, the home screen's launcher, the Start menu's
 * Desktops group and the `mod+1..9` keys.
 *
 * Off by default. The app is a floating desktop first: you log in to one desktop
 * and open windows on it, and the seventeen presets are an opt-in way of working
 * rather than seventeen tabs a new user has to understand before doing anything.
 *
 * Its own file, not `persistence.ts`, because `WorkspaceLauncher` needs it and
 * importing persistence there would pull the whole layout engine into a component.
 * Declared in the `desktop` module's manifest.
 */
import { getSetting } from '../settings';

export const WORKSPACES_ENABLED_KEY = 'desktop.workspaces';

/** Read at call time, so a toggle on the settings page takes effect without a reload. */
export function workspacesEnabled(): boolean {
  return getSetting<boolean>(WORKSPACES_ENABLED_KEY) === true;
}
