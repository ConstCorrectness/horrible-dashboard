/**
 * "You are invited to edit live", in the shell chrome. An invitation arrives from a
 * friend's machine at any moment, with no Scrive pane open; a toast says so once, and
 * this stays until the host ends the session. Renders nothing otherwise.
 */
import { useEffect, useSyncExternalStore } from 'react';

import { toastsStore } from '../../../toasts';
import { LiveIcon } from '../icons';
import { getInvitations, subscribeInvitations, watchInvitations } from '../live/invites';
import { openLive } from '../open';
import './indicator.css';

export function LiveIndicator() {
  useEffect(
    () =>
      watchInvitations((room) =>
        toastsStore.add(
          'info',
          `${room.host || 'A friend'} invites you to edit live`,
          room.title,
          0,
          { action: { label: 'Join', run: () => openLive(room.key, room.title) } },
        ),
      ),
    [],
  );
  const invitations = useSyncExternalStore(subscribeInvitations, getInvitations, getInvitations);
  if (!invitations.length) return null;
  const newest = invitations[invitations.length - 1];
  return (
    <div className="scrive-indicator" role="status" aria-live="polite">
      <button
        type="button"
        className="scrive-indicator__open"
        title={`Join ${newest.host || 'a friend'} editing “${newest.title}”`}
        onClick={() => openLive(newest.key, newest.title)}
      >
        <LiveIcon size={12} />
        <span className="scrive-indicator__label">Live</span>
        <span className="scrive-indicator__meta">
          {invitations.length === 1 ? newest.host || 'invited' : `${invitations.length} invites`}
        </span>
      </button>
    </div>
  );
}
