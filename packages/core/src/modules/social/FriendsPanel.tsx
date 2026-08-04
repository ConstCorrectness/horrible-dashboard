import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';

import { useAgentContext } from '../../agent-context';
import { revealSection } from '../../layout/controller';
import { useAccount } from '../../useAccount';
import { PublicProfile } from '../games/panels/PublicProfile';
import { chatRequestUnread, subscribeChat } from '../network/peerchat';
import { Avatar } from '../people/Avatar';
import {
  ensureProfileCards,
  getProfileCard,
  getProfileCards,
  subscribeProfileCards,
} from '../people/profile-cards';
import { openConversation } from '../people/conversation';
import { addFriend, linkDevice, updateSelfProfile, type Friend } from './api';
import {
  blockViaChannel,
  getSocialState,
  initSocial,
  removeViaChannel,
  requestRoster,
  respondViaChannel,
  subscribeSocial,
} from './ws';

function useSocialState() {
  return useSyncExternalStore(subscribeSocial, getSocialState, getSocialState);
}

function useCards() {
  return useSyncExternalStore(subscribeProfileCards, getProfileCards, getProfileCards);
}

/**
 * One person in the list: face, name, what they're up to, and the actions that
 * follow. Steam's shape, because Steam's shape is right — the two things you want
 * at a glance are *who is around* and *is there anything waiting for me*, and
 * everything else belongs behind the row.
 *
 * The card (avatar, level) is decoration from the game server; the name, presence
 * and every action come from the local roster, so a row still renders in full with
 * that server unreachable.
 */
function FriendRow({
  friend,
  unread,
  onOpenProfile,
}: {
  friend: Friend;
  unread: number;
  onOpenProfile: (handle: string) => void;
}) {
  const [menu, setMenu] = useState(false);
  const card = getProfileCard(friend.handle);
  const online = friend.presence === 'online';
  const devices = friend.devices.filter((d) => d.online);

  const subtitle = card?.status_text
    ? card.status_text
    : online
      ? devices.length > 1
        ? `Online · ${devices.length} devices`
        : (devices[0]?.label ?? 'Online')
      : 'Offline';

  return (
    <li className="friend-row" data-online={online ? 'true' : 'false'}>
      <Avatar
        name={friend.display_name}
        emoji={card?.avatar}
        imageRef={card?.avatar_url}
        size={34}
        online={online}
        showPresence
      />
      <div className="friend-row-main">
        <div className="friend-row-name">
          <strong>{friend.display_name}</strong>
          {friend.is_self && <span className="friend-row-tag">you</span>}
          {card && <span className="friend-row-level">Lv {card.level}</span>}
          {unread > 0 && <span className="friend-row-unread">{unread}</span>}
        </div>
        <span className="friend-row-sub">{subtitle}</span>
      </div>

      {friend.status === 'pending_in' ? (
        <span className="friend-row-actions">
          <button onClick={() => respondViaChannel(friend.person_id, true)}>Accept</button>
          <button onClick={() => respondViaChannel(friend.person_id, false)}>Decline</button>
        </span>
      ) : friend.status === 'pending_out' ? (
        <span className="friend-row-sub">requested…</span>
      ) : (
        <span className="friend-row-actions">
          <button
            title="Message"
            onClick={() => {
              openConversation(friend.person_id);
              revealSection('messages', 'people.home');
            }}
          >
            ✉
          </button>
          <button title="More" onClick={() => setMenu((m) => !m)}>
            ⋯
          </button>
        </span>
      )}

      {menu && (
        <ul className="friend-menu">
          {friend.handle && (
            <li>
              <button
                onClick={() => {
                  setMenu(false);
                  onOpenProfile(friend.handle!);
                }}
              >
                View profile
              </button>
            </li>
          )}
          <li>
            <button
              onClick={() => {
                setMenu(false);
                openConversation(friend.person_id);
                revealSection('messages', 'people.home');
              }}
            >
              Message
            </button>
          </li>
          {/* Each machine of theirs, so "my other computer" stays reachable — the
              one place a node id is still a useful thing to show. */}
          {friend.devices.length > 0 && (
            <li className="friend-menu-devices">
              {friend.devices.map((d) => (
                <span key={d.node_id} title={d.node_id} data-online={d.online ? 'true' : 'false'}>
                  {d.online ? '●' : '○'} {d.label}
                </span>
              ))}
            </li>
          )}
          <li>
            <button
              onClick={() => {
                setMenu(false);
                removeViaChannel(friend.person_id);
              }}
            >
              Remove friend
            </button>
          </li>
          {!friend.is_self && (
            <li>
              <button
                onClick={() => {
                  setMenu(false);
                  blockViaChannel(friend.person_id);
                }}
              >
                Block
              </button>
            </li>
          )}
        </ul>
      )}
    </li>
  );
}

