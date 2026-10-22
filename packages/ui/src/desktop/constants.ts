/**
 * Setting key for "first-run setup has been seen".
 *
 * A setting, not localStorage: it is a fact about this node, so opening the app
 * in a second browser should not restart the wizard. (The greeting *name* is
 * localStorage, deliberately — see home/constants.ts.)
 */
export const OOBE_COMPLETE_KEY = 'desktop.oobeComplete';
