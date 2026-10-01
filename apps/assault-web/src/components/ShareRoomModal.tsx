import { useEffect, useState } from 'react';
import { mapEntry } from '../maps';

export interface ShareRoomModalProps {
  room: string;
  map: string;
  onClose: () => void;
}

export function ShareRoomModal({ room, map, onClose }: ShareRoomModalProps) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  const shareUrl = `${window.location.origin}/#room=${encodeURIComponent(room)}&map=${encodeURIComponent(map)}`;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      setFailed(false);
      setTimeout(() => setCopied(false), 2200);
    } catch {
      // Clipboard denied (no permission, or an embedded frame). The field is
      // selectable, so say so rather than pretending it worked.
      setFailed(true);
    }
  };

  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="share-title">
        <div className="dialog-head">
          <div>
            <h2 id="share-title" className="display">
              Invite to match
            </h2>
            <p>
              Anyone with this link lands on the deploy screen for this room. No install, no
              account.
            </p>
          </div>
          <button type="button" className="close" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="dialog-body">
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              type="text"
              className="input"
              readOnly
              value={shareUrl}
              aria-label="Invite link"
              onFocus={(e) => e.currentTarget.select()}
              style={{ fontFamily: 'var(--font-mono)', fontSize: 13 }}
            />
            <button type="button" className="btn btn-primary" onClick={() => void copy()}>
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          {failed && (
            <p className="hint">Couldn't reach the clipboard. Select the link and copy it.</p>
          )}
          <hr className="rule" />
          <div
            className="mono"
            style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}
          >
            <span>room {room}</span>
            <span>
              {mapEntry(map).title} · {map}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
