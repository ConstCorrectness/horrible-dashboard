/**
 * Bridge for opening the agent chat on a **specific** session — used by the git
 * provenance pane to jump from a commit to the conversation that authored it. It opens
 * the chat pane and asks the mounted `ChatWidget` to switch to the session; if the
 * widget isn't up yet, it claims the pending id on mount (drain-on-mount, like the
 * companion-reveal bus). This keeps the git module off the ChatWidget's internals — it
 * calls only this public helper.
 */
import { agentForWorkspace } from '../../layout/persistence';
import { registry } from '../../registry';
import { workspaceStore } from '../../workspace-store';
import { updateChat } from './chat-state';

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

/**
 * Open the chat with `prompt` typed into the box, for the agent this workspace
 * talks to — **not sent**. A pane that offers "ask the agent about this" puts the
 * question where the user can read and edit it first; sending on their behalf
 * would spend a turn on wording they never saw.
 */
export function draftInChat(prompt: string): void {
  const agentId = agentForWorkspace(workspaceStore.getSnapshot().activeId);
  updateChat(agentId, { prompt });
  registry.openPanel('agent.chat');
}
