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
  /** The orchestrator turn this belongs to (a user turn and its reply share one).
   *  The join key into interpretability and trajectories. */
  turnId?: string;
  /** Sub-agents this assistant turn delegated to, oldest first. `ok` is unset
   *  while the sub-turn is still running. */
  subTurns?: SubTurn[];
}

export interface SubTurn {
  turnId: string;
  agentId: string;
  ok?: boolean;
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

/**
 * Which chat the trace view follows: the agent chat pane used most recently, and
 * optionally one turn of it pinned in place.
 *
 * Set by the chat on focus and on send, and by its per-turn trace button (which
 * pins). Kept here rather than in the trajectories module: the chat only
 * lazy-loads the view that watches it (for its drawer), and the view reads this.
 */
export interface FollowTarget {
  agentId: string;
  /** A turn to hold on instead of advancing to the newest one. */
  pinnedTurnId: string | null;
}

let follow: FollowTarget | null = null;
const followListeners = new Set<() => void>();

export function followTarget(): FollowTarget | null {
  return follow;
}

/** Point the trace view at an agent's chat. Keeps an existing pin on the same
 *  agent unless `pinnedTurnId` is given (null unpins). */
export function setFollowTarget(agentId: string, pinnedTurnId?: string | null): void {
  const pinned =
    pinnedTurnId !== undefined
      ? pinnedTurnId
      : follow?.agentId === agentId
        ? follow.pinnedTurnId
        : null;
  if (follow?.agentId === agentId && follow.pinnedTurnId === pinned) return;
  follow = { agentId, pinnedTurnId: pinned };
  for (const l of followListeners) l();
}

function subscribeFollow(listener: () => void): () => void {
  followListeners.add(listener);
  return () => {
    followListeners.delete(listener);
  };
}

export function useFollowTarget(): FollowTarget | null {
  return useSyncExternalStore(subscribeFollow, followTarget, followTarget);
}

/**
 * Whether the chat pane's trace drawer is out. Module-level so the state survives
 * the pane remounting, and so the trajectories pane's "dock into chat" can open it
 * from outside. The drawer width is a per-viewer convenience kept in localStorage.
 */
const DRAWER_KEY = 'agent.traceDrawer';
let drawer: { open: boolean; width: number } = (() => {
  try {
    const raw = globalThis.localStorage?.getItem(DRAWER_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as { open?: unknown; width?: unknown };
      return {
        open: parsed.open === true,
        width: typeof parsed.width === 'number' ? parsed.width : 520,
      };
    }
  } catch {
    /* storage unavailable: defaults */
  }
  return { open: false, width: 520 };
})();
const drawerListeners = new Set<() => void>();

export function traceDrawer(): { open: boolean; width: number } {
  return drawer;
}

export function setTraceDrawer(patch: Partial<{ open: boolean; width: number }>): void {
  const next = { ...drawer, ...patch };
  if (next.open === drawer.open && next.width === drawer.width) return;
  drawer = next;
  try {
    globalThis.localStorage?.setItem(DRAWER_KEY, JSON.stringify(drawer));
  } catch {
    /* not persisted: fine */
  }
  for (const l of drawerListeners) l();
}

export function useTraceDrawer(): { open: boolean; width: number } {
  return useSyncExternalStore(
    (l) => {
      drawerListeners.add(l);
      return () => {
        drawerListeners.delete(l);
      };
    },
    traceDrawer,
    traceDrawer,
  );
}

/** Subscribe to every agent's chat state (the trace view reads the followed one). */
export function subscribeChats(listener: () => void): () => void {
  return subscribe(listener);
}

/** Test seam: forget everything. Not used by the app. */
export function resetChatStates(): void {
  states.clear();
  for (const l of listeners) l();
}
