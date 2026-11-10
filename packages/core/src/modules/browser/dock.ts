/**
 * The agent chat's **docked browser**: `browser.view` as a region strip of
 * `agent.chat` (declared on the chat, modules/agent/index.ts). It is the page the
 * agent reads and acts on, beside the conversation that asked for it, where the
 * person can watch and take over.
 *
 * A region view renders under `${chat}:${position}:browser.view`, a synthetic
 * instance id with its own pane session (closed with the chat; see
 * layout/pane-lifetime). Nothing about the browser panel changes inside one.
 *
 * `browser.open` hands the dock a URL through {@link sendToDock}. The panel takes it
 * and navigates as if the person had typed it, so the same path serves the native
 * webview, the headless engine and the iframe.
 */
import { focusInstance, setRegionView } from '../../layout/controller';
import { findPaneAnywhere, listPanes } from '../../layout/model';
import { regionHost } from '../../layout/pane-lifetime';
import { layoutStore } from '../../layout/store';

export const CHAT_VIEW = 'agent.chat';
export const BROWSER_VIEW = 'browser.view';

/** The chat whose dock the agent uses: the focused chat pane, else any open one. */
export function agentChatPane(): string | null {
  const { frame } = layoutStore.getSnapshot();
  const chats = listPanes(frame).filter((p) => p.pane.viewId === CHAT_VIEW);
  const focused = chats.find((p) => p.pane.instanceId === frame.focusedInstanceId);
  return (focused ?? chats[0])?.pane.instanceId ?? null;
}

/** Whether browser instance `paneKey` is a chat's dock (a region view). */
export function isDock(paneKey: string): boolean {
  return regionHost(paneKey) !== paneKey;
}

// --- URL hand-off -------------------------------------------------------------

/** URLs waiting for a dock to take them, by host (chat) instance id. */
const pending = new Map<string, string>();
const listeners = new Set<() => void>();

/**
 * Open `chat`'s browser strip and, given a `url`, point it there (null only opens
 * it: the headless engine's page is navigated by the engine, not the panel).
 * False when the chat declares no dock (the browser module is off, or the chat
 * pane is gone).
 */
export function sendToDock(chat: string, url: string | null): boolean {
  if (!setRegionView(chat, BROWSER_VIEW)) return false;
  if (url) {
    pending.set(chat, url);
    listeners.forEach((fn) => fn());
  }
  return true;
}

/** The URL waiting for dock `paneKey`, taken (so it is acted on once). */
export function takeDockRequest(paneKey: string): string | null {
  if (!isDock(paneKey)) return null;
  const host = regionHost(paneKey);
  const url = pending.get(host) ?? null;
  pending.delete(host);
  return url;
}

/** Hear about new dock requests; a mounted dock re-checks {@link takeDockRequest}. */
export function onDockRequest(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/**
 * Bring browser instance `paneKey` forward: its pane, or for a dock, its chat with
 * the browser strip open.
 */
export function revealBrowserPane(paneKey: string): void {
  const host = regionHost(paneKey);
  const located = findPaneAnywhere(layoutStore.getSnapshot().frame, host);
  if (!located) return;
  focusInstance(located);
  if (host !== paneKey) setRegionView(host, BROWSER_VIEW);
}

/** Test-only. */
export function resetDockForTests(): void {
  pending.clear();
  listeners.clear();
}
