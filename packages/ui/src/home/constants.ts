/** Where the greeting name is kept. Read by HomeView, written by OnboardingCard. */
export const NAME_KEY = 'horrible.userName';

/** Setting key hiding the intro setup flow. A *setting*, not localStorage like the
 * name above: dismissing setup is a decision about this node, so it should follow
 * the user to their other browsers rather than reappearing on each one. Declared
 * by the shell module (AppShell) and reopened by `shell.setup`. */
export const SETUP_DISMISSED_KEY = 'home.setupDismissed';
