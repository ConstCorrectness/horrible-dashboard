/**
 * Chat sessions client: persisted agent-chat transcripts (backend chat module).
 * Mirrors `workspace.ts` — the list is metadata-only; a per-id GET returns the full
 * transcript. See docs/modules/agent-chat.md.
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
  messages: ChatMessage[];
  created: number;
  updated: number;
}

export interface ChatSessionMeta {
  id: string;
  title: string;
  updated: number;
}

export interface ChatSessionsList {
  active: string | null;
  sessions: ChatSessionMeta[];
}

export function getSessions(): Promise<ChatSessionsList> {
  return apiGet<ChatSessionsList>('/chat/sessions');
}

export function getSession(id: string): Promise<ChatSession> {
  return apiGet<ChatSession>(`/chat/sessions/${encodeURIComponent(id)}`);
}

export function createSession(title?: string): Promise<ChatSession> {
  return apiPost<ChatSession>('/chat/sessions', title ? { title } : {});
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
