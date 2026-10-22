import { useEffect, useState } from 'react';
import { useServerMaps } from '../hooks/useServerMaps';
import { MAPS, MODE_OPTIONS, mapArt, mapEntry, modeLabel, modesFor } from '../maps';

/**
 * Pick a map and a mode, and open a fresh room on it; or queue for a ranked
 * duel, which has no map to pick: the server pairs two players and chooses.
 */
export function CreateMatchDialog({
  onLaunch,
  onDuel,
  canDuel,
  onSignIn,
  onClose,
}: {
  onLaunch: (map: string, mode: string) => void;
  /** Queue for a duel on the served duel map. */
  onDuel: (duelMap: string) => void;
  /** Duels are rated, so the server queues accounts only. */
  canDuel: boolean;
  onSignIn: () => void;
  onClose: () => void;
}) {
  const served = useServerMaps();
  const [selected, setSelected] = useState(MAPS[0]!.id);
  const [mode, setMode] = useState<string>('dm');
  // Only what this map offers; the server refuses a room in anything else.
  const allowed = modesFor(selected, served?.modes);
  const effective = allowed.includes(mode) ? mode : (allowed[0] ?? 'dm');

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="dialog dialog-wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-title"
      >
        <div className="dialog-head">
          <div>
            <h2 id="create-title" className="display">
              Create match
            </h2>
            <p>A new room of your own. Share its link and anyone can drop straight in.</p>
          </div>
          <button type="button" className="close" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="dialog-body">
          <div className="map-pick" role="group" aria-label="Map">
            {MAPS.map((m) => {
              const art = mapArt(m.id);
              return (
                <button
                  key={m.id}
                  type="button"
                  className="map-pick-item"
                  aria-pressed={selected === m.id}
                  onClick={() => setSelected(m.id)}
                  onDoubleClick={() => onLaunch(m.id, effective)}
                >
                  {art && <img src={art} alt="" loading="lazy" />}
                  <span>{m.title}</span>
                </button>
              );
            })}
          </div>
          <p className="hint">{mapEntry(selected).blurb}</p>
          <div className="chips" role="group" aria-label="Mode">
            {MODE_OPTIONS.map((id) => (
              <button
                key={id}
                type="button"
                className="chip"
                aria-pressed={effective === id}
                disabled={!allowed.includes(id)}
                onClick={() => setMode(id)}
              >
                {modeLabel(id)}
              </button>
            ))}
          </div>
          <div className="duel-row">
            <p className="hint">
              Ranked duel: one against one, first to ten, rated against your opponent.
              {canDuel ? '' : ' Needs an account.'}
            </p>
            {canDuel ? (
              <button
                type="button"
                className="btn"
                disabled={!served?.duelMap}
                onClick={() => served?.duelMap && onDuel(served.duelMap)}
              >
                Queue for a duel
              </button>
            ) : (
              <button type="button" className="btn" onClick={onSignIn}>
                Sign in
              </button>
            )}
          </div>
          <div className="form-actions">
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => onLaunch(selected, effective)}
            >
              Open room on {mapEntry(selected).title}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
