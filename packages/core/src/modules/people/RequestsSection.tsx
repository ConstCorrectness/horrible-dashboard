/**
 * One inbox for everything waiting on your decision.
 *
 * There used to be two, in the same pane: an incoming *friend request* appeared as
 * a row inside the Friends list, while a *commons meet request* had its own
 * Requests section. Two inboxes a click apart is how you end up ignoring both —
 * neither one is where you look, because neither one is "the" place.
 *
 * The two kinds stay visibly distinct, because they mean genuinely different
 * things and grant genuinely different access:
 *
 * - **A friend request** is from someone who already has your friend code. Accepting
 *   grants every one of their machines fabric trust, which is what makes chat,
 *   shared panes and agent-to-agent work with no second pairing.
 * - **A meet request** is from a *stranger* found through the commons. It keeps its
 *   own consent handshake and its own trust tiers — merging it into the friend path
 *   is exactly what made "peers" confusing in the first place.
 *
 * See docs/modules/social.mdx and docs/modules/commons.mdx.
 */
import { useEffect, useSyncExternalStore } from 'react';

import {
  commonsRefresh,
  commonsRespond,
  getCommonsState,
  initCommons,
  subscribeCommons,
} from '../commons/commons';
import {
  getSocialState,
  initSocial,
  requestRoster,
  respondViaChannel,
  subscribeSocial,
} from '../social/ws';
import { Avatar } from './Avatar';
import {
  ensureProfileCards,
  getProfileCard,
  getProfileCards,
  subscribeProfileCards,
} from './profile-cards';

function useCommons() {
  return useSyncExternalStore(subscribeCommons, getCommonsState, getCommonsState);
}

function useSocial() {
  return useSyncExternalStore(subscribeSocial, getSocialState, getSocialState);
}

function useCards() {
  return useSyncExternalStore(subscribeProfileCards, getProfileCards, getProfileCards);
}

export function RequestsSection() {
  const { requests } = useCommons();
  const { roster } = useSocial();
  useCards();

  useEffect(() => {
    initCommons();
    commonsRefresh();
    initSocial();
    requestRoster();
  }, []);

  const friendRequests = (roster?.friends ?? []).filter((f) => f.status === 'pending_in');

  useEffect(() => {
    void ensureProfileCards(friendRequests.map((f) => f.handle));
    // Handles only; a changed presence must not re-fetch.
  }, [friendRequests.map((f) => f.handle).join(',')]);

  const total = friendRequests.length + requests.length;

  return (
    <div className="people-section">
      {total === 0 && (
        <p className="people-hint">
          Nothing waiting. Friend requests and requests to meet from the commons both land here.
        </p>
      )}

      {friendRequests.length > 0 && (
        <section>
          <h4 className="people-label">Friend requests ({friendRequests.length})</h4>
          <ul className="people-list">
            {friendRequests.map((f) => {
              const card = getProfileCard(f.handle);
              return (
                <li key={f.person_id} className="people-row">
                  <Avatar
                    name={f.display_name}
                    emoji={card?.avatar}
                    imageRef={card?.avatar_url}
                    size={30}
                  />
                  <div className="people-row-main">
                    <strong>{f.display_name}</strong>
                    <span className="people-dim">
                      {f.handle ? `@${f.handle}` : 'wants to be friends'}
                    </span>
                  </div>
                  <button onClick={() => respondViaChannel(f.person_id, true)}>Accept</button>
                  <button onClick={() => respondViaChannel(f.person_id, false)}>Decline</button>
                </li>
              );
            })}
          </ul>
          <p className="people-hint">
            Accepting grants every machine they own access to this node’s fabric — chat, shared
            panes, and letting their agent ask yours things.
          </p>
        </section>
      )}

      {requests.length > 0 && (
        <section>
          <h4 className="people-label">Requests to meet ({requests.length})</h4>
          <ul className="people-list">
            {requests.map((r) => (
              <li key={r.request_id} className="people-row">
                <Avatar name={r.from.display_name ?? r.from.node_id} size={30} />
                <div className="people-row-main">
                  <strong>{r.from.display_name ?? r.from.node_id}</strong>
                  <span className="people-dim">
                    {r.note ? `“${r.note}”` : 'found you through the commons'}
                  </span>
                </div>
                <button onClick={() => commonsRespond(r.request_id, true)}>Accept</button>
                <button onClick={() => commonsRespond(r.request_id, false)}>Decline</button>
              </li>
            ))}
          </ul>
          <p className="people-hint">
            These are strangers, not friends. Accepting opens a peer link on the commons’ own trust
            tiers — it does not add them to your roster.
          </p>
        </section>
      )}
    </div>
  );
}