/**
 * The friends roster: who you know, who's around, and what's waiting.
 *
 * Friending is person-level — a friend with a desktop and a laptop is one row with
 * two devices, not two rows. Accepting grants those machines fabric trust, which is
 * what lets peer chat, shared panes and agent-to-agent questions work between
 * friends without a second pairing step.
 *
 * **Incoming friend requests are not here.** They live in the Requests section
 * alongside the commons ones, because two separate request inboxes in one pane is
 * how you end up ignoring both. See docs/modules/social.mdx.
 */
export function FriendsPanel() {
  const { roster } = useSocialState();
  useCards();
  const { account } = useAccount();
  const [code, setCode] = useState('');
  const [address, setAddress] = useState('');
  const [invite, setInvite] = useState('');
  const [name, setName] = useState('');
  const [filter, setFilter] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [unread, setUnread] = useState<Record<string, number>>({});
  const [viewing, setViewing] = useState<string | null>(null);

  useEffect(() => {
    initSocial();
    requestRoster();
    chatRequestUnread();
    return subscribeChat((event) => {
      if (event.kind === 'unread') setUnread(event.counts ?? {});
    });
  }, []);

  const me = roster?.self_profile;
  const friends = useMemo(() => roster?.friends ?? [], [roster]);

  // One batched card fetch for the whole roster, rather than one per row.
  useEffect(() => {
    void ensureProfileCards([me?.handle, ...friends.map((f) => f.handle)]);
  }, [friends, me?.handle]);

  // Let the local agent see the roster, so it can resolve "ask Rob's agent" or
  // "message my laptop" to a concrete person and node.
  useAgentContext(() => ({
    self: me ? { personId: me.person_id, name: me.display_name } : null,
    friends: friends.map((f) => ({
      personId: f.person_id,
      name: f.display_name,
      handle: f.handle,
      status: f.status,
      presence: f.presence,
      isSelf: f.is_self,
      unread: unread[f.person_id] ?? 0,
      nodes: f.devices.map((d) => ({ nodeId: d.node_id, label: d.label, online: d.online })),
    })),
  }));

  const submitAdd = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await addFriend(code.trim(), address.trim() || undefined);
      if (!res.ok) setError(res.error ?? 'could not add that friend');
      else {
        setCode('');
        setAddress('');
      }
    } finally {
      setBusy(false);
      requestRoster();
    }
  }, [code, address]);

  const submitLink = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await linkDevice(invite.trim());
      if (!res.ok) setError(res.error ?? 'could not link that machine');
      else setInvite('');
    } finally {
      setBusy(false);
      requestRoster();
    }
  }, [invite]);

  const copyCode = useCallback(() => {
    if (!me) return;
    void navigator.clipboard?.writeText(me.friend_code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }, [me]);

  if (viewing) {
    return (
      <div className="social-friends">
        <PublicProfile
          handle={viewing}
          viewerAccountId={account?.id ?? null}
          onBack={() => setViewing(null)}
        />
      </div>
    );
  }

  const listed = friends.filter((f) => f.status === 'accepted' || f.status === 'pending_out');
  const needle = filter.trim().toLowerCase();
  const matching = needle
    ? listed.filter(
        (f) =>
          f.display_name.toLowerCase().includes(needle) ||
          (f.handle ?? '').toLowerCase().includes(needle),
      )
    : listed;
  const online = matching.filter((f) => f.presence === 'online');
  const offline = matching.filter((f) => f.presence !== 'online');
  const myCard = getProfileCard(me?.handle);

  return (
    <div className="social-friends">
      {me ? (
        <header className="friends-me">
          <Avatar
            name={me.display_name}
            emoji={myCard?.avatar}
            imageRef={myCard?.avatar_url}
            size={40}
          />
          <div className="friends-me-text">
            <strong>{me.display_name}</strong>
            {me.handle ? (
              <button
                type="button"
                className="friends-me-handle"
                title="View your profile"
                onClick={() => setViewing(me.handle!)}
              >
                @{me.handle}
              </button>
            ) : (
              <span className="people-dim">not signed in to the ladder</span>
            )}
          </div>
          <button onClick={copyCode} title={me.friend_code}>
            {copied ? 'Copied' : 'Friend code'}
          </button>
        </header>
      ) : (
        <p className="people-dim">Loading your identity…</p>
      )}

      {listed.length > 3 && (
        <input
          className="friends-filter"
          value={filter}
          placeholder="Search friends…"
          onChange={(e) => setFilter(e.target.value)}
        />
      )}

      {listed.length === 0 ? (
        <p className="people-dim">No friends yet. Add someone with their friend code below.</p>
      ) : (
        <>
          <h4 className="friends-group">Online ({online.length})</h4>
          {online.length === 0 ? (
            <p className="people-dim">Nobody right now.</p>
          ) : (
            <ul className="friends-list">
              {online.map((f) => (
                <FriendRow
                  key={f.person_id}
                  friend={f}
                  unread={unread[f.person_id] ?? 0}
                  onOpenProfile={setViewing}
                />
              ))}
            </ul>
          )}
          {offline.length > 0 && (
            <>
              <h4 className="friends-group">Offline ({offline.length})</h4>
              <ul className="friends-list friends-list-offline">
                {offline.map((f) => (
                  <FriendRow
                    key={f.person_id}
                    friend={f}
                    unread={unread[f.person_id] ?? 0}
                    onOpenProfile={setViewing}
                  />
                ))}
              </ul>
            </>
          )}
        </>
      )}

      <details className="people-fold">
        <summary>Add a friend, link a machine, rename yourself</summary>
        <div className="friends-admin">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submitAdd();
            }}
          >
            <label className="people-label">Add a friend</label>
            <input
              value={code}
              placeholder="HD-XXXX-XXXX-XXXX-XXXX-XXXX"
              spellCheck={false}
              onChange={(e) => setCode(e.target.value)}
            />
            <input
              value={address}
              placeholder="optional: ws://their-host:8000/peer-ws (needed off your network)"
              spellCheck={false}
              onChange={(e) => setAddress(e.target.value)}
            />
            <button type="submit" disabled={!code.trim() || busy}>
              {busy ? 'Sending…' : 'Send friend request'}
            </button>
          </form>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submitLink();
            }}
          >
            <label className="people-label">Link another of your machines</label>
            <p className="people-hint">
              Generate an invite on the other computer, then paste it here. It joins your identity
              rather than becoming a separate friend.
            </p>
            <input
              value={invite}
              placeholder="paste that machine’s invite"
              spellCheck={false}
              onChange={(e) => setInvite(e.target.value)}
            />
            <button type="submit" disabled={!invite.trim() || busy || !me?.holds_person_key}>
              Link
            </button>
          </form>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) {
                void updateSelfProfile(name.trim()).then(() => {
                  setName('');
                  requestRoster();
                });
              }
            }}
          >
            <label className="people-label">Rename yourself</label>
            <input
              value={name}
              placeholder={me ? `currently “${me.display_name}”` : ''}
              onChange={(e) => setName(e.target.value)}
            />
            <button type="submit" disabled={!name.trim() || !me?.holds_person_key}>
              Rename
            </button>
          </form>

          {me && !me.holds_person_key && (
            <p className="people-note">
              This machine was linked by another device, so it can’t link further machines or rename
              you.
            </p>
          )}
        </div>
      </details>

      {error && <p className="friends-error">{error}</p>}
    </div>
  );
}
