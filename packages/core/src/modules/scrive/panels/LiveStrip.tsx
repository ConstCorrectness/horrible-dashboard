/**
 * The host's live-editing strip in the page pane: start a session, invite friends
 * who are online, see who is in the page, end it.
 *
 * Invitations go to a *person* (`network/collab.py`'s rule): whichever of their
 * machines is up receives it. Only friends with a machine online are offered — an
 * invitation to someone offline is an invitation to nothing.
 */
import { useSyncExternalStore } from 'react';

import { getSocialState, subscribeSocial } from '../../social/ws';
import { CloseIcon, LiveIcon } from '../icons';
import type { LivePerson, LivePresence } from '../live/session';

export interface HostLiveState {
  key: string;
  people: LivePerson[];
  presence: LivePresence[];
}

export function LiveStrip({
  live,
  busy,
  error,
  onStart,
  onEnd,
  onInvite,
  onUninvite,
  onClose,
}: {
  live: HostLiveState | null;
  busy: boolean;
  error: string | null;
  onStart: () => void;
  onEnd: () => void;
  onInvite: (personId: string) => void;
  onUninvite: (personId: string) => void;
  onClose: () => void;
}) {
  const { roster } = useSyncExternalStore(subscribeSocial, getSocialState, getSocialState);
  const online = (roster?.friends ?? []).filter(
    (f) => f.status === 'accepted' && f.presence === 'online' && !f.is_self,
  );
  return (
    <div className="scrive-findings scrive-export">
      <div className="scrive-export-bar">
        <span
          className="scrive-head"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
        >
          <LiveIcon size={12} /> Live
        </span>
        {live ? (
          <>
            <ul className="scrive-live-people" aria-label="Editing now">
              {live.presence.map((p) => (
                <li key={p.clientId} className="scrive-chip">
                  <span className="scrive-live-dot" style={{ background: p.color }} />
                  {p.name}
                </li>
              ))}
            </ul>
            <span style={{ flex: 1 }} />
            <button
              type="button"
              disabled={busy}
              onClick={onEnd}
              style={{ borderColor: 'var(--danger)', color: 'var(--danger)' }}
              title="End the session for everyone. The page stays as it is here."
            >
              End for everyone
            </button>
          </>
        ) : (
          <>
            <span className="scrive-meta" style={{ flex: 1, minWidth: 0 }}>
              Edit this page with friends as you both type; this machine keeps and saves it.
            </span>
            <button
              type="button"
              disabled={busy}
              onClick={onStart}
              style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
            >
              {busy ? 'Starting…' : 'Start live'}
            </button>
          </>
        )}
        <button
          type="button"
          className="btn-mini"
          aria-label="Close live editing"
          title={live ? 'Hide (the session goes on)' : 'Close'}
          onClick={onClose}
        >
          <CloseIcon size={12} />
        </button>
      </div>
      {live && (
        <div className="scrive-live-invite">
          <span className="scrive-meta">Invite</span>
          {online.length === 0 && <span className="scrive-meta">No friends online right now.</span>}
          {online.map((f) => {
            const invited = live.people.some((p) => p.personId === f.person_id);
            return (
              <button
                key={f.person_id}
                type="button"
                className="scrive-chip scrive-live-person"
                aria-pressed={invited}
                title={invited ? `Stop sharing with ${f.display_name}` : `Invite ${f.display_name}`}
                onClick={() => (invited ? onUninvite(f.person_id) : onInvite(f.person_id))}
              >
                {f.display_name}
              </button>
            );
          })}
        </div>
      )}
      {live && (
        <p className="scrive-meta scrive-export-note">
          Write mode is off while live: everyone edits the source, and the preview follows. Changes
          autosave here.
        </p>
      )}
      {error && (
        <p className="scrive-export-note" role="alert" style={{ color: 'var(--danger)' }}>
          {error}
        </p>
      )}
    </div>
  );
}
