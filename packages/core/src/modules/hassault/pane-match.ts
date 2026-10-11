/**
 * The hassault pane's match connection, owned by the **pane** rather than a mount.
 *
 * A pane unmounts on every tab switch, desktop switch and split — only an area's
 * active tab renders — and `HorribleAssaultPanel` used to `disconnect()` in its
 * mount cleanup, which sends `leave`. Glancing at another tab dropped you out of
 * the match and the lobby and hung up lobby voice; coming back put you on the main
 * menu while the room carried on without you. The session now survives every
 * unmount and is disconnected only when the pane really closes (see
 * `layout/pane-lifetime`), and a remount picks the match back up from it.
 *
 * Split from the panel so the lifetime rules are testable without importing a
 * three.js scene.
 */
import { lobbyVoice } from './lobby-voice';
import { MatchSession, type SessionState } from './session';

/** Hang lobby voice up and cut it off from the session it signalled through. */
export function detachLobbyVoice(session: MatchSession): void {
  lobbyVoice.stop();
  session.onLobbySignal = () => {};
  lobbyVoice.sendSignal = () => {};
  lobbyVoice.announce = () => {};
}

/** `create` for `usePaneSession`. */
export function createPaneMatch(): MatchSession {
  return new MatchSession();
}

/**
 * `dispose` for `usePaneSession`: the pane really closed. Everything a tab switch
 * keeps running stops here — the UI callbacks, then voice, then the session,
 * whose `disconnect` sends `leave` if we are in a match.
 */
export function disposePaneMatch(session: MatchSession): void {
  session.onChange = () => {};
  session.onLobbyStart = () => {};
  detachLobbyVoice(session);
  session.disconnect();
}

/**
 * The match a remount should land back in: one the session is in, or on its
 * way into. `null` for a fresh pane, or one whose session is idle.
 */
export function resumedMatch(session: MatchSession | null): SessionState | null {
  if (!session) return null;
  const { status } = session.state;
  return status === 'joined' || status === 'joining' ? session.state : null;
}
