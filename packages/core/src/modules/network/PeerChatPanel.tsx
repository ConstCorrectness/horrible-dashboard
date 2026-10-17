import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';

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
import { ChatThread } from './ChatThread';
import { chatRequestUnread, subscribeChat } from './peerchat';
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
 * is stated rather than hidden. The thread itself is `ChatThread`, shared with the
 * chat dock beside the Friends list, so the two are one chat in two places.
 * See docs/modules/social.mdx.
 */
export function PeerChatPanel() {
  const { roster } = useSocial();
  useCards();
  const selected = useSelected();
  const [unread, setUnread] = useState<Record<string, number>>({});

  useEffect(() => {
    initSocial();
    initNetwork();
    requestRoster();
    chatRequestUnread();
    return subscribeChat((event) => {
      if (event.kind === 'unread') setUnread(event.counts ?? {});
    });
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

  // No auto-select: picking someone here opens a tab in the Friends chat dock
  // too, and a tab nobody asked for is clutter.
  const active: Friend | undefined = people.find((p) => p.person_id === selected);

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

      <ChatThread friend={active} />
    </div>
  );
}
