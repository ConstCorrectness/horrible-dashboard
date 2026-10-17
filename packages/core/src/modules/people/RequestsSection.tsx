/**
 * Requests to meet from the commons — strangers, not friends.
 *
 * Friend requests used to live here too, on the theory that one inbox beats two.
 * They moved back to the top of the Friends list (Steam's place for them), because
 * that is where people actually look: an inbox a section away from the list it
 * feeds was the one nobody opened. This section still *mentions* them, with a link,
 * so arriving here for a friend request is not a dead end.
 *
 * The two kinds mean genuinely different things and grant genuinely different
 * access:
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

import { revealSection } from '../../layout/controller';

import {
  commonsRefresh,
  commonsRespond,
  getCommonsState,
  initCommons,
  subscribeCommons,
} from '../commons/commons';
import { getSocialState, initSocial, requestRoster, subscribeSocial } from '../social/ws';
import { Avatar } from './Avatar';

function useCommons() {
  return useSyncExternalStore(subscribeCommons, getCommonsState, getCommonsState);
}

function useSocial() {
  return useSyncExternalStore(subscribeSocial, getSocialState, getSocialState);
}

export function RequestsSection() {
  const { requests } = useCommons();
  const { roster } = useSocial();

  useEffect(() => {
    initCommons();
    commonsRefresh();
    initSocial();
    requestRoster();
  }, []);

  const friendRequests = (roster?.friends ?? []).filter((f) => f.status === 'pending_in');

  return (
    <div className="people-section">
      {requests.length === 0 && (
        <p className="people-hint">
          Nothing waiting. Requests to meet from people who found you through the commons land here.
        </p>
      )}

      {friendRequests.length > 0 && (
        <p className="people-hint">
          {friendRequests.length === 1
            ? '1 friend request is waiting'
            : `${friendRequests.length} friend requests are waiting`}{' '}
          at the top of{' '}
          <button
            type="button"
            className="people-link"
            onClick={() => revealSection('friends', 'people.home')}
          >
            Friends
          </button>
          .
        </p>
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
