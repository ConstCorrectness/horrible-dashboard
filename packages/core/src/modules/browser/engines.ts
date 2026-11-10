/**
 * Which browser the agent drives right now.
 *
 * 1. **The native pane the human last used**, when one is open (desktop with the
 *    CDP bridge). Agent and human share one real page, which is what makes a
 *    browsing agent watchable and interruptible.
 * 2. **The backend's headless Chromium**, when the engine is enabled. This is the
 *    web build's browser and the desktop's fallback for reading pages nobody has
 *    open — invisible, but complete.
 * 3. Nothing — the tools then fall back to a server-side fetch (`browser.read`) or
 *    say what's missing.
 */
import { relayTurn } from '../../agent-turn';
import { engineStatus } from './api';
import { revealBrowserPane } from './dock';
import { markDriving, SERVER_PAGE } from './driving';
import { activeNativeTarget, nativeEngine, nativeTargetPane } from './native-engine';
import { engine as serverEngine, type BrowserEngine } from './session';

// The engine gate is process-wide and rarely flips within a session; cache the probe.
let serverProbe: Promise<boolean> | null = null;
export function serverEngineEnabled(): Promise<boolean> {
  if (!serverProbe) {
    serverProbe = engineStatus()
      .then((s) => s.enabled)
      .catch(() => false);
  }
  return serverProbe;
}

/**
 * The engine the agent's next browser action should use, or null if none. Called
 * from a relayed tool call, it also marks that page as driven by the calling turn
 * (browser/driving.ts), so the page can show it and offer a Stop. The turn is read
 * before the first `await`, which is the only place it is known (agent-turn.ts).
 */
export async function agentEngine(): Promise<BrowserEngine | null> {
  const turn = relayTurn();
  const native = activeNativeTarget();
  if (native) {
    markDriving(native, turn);
    return nativeEngine(native);
  }
  if (!(await serverEngineEnabled())) return null;
  markDriving(SERVER_PAGE, turn);
  return serverEngine;
}

/**
 * Bring the pane holding native webview `id` to the front (the agent is using it).
 * For a chat's docked browser that is the chat, with its browser strip open.
 */
export function revealNativeTarget(id: string): void {
  const paneId = nativeTargetPane(id);
  if (paneId) revealBrowserPane(paneId);
}
