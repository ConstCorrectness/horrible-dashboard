/**
 * Bridge for opening the agent chat on a **specific** session — used by the git
 * provenance pane to jump from a commit to the conversation that authored it. It opens
 * the chat pane and asks the mounted `ChatWidget` to switch to the session; if the
 * widget isn't up yet, it claims the pending id on mount (drain-on-mount, like the
 * companion-reveal bus). This keeps the git module off the ChatWidget's internals — it
 * calls only this public helper.
 */
import { registry } from '../../registry';

let pending: string | null = null;
const listeners = new Set<(id: string) => void>();

/** Open the chat pane and switch it to session `id`. */
export function openChatSession(id: string): void {
  pending = id;
  registry.openPanel('agent.chat');
  listeners.forEach((l) => l(id));
}

/** ChatWidget subscribes to switch when a request arrives while it's mounted. */
export function onOpenChatSession(listener: (id: string) => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** ChatWidget claims a request buffered before it mounted (once). */
export function claimPendingChatSession(): string | null {
  const id = pending;
  pending = null;
  return id;
}
