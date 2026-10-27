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

/**
 * A waiting send. `agentId` addresses it to one agent's chat: the
 * interpretability suite's Ask strip talks to the `interp` agent, and its "ask
 * about this" must not be answered by whichever chat pane happens to claim first.
 */
interface PendingSend {
  prompt: string;
  agentId?: string;
}

const sends: PendingSend[] = [];
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
export function sendInChat(
  prompt: string,
  opts: {
    /** Only a chat talking to this agent may send it. */
    agentId?: string;
    /** Open the Agent pane (the default). A caller with its own chat host opts out. */
    open?: boolean;
  } = {},
): void {
  sends.push({ prompt, agentId: opts.agentId });
  if (opts.open ?? true) registry.openPanel('agent.chat');
  sendListeners.forEach((l) => l());
}

/**
 * Whether a chat for `agentId` may take `send`. A chat pinned to one agent (the
 * suite's Ask strip) takes only sends addressed to it; a general chat takes
 * unaddressed ones as well as its own.
 */
function claimable(send: PendingSend, agentId: string | undefined, pinned: boolean): boolean {
  if (send.agentId) return send.agentId === agentId;
  return !pinned;
}

/** ChatWidget subscribes to learn that a send is waiting. */
export function onSendInChat(listener: () => void): () => void {
  sendListeners.add(listener);
  return () => {
    sendListeners.delete(listener);
  };
}

/** The oldest waiting send this chat may take, taken (so two chat panes never both
 *  send it). */
export function claimChatSend(agentId?: string, pinned = false): string | null {
  const index = sends.findIndex((s) => claimable(s, agentId, pinned));
  if (index < 0) return null;
  return sends.splice(index, 1)[0].prompt;
}

export function hasChatSend(agentId?: string, pinned = false): boolean {
  return sends.some((s) => claimable(s, agentId, pinned));
}
