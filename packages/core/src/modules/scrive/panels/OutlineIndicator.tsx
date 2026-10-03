/**
 * "An outline is waiting for you", in the shell chrome. The agent stops after
 * proposing an outline and nothing happens until a person approves it, so the one
 * thing that must not get lost is that it is waiting — even with the outline pane
 * closed. Renders nothing when no outline awaits review.
 */
import { useEffect } from 'react';

import { OutlineIcon } from '../icons';
import { openOutline, startOutlineWatch, useProposedOutlines } from '../outline-watch';
import './indicator.css';

export function OutlineIndicator() {
  useEffect(() => startOutlineWatch(), []);
  const outlines = useProposedOutlines();
  if (!outlines.length) return null;
  const newest = outlines[0];
  return (
    <div className="scrive-indicator" role="status" aria-live="polite">
      <button
        type="button"
        className="scrive-indicator__open"
        title={`Review the outline “${newest.title}”`}
        onClick={() => openOutline(newest.site, newest.id)}
      >
        <OutlineIcon size={12} />
        <span className="scrive-indicator__label">Outline</span>
        <span className="scrive-indicator__meta">
          {outlines.length === 1 ? 'to review' : `${outlines.length} to review`}
        </span>
      </button>
    </div>
  );
}
