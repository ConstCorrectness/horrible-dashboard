/**
 * One conversation: the log and the composer, for one person.
 *
 * Shared by the chat dock beside the Friends list and by the Messages section, so
 * the two cannot drift into two subtly different chats. It owns the history fetch
 * for its person and nothing else — which conversation is open is the caller's.
 */
import { useEffect, useRef, useState } from 'react';

import type { Friend } from '../social/api';
import { chatClose, chatOpen, chatSend, subscribeChat, type ChatMessage } from './peerchat';

function dayStamp(ts: number): string {
  const date = new Date(ts * 1000);
  const today = new Date();
  if (date.toDateString() === today.toDateString()) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  return date.toLocaleDateString();
}

/**
 * How long after their last message you can still answer someone who reads as
 * offline. An *invisible* friend still has a live session — that is what lets them
 * message you — and refusing the reply would turn invisible into "can talk, can't
 * be answered", which nobody chose.
 */
export const REPLY_WINDOW_S = 15 * 60;

export function canSend(friend: Friend | undefined, messages: ChatMessage[], now: number): boolean {
  if (!friend) return false;
  if (friend.presence === 'online') return true;
  const last = messages[messages.length - 1];
  return !!last && last.direction === 'in' && now - last.ts < REPLY_WINDOW_S;
}

export function ChatThread({ friend }: { friend: Friend | undefined }) {
  const personId = friend?.person_id ?? null;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    return subscribeChat((event) => {
      if (event.kind === 'history') {
        if (event.personId === personId) setMessages(event.messages ?? []);
      } else if (event.kind === 'message' && event.message) {
        if (event.message.personId === personId) {
          setMessages((prev) => [...prev, event.message as ChatMessage]);
        }
      } else if (event.kind === 'error' && event.personId === personId) {
        setError(event.error ?? 'send failed');
      }
    });
  }, [personId]);

  useEffect(() => {
    setMessages([]);
    setError(null);
    if (!personId) return;
    chatOpen(personId);
    return () => chatClose(personId);
  }, [personId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  const sendable = canSend(friend, messages, Date.now() / 1000);

  const send = () => {
    const text = draft.trim();
    if (!text || !personId) return;
    setError(null);
    chatSend(personId, text);
    setDraft('');
  };

  return (
    <div className="chat-thread">
      <div ref={scrollRef} className="chat-log">
        {messages.length === 0 ? (
          <p className="people-dim">
            {!friend
              ? 'Pick someone to talk to.'
              : sendable
                ? 'No messages yet — say hello.'
                : `${friend.display_name} is offline. You can read the history; sending needs one of their machines up.`}
          </p>
        ) : (
          messages.map((m) => (
            <div key={m.id} className="chat-msg" data-dir={m.direction}>
              <span title={new Date(m.ts * 1000).toLocaleString()}>{m.text}</span>
              <em>{dayStamp(m.ts)}</em>
            </div>
          ))
        )}
      </div>

      {error && <div className="chat-error">{error}</div>}

      <form
        className="chat-compose"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input
          type="text"
          value={draft}
          placeholder={
            !friend
              ? 'No one selected'
              : sendable
                ? 'Message…'
                : `${friend.display_name} is offline`
          }
          disabled={!sendable}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button type="submit" disabled={!sendable || !draft.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
