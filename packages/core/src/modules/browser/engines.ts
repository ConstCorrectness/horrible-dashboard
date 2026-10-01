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
import { layoutStore } from '../../layout/store';
import { findPaneAnywhere } from '../../layout/model';
import { focusInstance } from '../../layout/controller';
import { engineStatus } from './api';
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

/** The engine the agent's next browser action should use, or null if none. */
export async function agentEngine(): Promise<BrowserEngine | null> {
  const native = activeNativeTarget();
  if (native) return nativeEngine(native);
  return (await serverEngineEnabled()) ? serverEngine : null;
}

/** Bring the pane holding native webview `id` to the front (the agent is using it). */
export function revealNativeTarget(id: string): void {
  const paneId = nativeTargetPane(id);
  if (!paneId) return;
  const located = findPaneAnywhere(layoutStore.getSnapshot().frame, paneId);
  if (located) focusInstance(located);
}
