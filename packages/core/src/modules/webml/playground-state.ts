/**
 * The playground's conversation, kept outside the component so a remount (tab
 * switch, workspace switch) keeps it — the same reason the model lives in
 * `engine.ts`. Singleton pane, so one transcript.
 */
import { useSyncExternalStore } from 'react';

import type { StopReason } from '@horrible/webml';

import type { TokenStep } from '../../token-strip/TokenStrip';

export interface PlaygroundMessage {
  role: 'user' | 'assistant';
  content: string;
  meta?: {
    ttftMs: number;
    tokensPerSecond: number;
    tokens: number;
    stop: StopReason;
    model: string;
  };
  steps?: TokenStep[];
  error?: string;
}

interface Snapshot {
  messages: PlaygroundMessage[];
  /** Index of the assistant message being streamed, or -1. */
  streaming: number;
}

let snapshot: Snapshot = { messages: [], streaming: -1 };
const listeners = new Set<() => void>();

function set(next: Snapshot): void {
  snapshot = next;
  for (const l of listeners) l();
}

export const playground = {
  get: () => snapshot,
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  /** Append a user turn and an empty assistant turn to stream into; returns its index. */
  begin(user: string): number {
    const messages = [
      ...snapshot.messages,
      { role: 'user' as const, content: user },
      { role: 'assistant' as const, content: '', steps: [] },
    ];
    set({ messages, streaming: messages.length - 1 });
    return messages.length - 1;
  },
  patch(index: number, update: (m: PlaygroundMessage) => PlaygroundMessage): void {
    const messages = snapshot.messages.slice();
    if (!messages[index]) return;
    messages[index] = update(messages[index]);
    set({ ...snapshot, messages });
  },
  end(): void {
    set({ ...snapshot, streaming: -1 });
  },
  clear(): void {
    set({ messages: [], streaming: -1 });
  },
};

export function usePlayground(): Snapshot {
  return useSyncExternalStore(playground.subscribe, playground.get);
}

/** Qwen3/SmolLM3 reasoning arrives inline as `<think>…</think>` before the answer. */
export function splitThinking(text: string): { thinking: string | null; answer: string } {
  const open = text.indexOf('<think>');
  if (open === -1 || text.slice(0, open).trim()) return { thinking: null, answer: text };
  const close = text.indexOf('</think>', open);
  if (close === -1) return { thinking: text.slice(open + 7).trim(), answer: '' };
  return {
    thinking: text.slice(open + 7, close).trim(),
    answer: text.slice(close + 8).trimStart(),
  };
}
