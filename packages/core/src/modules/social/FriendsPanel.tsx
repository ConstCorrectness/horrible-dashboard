import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type MouseEvent,
} from 'react';

import { AccountGate } from '../../AccountGate';
import { STAGGER_CAP } from '../../DataList';
import { useAgentContext } from '../../agent-context';
import { IconChevron, IconClose, IconMore, IconPerson, IconSearch } from '../../glyphs';
import { openContextMenu } from '../../overlay/context-menu';
import { useAccount } from '../../useAccount';
import { PublicProfile } from '../games/panels/PublicProfile';
import { ChatThread } from '../network/ChatThread';
import { chatRequestUnread, subscribeChat } from '../network/peerchat';
import { Avatar } from '../people/Avatar';
import {
  closeConversation,
  getConversations,
  openConversation,
  pruneConversations,
  subscribeConversation,
} from '../people/conversation';
import {
  ensureProfileCards,
  getProfileCard,
  getProfileCards,
  subscribeProfileCards,
} from '../people/profile-cards';
import { addFriend, type Friend, type SelfProfile } from './api';
import {
  getViewingProfile,
  messageFriend,
  subscribeViewingProfile,
  viewProfile,
} from './friend-actions';
import {
  durationSince,
  groupFriends,
  personaLabel,
  personaTone,
  statusLine,
  type FriendGroup,
  type GroupId,
  type PersonaTone,
} from './friends-model';
import {
  getSocialState,
  initSocial,
  requestRoster,
  respondViaChannel,
  subscribeSocial,
} from './ws';
import './friends.css';

function useSocialState() {
  return useSyncExternalStore(subscribeSocial, getSocialState, getSocialState);
}

function useCards() {
  return useSyncExternalStore(subscribeProfileCards, getProfileCards, getProfileCards);
}

function useConversations() {
  return useSyncExternalStore(subscribeConversation, getConversations, getConversations);
}

function useViewing() {
  return useSyncExternalStore(subscribeViewingProfile, getViewingProfile, getViewingProfile);
}

/** Epoch seconds, re-read once a minute — enough for "in a match for 12 min". */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now() / 1000), 60_000);
    return () => window.clearInterval(t);
  }, []);
  return now;
}

// ---- collapsed groups, remembered per viewer -----------------------------------------

const COLLAPSE_KEY = 'friends.collapsed';

function loadCollapsed(): Set<GroupId> {
  try {
    const raw = window.localStorage.getItem(COLLAPSE_KEY);
    return new Set(raw ? (JSON.parse(raw) as GroupId[]) : ['sent']);
  } catch {
    return new Set(['sent']);
  }
}

function saveCollapsed(groups: Set<GroupId>): void {
  try {
    window.localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...groups]));
  } catch {
    // Private window or blocked storage: the fold just isn't remembered.
  }
}

/** Open the shared context menu under an element, as a click on it would. */
function menuBelow(e: MouseEvent<HTMLElement>, target: { kind: string; [k: string]: unknown }) {
  e.preventDefault();
  e.stopPropagation();
  const rect = e.currentTarget.getBoundingClientRect();
  openContextMenu({ clientX: rect.left, clientY: rect.bottom + 2 }, target);
}

// ---- one person ----------------------------------------------------------------------

/**
 * One person: face, name, what they are doing. Steam's shape — the two things you
 * want at a glance are *who is around* and *is anything waiting for me*, and the
 * rest is a right-click away.
 *
 * The card (avatar, level) is decoration from the game server; the name, persona
 * and every action come from the local roster, so a row renders in full with that
 * server unreachable.
 */
