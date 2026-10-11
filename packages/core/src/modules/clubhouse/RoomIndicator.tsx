import { useState, useSyncExternalStore } from 'react';

import { IconClose } from '../../glyphs';
import { registry } from '../../registry';
import { getClubhousePresence, leaveClubhouse, subscribeClubhousePresence } from './presence';

import './room-indicator.css';

/**
 * "You are in a Clubhouse room", in the shell chrome, on every desktop.
 *
 * A room is audio: it plays whether or not its pane is on screen, and by design
 * it survives a tab switch, a hidden dock and a desktop switch. Before this,
 * nothing outside the pane said a call was live — so a room left running on
 * another desktop, or in a desktop board tile, played until the whole app was
 * closed. This is on screen whenever a room is, with Leave one click away.
 *
 * Reads `presence` rather than the room session, so the shell never loads the
 * Agora SDK just to learn that nothing is playing.
 */
export function ClubhouseRoomIndicator() {
  const presence = useSyncExternalStore(
    subscribeClubhousePresence,
    getClubhousePresence,
    getClubhousePresence,
  );
  const [leaving, setLeaving] = useState(false);
  if (!presence.joined) return null;

  const meta = [`${presence.people} in room`];
  if (presence.muted) meta.push('muted');

  return (
    <div className="ch-live" role="status" aria-live="polite">
      <button
        type="button"
        className="ch-live__open"
        title="Open Clubhouse"
        onClick={() => registry.openPanel('clubhouse.account')}
      >
        <span className="ch-live__dot" aria-hidden="true" />
        <span className="ch-live__label">Clubhouse</span>
        <span className="ch-live__meta">{meta.join(' · ')}</span>
      </button>
      <button
        type="button"
        className="ch-live__leave"
        aria-label="Leave the Clubhouse room"
        title="Leave the room"
        disabled={leaving || presence.loading}
        onClick={() => {
          setLeaving(true);
          void leaveClubhouse().finally(() => setLeaving(false));
        }}
      >
        <IconClose />
      </button>
    </div>
  );
}
