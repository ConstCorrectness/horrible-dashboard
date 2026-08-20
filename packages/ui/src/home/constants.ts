import { getSetting, setSetting } from '@horrible/core';

/** Where the greeting name *used* to live: a per-browser localStorage key.
 * Still read once, as a migration source — see {@link getUserName}. */
export const NAME_KEY = 'horrible.userName';

/** Where the greeting name lives now. A *setting*, like `desktop.oobeComplete`
 * beside it: the name is a fact about this node, so it has to survive a reload,
 * a second browser and the desktop shell — a localStorage copy did none of that,
 * which is what made first-run setup ask for it again every time. Declared by the
 * shell module (AppShell), so it is also editable in settings and resettable
 * there like any other override. */
export const NAME_SETTING_KEY = 'shell.userName';

/** The greeting name, migrating the old localStorage value on first read.
 *
 * The migration is one-way and one-shot: once the setting exists it wins, and the
 * stale localStorage copy is dropped so the two can never disagree. */
export function getUserName(): string {
  const stored = getSetting<string>(NAME_SETTING_KEY);
  if (stored) return stored;
  const legacy = localStorage.getItem(NAME_KEY);
  if (legacy) {
    localStorage.removeItem(NAME_KEY);
    void setSetting(NAME_SETTING_KEY, legacy);
    return legacy;
  }
  return '';
}

/** Persist the greeting name (trimmed; blank clears it). */
export function setUserName(name: string): Promise<void> {
  return setSetting(NAME_SETTING_KEY, name.trim());
}

/** Setting key hiding the intro setup flow. A *setting*, not localStorage like the
 * name above: dismissing setup is a decision about this node, so it should follow
 * the user to their other browsers rather than reappearing on each one. Declared
 * by the shell module (AppShell) and reopened by `shell.setup`. */
export const SETUP_DISMISSED_KEY = 'home.setupDismissed';
