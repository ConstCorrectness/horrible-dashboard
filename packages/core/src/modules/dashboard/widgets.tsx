import { bindingsFor } from '../../keymap/resolve';
import { labelSpec, tryParseSpec } from '../../keymap/spec';
import { useKeyContext, useKeymap } from '../../keymap/state';
import { WorkspaceLauncher } from '../../WorkspaceLauncher';

import './welcome.css';

/**
 * `BackendStatusWidget` used to live here — a whole center pane for one line of
 * backend health. It is now `backendHealth` (core/health.ts), rendered as a dot
 * in the minibuffer status line.
 */

/**
 * The live chord for a command, or `null` if it has none.
 *
 * Resolved rather than written down. This widget's previous text said "Press
 * Ctrl+K", which is wrong on a Mac, wrong for anyone who rebound it, and wrong for
 * anyone on a keymap preset that disables it — `presets.ts` ships one where
 * `mod+k` is `area.focus:up` and the palette moves. Advice from the app's own
 * welcome card that does not work is worse than no advice.
 */
function useChordLabel(command: string): string | null {
  const bindings = useKeymap();
  const ctx = useKeyContext();
  for (const binding of bindingsFor(command, bindings, ctx)) {
    const chord = tryParseSpec(binding.key);
    if (chord) return labelSpec(chord, { platform: ctx.platform });
  }
  return null;
}

/**
 * The welcome card: what this app is, and the ways to get anywhere in it.
 *
 * It used to be two sentences, one of which was "Press Ctrl+K for the command
 * palette" — a real thing to know, and also the *only* thing the app ever told
 * anyone about itself. There is no tour and no help menu, so this card and the
 * first-run wizard are the whole body of guidance, and the wizard covers a name, a
 * wallpaper and three credential steps.
 *
 * The workspaces do the heavy lifting, through the same `WorkspaceLauncher` the home
 * surface renders. Sharing the component is the point: the presets are this app's
 * table of contents, and a second hand-written list of them would be missing
 * whichever one was added last.
 */
export function WelcomeWidget() {
  const palette = useChordLabel('shell.commandPalette');
  return (
    <div className="welcome">
      <p className="welcome-lede">
        <strong>horrible-dashboard</strong> is one app for everything — a desktop of panes you
        arrange yourself, an agent that can arrange them for you, and the tools underneath.
      </p>
      <ul className="welcome-ways">
        <li>
          <b>Pick a workspace.</b> Each one below opens a complete arrangement for a kind of work.
          Switching costs nothing: every desktop keeps what you left in it.
        </li>
        <li>
          {/* Absent, not faked: if the palette has no binding on this keymap, saying
              so is the honest render — and it is a real state, not a hypothetical. */}
          <b>Search for a pane.</b>{' '}
          {palette ? (
            <>
              <kbd>{palette}</kbd> opens the command palette.
            </>
          ) : (
            <>The command palette has no shortcut on this keymap.</>
          )}{' '}
          Type <code>@</code> for panes, <code>&gt;</code> for commands; anything else asks the
          agent.
        </li>
        <li>
          <b>Or just ask.</b> The agent can open, close and rearrange panes, so &ldquo;show me the
          training metrics&rdquo; is a way to navigate.
        </li>
      </ul>
      <WorkspaceLauncher />
    </div>
  );
}
