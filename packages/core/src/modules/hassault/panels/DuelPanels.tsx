/**
 * The rated duel's two pieces of HUD: the search while queued, and the result.
 *
 * Both read the session's copy of what the server said (`duel_queue`,
 * `rating_update`); neither computes a number. The rating delta counts in
 * (`RollingNumber`, seeded at its final value), because a rating is the one
 * figure a player came to the duel to see move.
 */

import { RollingNumber } from '../../../DataList';
import type { DuelQueueStatus, RatingUpdate } from '../session';

const MUTED = 'var(--text-secondary, rgba(255,255,255,0.6))';
const UP = 'var(--success)';
const DOWN = 'var(--danger)';

const card = {
  position: 'absolute' as const,
  left: '50%',
  transform: 'translateX(-50%)',
  minWidth: 300,
  background: 'rgba(13,17,23,0.94)',
  borderTop: '2px solid var(--accent)',
  clipPath: 'polygon(0 0, calc(100% - 12px) 0, 100% 12px, 100% 100%, 0 100%)',
  padding: '0.6rem 0.9rem',
  fontFamily: 'monospace',
  fontSize: '13px',
  color: 'rgba(255,255,255,0.92)',
  zIndex: 30,
};

const heading = {
  fontWeight: 700,
  letterSpacing: '0.14em',
  textTransform: 'uppercase' as const,
  fontSize: '13px',
};

/** Searching for an opponent, with the wait and a way out. */
export function DuelQueueBanner({
  queue,
  onCancel,
}: {
  queue: DuelQueueStatus | null;
  onCancel: () => void;
}) {
  if (!queue?.queued) return null;
  return (
    <div style={{ ...card, top: '30%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem' }}>
        <span style={heading}>Searching for an opponent</span>
        <span style={{ color: MUTED }}>{Math.round(queue.waited ?? 0)}S</span>
      </div>
      <div style={{ color: MUTED, margin: '0.35rem 0 0.5rem' }}>
        RATED DUEL · {queue.waiting ?? 1} IN QUEUE
        {queue.rating !== undefined ? ` · YOUR RATING ${Math.round(queue.rating)}` : ''}
      </div>
      <button type="button" onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}

/** The duel's verdict and what it did to the rating. */
export function RatingResult({
  update,
  onDismiss,
}: {
  update: RatingUpdate | null;
  onDismiss: () => void;
}) {
  if (!update) return null;
  const verdict = update.draw ? 'Draw' : update.won ? 'Victory' : 'Defeat';
  const colour = update.delta >= 0 ? UP : DOWN;
  const placed = update.rating?.placed ?? true;
  return (
    <div style={{ ...card, top: '22%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem' }}>
        <span style={heading}>{verdict}</span>
        <span style={{ color: MUTED, letterSpacing: '0.1em' }}>
          {update.reason === 'forfeit' ? 'BY FORFEIT' : 'RATED DUEL'}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.6rem', margin: '0.4rem 0' }}>
        <span style={{ fontSize: '21px', fontWeight: 700, color: colour }}>
          <RollingNumber
            value={update.delta}
            format={(n) => `${n >= 0 ? '+' : ''}${Math.round(n)}`}
          />
        </span>
        <span style={{ color: MUTED }}>
          {Math.round(update.before)} → <RollingNumber value={update.after} />
        </span>
      </div>
      <div style={{ color: MUTED, marginBottom: '0.5rem' }}>
        {placed
          ? (update.rating?.tier ?? '').toUpperCase()
          : `PLACEMENT ${update.rating?.games ?? 0}/${update.rating?.placementGames ?? 10}`}
      </div>
      <button type="button" onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  );
}
