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

const sends: string[] = [];
const sendListeners = new Set<() => void>();

/**
 * Open the chat and **send** `prompt` as the next turn — for a click whose label
 * already says the agent will act ("Approve and write", "Ask"), on substance the
 * person typed or approved. Anything less explicit should use `draftInChat`.
 *
 * The turn is an ordinary chat turn: it shows in the transcript, streams, can be
 * stopped, and passes the same permission prompts as a typed one. It waits until a
 * chat pane is mounted, its agent is ready and no turn is running; the user's own
 * unsent draft in the box is left alone.
 */
export function sendInChat(prompt: string): void {
  sends.push(prompt);
  registry.openPanel('agent.chat');
  sendListeners.forEach((l) => l());
}

/** ChatWidget subscribes to learn that a send is waiting. */
export function onSendInChat(listener: () => void): () => void {
  sendListeners.add(listener);
  return () => {
    sendListeners.delete(listener);
  };
}

/** The oldest waiting send, taken (so two chat panes never both send it). */
export function claimChatSend(): string | null {
  return sends.shift() ?? null;
}

export function hasChatSend(): boolean {
  return sends.length > 0;
}
