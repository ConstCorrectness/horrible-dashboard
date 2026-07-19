/**
 * Chat sessions client: persisted agent-chat transcripts (backend chat module).
 * Mirrors `workspace.ts` — the list is metadata-only; a per-id GET returns the full
 * transcript. Sessions belong to a roster agent (`agent_id`, default "main"); the
 * list endpoint filters per agent so each agent keeps its own conversations.
 * See docs/modules/agent-chat.md.
 */
import { apiDelete, apiGet, apiPost, apiPut } from '../../api';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  reasoning?: string;
  actions?: string[];
}

export interface ChatSession {
  id: string;
  title: string;
  agent_id: string;
  messages: ChatMessage[];
  created: number;
  updated: number;
}

export interface ChatSessionMeta {
  id: string;
  title: string;
  agent_id: string;
  updated: number;
}

export interface ChatSessionsList {
  active: string | null;
  sessions: ChatSessionMeta[];
}

/** All sessions, or one agent's (then `active` is that agent's active session). */
export function getSessions(agent?: string): Promise<ChatSessionsList> {
  const query = agent ? `?agent=${encodeURIComponent(agent)}` : '';
  return apiGet<ChatSessionsList>(`/chat/sessions${query}`);
}

export function getSession(id: string): Promise<ChatSession> {
  return apiGet<ChatSession>(`/chat/sessions/${encodeURIComponent(id)}`);
}

export function createSession(title?: string, agentId?: string): Promise<ChatSession> {
  return apiPost<ChatSession>('/chat/sessions', {
    ...(title ? { title } : {}),
    ...(agentId ? { agent_id: agentId } : {}),
  });
}

/** Partial upsert: pass only `messages` to save a turn without touching the title. */
export function saveSession(
  id: string,
  patch: { title?: string; messages?: ChatMessage[] },
): Promise<ChatSession> {
  return apiPut<ChatSession>(`/chat/sessions/${encodeURIComponent(id)}`, patch);
}

export function setActiveSession(id: string): Promise<ChatSessionsList> {
  return apiPut<ChatSessionsList>('/chat/sessions/active', { id });
}

export function deleteSession(id: string): Promise<ChatSessionsList> {
  return apiDelete<ChatSessionsList>(`/chat/sessions/${encodeURIComponent(id)}`);
}
