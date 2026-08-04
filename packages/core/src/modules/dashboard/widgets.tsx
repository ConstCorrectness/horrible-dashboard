/**
 * `BackendStatusWidget` used to live here — a whole center pane for one line of
 * backend health. It is now `backendHealth` (core/health.ts), rendered as a dot
 * in the minibuffer status line.
 */
export function WelcomeWidget() {
  return (
    <div>
      <p>
        Welcome to <strong>horrible-dashboard</strong> — your one-stop app for everything.
      </p>
      <p>
        Press <kbd>Ctrl</kbd>+<kbd>K</kbd> for the command palette.
      </p>
    </div>
  );
}