function FriendRow({
  friend,
  unread,
  active,
  index,
  now,
}: {
  friend: Friend;
  unread: number;
  active: boolean;
  index: number;
  now: number;
}) {
  const card = getProfileCard(friend.handle);
  const tone: PersonaTone = personaTone(friend);
  const request = friend.status === 'pending_in';
  const accepted = friend.status === 'accepted';
  const since =
    friend.activity && friend.presence === 'online'
      ? durationSince(friend.activity.since, now)
      : null;

  return (
    <li
      className="fl-row"
      data-persona={tone}
      data-active={active ? 'true' : 'false'}
      data-request={request ? 'true' : 'false'}
      style={{ '--hd-i': Math.min(index, STAGGER_CAP) } as CSSProperties}
      onContextMenu={(e) => {
        if (openContextMenu(e, { kind: 'social.friend', personId: friend.person_id })) {
          e.preventDefault();
        }
      }}
    >
      <button
        type="button"
        className="fl-row-hit"
        disabled={!accepted}
        title={accepted ? `Message ${friend.display_name}` : undefined}
        onClick={() => messageFriend(friend.person_id)}
      >
        <Avatar
          name={friend.display_name}
          emoji={card?.avatar}
          imageRef={card?.avatar_url}
          size={32}
          online={tone !== 'offline'}
          showPresence
        />
        <span className="fl-row-main">
          <span className="fl-row-name">
            <span className="fl-row-display">{friend.display_name}</span>
            {friend.is_self && <span className="fl-tag">you</span>}
            {card && <span className="fl-tag">Lv {card.level}</span>}
          </span>
          <span className="fl-row-sub">
            <span className="fl-row-status">{statusLine(friend, now)}</span>
            {since && <span className="fl-row-since">{since}</span>}
          </span>
        </span>
        {unread > 0 && (
          <span className="fl-unread" aria-label={`${unread} unread`}>
            {unread}
          </span>
        )}
      </button>

      {request ? (
        <span className="fl-row-decide">
          <button type="button" onClick={() => respondViaChannel(friend.person_id, true)}>
            Accept
          </button>
          <button
            type="button"
            className="fl-icon-btn"
            title="Decline"
            aria-label="Decline"
            onClick={() => respondViaChannel(friend.person_id, false)}
          >
            <IconClose />
          </button>
        </span>
      ) : (
        <button
          type="button"
          className="fl-icon-btn fl-row-more"
          title="More"
          aria-label={`More for ${friend.display_name}`}
          onClick={(e) => menuBelow(e, { kind: 'social.friend', personId: friend.person_id })}
        >
          <IconMore />
        </button>
      )}
    </li>
  );
}

function Group({
  group,
  collapsed,
  onToggle,
  unread,
  activeId,
  now,
}: {
  group: FriendGroup;
  collapsed: boolean;
  onToggle: () => void;
  unread: Record<string, number>;
  activeId: string | null;
  now: number;
}) {
  return (
    <section className="fl-group" data-group={group.id}>
      <button type="button" className="fl-group-head" aria-expanded={!collapsed} onClick={onToggle}>
        <IconChevron className="hd-icon fl-chevron" data-collapsed={collapsed ? 'true' : 'false'} />
        <span className="fl-group-label">{group.label}</span>
        <span className="fl-group-count">{group.friends.length}</span>
      </button>
      {!collapsed &&
        (group.friends.length === 0 ? (
          <p className="fl-empty">Nobody right now.</p>
        ) : (
          <ul className="fl-rows">
            {group.friends.map((f, i) => (
              <FriendRow
                key={f.person_id}
                friend={f}
                unread={unread[f.person_id] ?? 0}
                active={f.person_id === activeId}
                index={i}
                now={now}
              />
            ))}
          </ul>
        ))}
    </section>
  );
}

// ---- you ----------------------------------------------------------------------------

function SelfHeader({ me }: { me: SelfProfile }) {
  const card = getProfileCard(me.handle);
  // What friends see, which is what the dot should say: invisible is offline to
  // them, and an idle `online` is away.
  const shown =
    me.persona === 'invisible'
      ? 'offline'
      : me.persona === 'online' && me.idle
        ? 'away'
        : me.persona;
  const label = me.persona === 'online' && me.idle ? 'Away (idle)' : personaLabel(me.persona);
  return (
    <header className="fl-me" data-persona={shown}>
      <Avatar
        name={me.display_name}
        emoji={card?.avatar}
        imageRef={card?.avatar_url}
        size={38}
        online={shown !== 'offline'}
        showPresence
      />
      <div className="fl-me-text">
        <span className="fl-me-name">{me.display_name}</span>
        <button
          type="button"
          className="fl-me-status"
          aria-haspopup="menu"
          title="Set your status"
          onClick={(e) => menuBelow(e, { kind: 'social.self' })}
        >
          {label}
          <IconChevron className="hd-icon fl-me-caret" />
        </button>
        {me.activity && <span className="fl-me-activity">{me.activity.title}</span>}
      </div>
    </header>
  );
}

