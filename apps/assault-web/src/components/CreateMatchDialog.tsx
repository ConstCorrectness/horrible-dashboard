import { useEffect, useState } from 'react';
import { MAPS, mapArt, mapEntry } from '../maps';

/** Pick a map and open a fresh room on it. */
export function CreateMatchDialog({
  onLaunch,
  onClose,
}: {
  onLaunch: (map: string) => void;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState(MAPS[0]!.id);

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
                  onDoubleClick={() => onLaunch(m.id)}
                >
                  {art && <img src={art} alt="" loading="lazy" />}
                  <span>{m.title}</span>
                </button>
              );
            })}
          </div>
          <p className="hint">{mapEntry(selected).blurb}</p>
          <div className="form-actions">
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" onClick={() => onLaunch(selected)}>
              Open room on {mapEntry(selected).title}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
