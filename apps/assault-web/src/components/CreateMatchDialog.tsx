import { useEffect, useState } from 'react';
import { MAPS, mapArt, mapEntry } from '../maps';

/** The modes a browser room can open in. Defuse needs a map with bomb sites. */
const MODES = [
  { id: 'dm', label: 'Deathmatch' },
  { id: 'defuse', label: 'Defuse' },
] as const;

/** Pick a map and a mode, and open a fresh room on it. */
export function CreateMatchDialog({
  onLaunch,
  onClose,
}: {
  onLaunch: (map: string, mode: string) => void;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState(MAPS[0]!.id);
  const [mode, setMode] = useState<string>('dm');
  // A map without sites cannot host defuse; the server would refuse the room.
  const effective = mode === 'defuse' && !mapEntry(selected).sites ? 'dm' : mode;

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
            {MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                className="chip"
                aria-pressed={effective === m.id}
                disabled={m.id === 'defuse' && !mapEntry(selected).sites}
                onClick={() => setMode(m.id)}
              >
                {m.label}
              </button>
            ))}
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