// ---- the chat dock ----------------------------------------------------------------------

/**
 * Open conversations, docked beside the list: a tab per person, the active one
 * below. Wide enough and the two sit side by side; in a narrow dock the chat takes
 * the pane and "Friends" goes back, because a 120px chat helps nobody.
 */
function ChatDock({
  friends,
  open,
  active,
  unread,
  now,
}: {
  friends: Friend[];
  open: string[];
  active: string;
  unread: Record<string, number>;
  now: number;
}) {
  const byId = new Map(friends.map((f) => [f.person_id, f]));
  const current = byId.get(active);
  return (
    <section className="fl-chat" aria-label="Chat">
      <div className="fl-chat-tabs" role="tablist">
        <button
          type="button"
          className="fl-icon-btn fl-chat-back"
          title="Back to friends"
          aria-label="Back to friends"
          onClick={() => openConversation(null)}
        >
          <IconChevron className="hd-icon fl-back-chevron" />
        </button>
        {open.map((id) => {
          const f = byId.get(id);
          if (!f) return null;
          const count = id === active ? 0 : (unread[id] ?? 0);
          return (
            <span
              key={id}
              className="fl-chat-tab"
              data-active={id === active ? 'true' : 'false'}
              data-persona={personaTone(f)}
            >
              <button
                type="button"
                role="tab"
                className="fl-chat-tab-hit"
                aria-selected={id === active}
                onClick={() => openConversation(id)}
              >
                <span className="fl-tab-dot" aria-hidden="true" />
                <span className="fl-tab-name">{f.display_name}</span>
                {count > 0 && <span className="fl-unread">{count}</span>}
              </button>
              <button
                type="button"
                className="fl-icon-btn fl-tab-close"
                title={`Close chat with ${f.display_name}`}
                aria-label={`Close chat with ${f.display_name}`}
                onClick={() => closeConversation(id)}
              >
                <IconClose />
              </button>
            </span>
          );
        })}
      </div>
      {current && (
        <header className="fl-chat-head" data-persona={personaTone(current)}>
          <Avatar
            name={current.display_name}
            emoji={getProfileCard(current.handle)?.avatar}
            imageRef={getProfileCard(current.handle)?.avatar_url}
            size={28}
            online={personaTone(current) !== 'offline'}
            showPresence
          />
          <span className="fl-chat-who">
            <span className="fl-row-display">{current.display_name}</span>
            <span className="fl-row-status">{statusLine(current, now)}</span>
          </span>
          {current.handle && (
            <button
              type="button"
              className="fl-icon-btn"
              title="View profile"
              aria-label="View profile"
              onClick={() => viewProfile(current.handle)}
            >
              <IconPerson />
            </button>
          )}
        </header>
      )}
      <ChatThread friend={current} />
    </section>
  );
}

// ---- the pane ---------------------------------------------------------------------------

/**
 * The friends list, Steam-shaped: you and your status on top, then Friend Requests,
 * In-Game, Online and Offline, with chats docked beside it.
 *
 * Friending is person-level — a friend with a desktop and a laptop is one row with
 * two machines, not two rows. Accepting grants those machines fabric trust, which is
 * what lets chat, shared panes and agent-to-agent questions work between friends
 * without a second pairing step.
 *
 * **Friend requests are in the list**, at the top, because that is where you look.
 * The Requests section keeps the commons' meet requests, which are from strangers
 * and grant something different. See docs/modules/social.mdx.
 */
