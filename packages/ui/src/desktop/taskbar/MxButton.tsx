/**
 * The `M-x` zone: the desktop's answer to "where is the minibuffer?".
 *
 * The minibuffer is the frame's bottom row, so on a **floating** desktop — where
 * the frame is not mounted at all — it had no visible presence whatsoever. The
 * `alt+x` binding still worked and still does, but a keystroke nobody can see is
 * a keystroke nobody finds; this is the same discoverability argument the tray's
 * fullscreen button already makes.
 *
 * It carries the echo area too, because that is the other half of what the idle
 * minibuffer was for: a command that says something ("saved", "no match") must
 * say it somewhere, and on the desktop this is the only somewhere.
 */
import { useSyncExternalStore } from 'react';
import { minibuffer } from '@horrible/core';

export function MxButton({ showLabels }: { showLabels: boolean }) {
  const state = useSyncExternalStore(minibuffer.subscribe, minibuffer.getSnapshot);
  return (
    <div className="os-taskbar-mx">
      <button
        type="button"
        className="os-taskbar-mx-btn"
        title="Run a command (alt+x)"
        aria-label="Run a command"
        onClick={() => minibuffer.open('/')}
      >
        M-x
      </button>
      {/* Labels off means a narrow taskbar, and an echo line is the first thing
          that should give up its space — the button is the part you need. */}
      {showLabels && state.echo && (
        <span
          className={`os-taskbar-echo${state.echo.tone === 'error' ? ' is-error' : ''}`}
          title={state.echo.text}
        >
          {state.echo.text}
        </span>
      )}
    </div>
  );
}
