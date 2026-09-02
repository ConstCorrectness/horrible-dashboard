/**
 * Setting key for "first-run setup has been seen".
 *
 * A setting, not localStorage: it is a fact about this node, so opening the app
 * in a second browser should not restart the wizard. (The greeting *name* is
 * localStorage, deliberately — see home/constants.ts.)
 */
export const OOBE_COMPLETE_KEY = 'desktop.oobeComplete';

/**
 * Setting key for the paradigm a **new** desktop is made with.
 *
 * The Start menu used to offer "New tiled desktop" and "New floating desktop" as
 * two rows, which put a paradigm choice in a launcher: the menu is where you go
 * to open something, and it already listed every desktop plus four management
 * verbs. The choice is a preference, so it lives on the settings page and the
 * launcher has one "New desktop" row that obeys it.
 */
export const DEFAULT_DESKTOP_MODE_KEY = 'desktop.defaultMode';