export function FriendsPanel() {
  const { roster } = useSocialState();
  useCards();
  const { account } = useAccount();
  const { open, active } = useConversations();
  const viewing = useViewing();
  const now = useNow();
  const [query, setQuery] = useState('');
  const [adding, setAdding] = useState(false);
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [unread, setUnread] = useState<Record<string, number>>({});
  const [collapsed, setCollapsed] = useState<Set<GroupId>>(loadCollapsed);

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
  const groups = useMemo(() => groupFriends(friends, query), [friends, query]);

  // A tab for someone you just removed or blocked would be a chat with nobody.
  useEffect(() => {
    if (!roster) return;
    pruneConversations(
      new Set(roster.friends.filter((f) => f.status === 'accepted').map((f) => f.person_id)),
    );
  }, [roster]);

  // One batched card fetch for the whole roster, rather than one per row.
  useEffect(() => {
    void ensureProfileCards([me?.handle, ...friends.map((f) => f.handle)]);
  }, [friends, me?.handle]);

  // Let the local agent see the roster, so it can resolve "ask Rob's agent" or
  // "message my laptop" to a concrete person and node — and know who asked not to
  // be bothered.
  useAgentContext(() => ({
    self: me
      ? { personId: me.person_id, name: me.display_name, persona: me.persona, idle: me.idle }
      : null,
    friends: friends.map((f) => ({
      personId: f.person_id,
      name: f.display_name,
      handle: f.handle,
      status: f.status,
      presence: f.presence,
      persona: f.persona,
      activity: f.activity
        ? `${f.activity.title}${f.activity.detail ? ` — ${f.activity.detail}` : ''}`
        : null,
      isSelf: f.is_self,
      unread: unread[f.person_id] ?? 0,
      nodes: f.devices.map((d) => ({ nodeId: d.node_id, label: d.label, online: d.online })),
    })),
    openChat: active,
  }));

  const toggle = useCallback((id: GroupId) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      saveCollapsed(next);
      return next;
    });
  }, []);

  const submitAdd = useCallback(async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      // No address: a `@username` resolves to the machines enrolled in that
      // account, and each is dialed through the relay by id wherever it is.
      const res = await addFriend(code.trim());
      if (!res.ok) {
        setError(res.error ?? 'could not add that friend');
      } else {
        setNotice(`Request sent to ${res.friend?.display_name ?? code.trim()}.`);
        setCode('');
      }
    } finally {
      setBusy(false);
      requestRoster();
    }
  }, [code]);

  if (viewing) {
    return (
      <div className="social-friends">
        <PublicProfile
          handle={viewing}
          viewerAccountId={account?.id ?? null}
          onBack={() => viewProfile(null)}
        />
      </div>
    );
  }

  const listed = friends.filter((f) => f.status !== 'blocked');
  const chatOpen = active !== null && open.includes(active);

  return (
    <div className="fl-wrap">
      <div className="fl" data-chat={chatOpen ? 'open' : 'closed'}>
        {chatOpen && active && (
          <ChatDock friends={friends} open={open} active={active} unread={unread} now={now} />
        )}

        <aside className="fl-list" aria-label="Friends">
          {me ? <SelfHeader me={me} /> : <p className="fl-empty">Loading your identity…</p>}
          {me && !me.handle ? <AccountGate onDone={requestRoster} /> : null}

          <div className="fl-tools">
            <label className="fl-search">
              <IconSearch />
              <input
                type="text"
                value={query}
                placeholder="Search friends"
                aria-label="Search friends"
                spellCheck={false}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="fl-icon-btn fl-add-toggle"
              aria-expanded={adding}
              title="Add a friend"
              aria-label="Add a friend"
              onClick={() => {
                setAdding((a) => !a);
                setError(null);
                setNotice(null);
              }}
            >
              <IconPerson />
              <span aria-hidden="true">+</span>
            </button>
          </div>

          {adding && (
            <form
              className="fl-add"
              onSubmit={(e) => {
                e.preventDefault();
                void submitAdd();
              }}
            >
              <input
                type="text"
                value={code}
                placeholder="@username or friend code"
                aria-label="Username or friend code"
                spellCheck={false}
                autoFocus
                onChange={(e) => setCode(e.target.value)}
              />
              <button type="submit" disabled={!code.trim() || busy}>
                {busy ? 'Sending…' : 'Send request'}
              </button>
            </form>
          )}
          {error && <p className="fl-error">{error}</p>}
          {!error && notice && <p className="fl-notice">{notice}</p>}

          <div className="fl-groups">
            {listed.length === 0 ? (
              <p className="fl-empty">
                No friends yet. Add someone by their @username with the button above.
              </p>
            ) : (
              groups.map((g) => (
                <Group
                  key={g.id}
                  group={g}
                  collapsed={collapsed.has(g.id)}
                  onToggle={() => toggle(g.id)}
                  unread={unread}
                  activeId={chatOpen ? active : null}
                  now={now}
                />
              ))
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
