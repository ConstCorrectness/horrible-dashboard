/**
 * The agent chat's transcript, kept **outside** the React component that draws it.
 *
 * This is the fix for "the chat resets when I prompt". The pane's transcript,
 * its in-flight turn and its draft used to be `useState` inside `ChatWidget`,
 * which is only safe if the component never unmounts — and it unmounts routinely:
 * a workspace switch tears down every pane, an inactive tab is unmounted rather
 * than hidden, and the agent's *own* layout tools (`open_pane`, `split_area`)
 * restructure the tree the chat is rendered in. So the most reliable way to lose
 * a conversation was to ask the agent to open something, which is the one thing
 * this agent is for.
 *
 * Two consequences follow, and both are the point:
 *
 * 1. **A remount is invisible.** The pane re-reads the store and draws the same
 *    transcript, at the same scroll, with the same draft in the box — no refetch,
 *    no flash of the starter prompts, no forked session.
 * 2. **A turn survives its pane.** `askAgent`'s callbacks write here, not into a
 *    dead component's setState, so tokens streamed while the pane was unmounted
 *    are there when it comes back. Nothing is silently dropped on the floor.
 *
 * Keyed by agent id because each roster agent keeps its own sessions. State is
 * per app session and deliberately not persisted: the node is the durable store
 * (see sessions.ts), this is only what makes the *pane* stateless.
 */
import { useSyncExternalStore } from 'react';

import type { ChatSessionMeta } from './sessions';

export interface ChatTurn {
  role: 'user' | 'assistant' | 'system';
  text: string;
  /** Streamed reasoning/thinking for an assistant turn (`reasoning_content`). */
  reasoning?: string;
  /** Mutating tools the agent ran during an assistant turn. */
  actions?: string[];
  /** Slash-command echo/output: shown but not persisted or replayed to the model. */
  ephemeral?: boolean;
}

export interface AgentChatState {
  sessions: ChatSessionMeta[];
  activeId: string | null;
  turns: ChatTurn[];
  /** The unsent draft. Here too, so a remount mid-sentence keeps what was typed. */
  prompt: string;
  busy: boolean;
  /** Whether this agent's conversations could be read — see `loadSessions`. */
  restore: 'loading' | 'ok' | 'failed';
  /** Sessions have been read at least once for this agent, in this app session. */
  loaded: boolean;
}

const EMPTY: AgentChatState = {
  sessions: [],
  activeId: null,
  turns: [],
  prompt: '',
  busy: false,
  restore: 'loading',
  loaded: false,
};

const states = new Map<string, AgentChatState>();
const listeners = new Set<() => void>();

/** The live state for an agent. Stable reference between changes, so it is safe
 *  to hand straight to `useSyncExternalStore`. */
export function chatState(agentId: string): AgentChatState {
  return states.get(agentId) ?? EMPTY;
}

/** Merge a patch into an agent's state and notify. `patch` may be a function of
 *  the current state, for the read-modify-write cases (appending a turn). */
export function updateChat(
  agentId: string,
  patch: Partial<AgentChatState> | ((prev: AgentChatState) => Partial<AgentChatState>),
): void {
  const prev = chatState(agentId);
  const next = { ...prev, ...(typeof patch === 'function' ? patch(prev) : patch) };
  states.set(agentId, next);
  for (const l of listeners) l();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Reactive read of one agent's chat state. */
export function useAgentChat(agentId: string): AgentChatState {
  return useSyncExternalStore(
    subscribe,
    () => chatState(agentId),
    () => chatState(agentId),
  );
}

/** Test seam: forget everything. Not used by the app. */
export function resetChatStates(): void {
  states.clear();
  for (const l of listeners) l();
}
