import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';

import { Avatar } from '../people/Avatar';
import { getConversation, openConversation, subscribeConversation } from '../people/conversation';
import {
  ensureProfileCards,
  getProfileCard,
  getProfileCards,
  subscribeProfileCards,
} from '../people/profile-cards';
import { getSocialState, initSocial, requestRoster, subscribeSocial } from '../social/ws';
import type { Friend } from '../social/api';
import {
  chatClose,
  chatOpen,
  chatRequestUnread,
  chatSend,
  subscribeChat,
  type ChatMessage,
} from './peerchat';
import { initNetwork } from './ws';

function useSocial() {
  return useSyncExternalStore(subscribeSocial, getSocialState, getSocialState);
}

function useSelected() {
  return useSyncExternalStore(subscribeConversation, getConversation, getConversation);
}

function useCards() {
  return useSyncExternalStore(subscribeProfileCards, getProfileCards, getProfileCards);
}

function dayStamp(ts: number): string {
  const date = new Date(ts * 1000);
  const today = new Date();
  if (date.toDateString() === today.toDateString()) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  return date.toLocaleDateString();
}

/**
 * Direct messages, **by person**.
 *
 * This picker used to list `node_name`s straight off the hub's connection table —
 * raw machines, with no idea whether a given one belonged to a friend, to another
 * of your own computers, or to a stranger you happened to be connected to. A friend
 * with a desktop and a laptop appeared twice under two names you had never chosen,
 * and a friend who was offline did not appear at all, so the Messages tab was empty
 * far more often than you had no friends.
 *
 * Now it is a conversation list: one row per person, newest first, with unread
 * badges. History is **persisted server-side** and keyed by person, so it survives
 * a restart and follows a friend from one of their machines to another. Which
 * machine a message travels over is chosen at send time and shown nowhere.
 *
 * An offline friend can still be selected and read — you just cannot send, which
 * is stated rather than hidden. See docs/modules/social.mdx.
 */
export function PeerChatPanel() {
  const { roster } = useSocial();
  useCards();
  const selected = useSelected();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [unread, setUnread] = useState<Record<string, number>>({});
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    initSocial();
    initNetwork();
    requestRoster();
    chatRequestUnread();
  }, []);

  // People you can hold a conversation with: accepted friends, and your own linked
  // machines (messaging your other computer is a real thing to want).
  const people = useMemo(
    () => (roster?.friends ?? []).filter((f) => f.status === 'accepted'),
    [roster],
  );

  useEffect(() => {
    void ensureProfileCards(people.map((p) => p.handle));
  }, [people]);

  const active: Friend | undefined = people.find((p) => p.person_id === selected);
  const online = active?.presence === 'online';

  useEffect(() => {
    if (!selected && people.length > 0) openConversation(people[0].person_id);
  }, [selected, people]);

  // One subscription for the whole panel: the badge counts arrive whether or not a
  // conversation is open, and a message for *another* thread must still bump its
  // badge rather than being dropped because the pane is showing someone else.
  useEffect(() => {
    return subscribeChat((event) => {
      if (event.kind === 'unread') {
        setUnread(event.counts ?? {});
      } else if (event.kind === 'history') {
        if (event.personId === selected) setMessages(event.messages ?? []);
      } else if (event.kind === 'message' && event.message) {
        if (event.message.personId === selected) {
          setMessages((prev) => [...prev, event.message as ChatMessage]);
        }
      } else if (event.kind === 'error' && event.personId === selected) {
        setError(event.error ?? 'send failed');
      }
    });
  }, [selected]);

  useEffect(() => {
    if (!selected) {
      setMessages([]);
      return;
    }
    setMessages([]);
    setError(null);
    chatOpen(selected);
    return () => chatClose(selected);
  }, [selected]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  const send = () => {
    const text = draft.trim();
    if (!text || !selected) return;
    chatSend(selected, text);
    setDraft('');
  };

  return (
    <div className="chat-pane">
      <ul className="chat-people">
        {people.length === 0 && <li className="people-dim">No friends yet.</li>}
        {people.map((p) => {
          const card = getProfileCard(p.handle);
          const count = unread[p.person_id] ?? 0;
          return (
            <li key={p.person_id}>
              <button
                type="button"
                className="chat-person"
                data-active={p.person_id === selected ? 'true' : 'false'}
                onClick={() => openConversation(p.person_id)}
              >
                <Avatar
                  name={p.display_name}
                  emoji={card?.avatar}
                  imageRef={card?.avatar_url}
                  size={26}
                  online={p.presence === 'online'}
                  showPresence
                />
                <span className="chat-person-name">{p.display_name}</span>
                {count > 0 && <span className="chat-unread">{count}</span>}
              </button>
            </li>
          );
        })}
      </ul>

      <div className="chat-thread">
        <div ref={scrollRef} className="chat-log">
          {messages.length === 0 ? (
            <p className="people-dim">
              {!active
                ? 'Add a friend to start a conversation.'
                : online
                  ? 'No messages yet — say hello.'
                  : `${active.display_name} is offline. You can read the history; sending needs one of their machines up.`}
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
            value={draft}
            placeholder={
              !active
                ? 'No one selected'
                : online
                  ? 'Message…'
                  : `${active.display_name} is offline`
            }
            disabled={!online}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button type="submit" disabled={!online || !draft.trim()}>
            Send
          </button>
        </form>
      </div>
    </div>
  );
}
