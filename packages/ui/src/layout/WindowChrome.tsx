/**
 * Native window chrome for the phase-2 `chrome.workspaceTabs` capability. When a
 * native shell grants it, the desktop window is **undecorated** and the
 * workspace tab strip is the titlebar, so the app must supply what the OS frame
 * normally would:
 *
 * - {@link WindowControls} — minimize / maximize-restore / close, hosted at the
 *   right end of the tab strip.
 * - {@link WindowResizeHandles} — invisible edge/corner strips that start an OS
 *   resize-drag (the undecorated window has no native resize borders).
 *
 * Both drive the core `WindowControl` seam and render nothing without it, so
 * they're inert in the browser. Callers still gate on
 * `hasCapability('chrome.workspaceTabs')` before mounting them.
 */
import { useEffect, useState } from 'react';
import { windowControl, type ResizeEdge } from '@horrible/core';

const MinimizeIcon = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
    <path d="M1 5h8" stroke="currentColor" strokeWidth="1" fill="none" />
  </svg>
);

const MaximizeIcon = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
    <rect x="1" y="1" width="8" height="8" stroke="currentColor" strokeWidth="1" fill="none" />
  </svg>
);

const RestoreIcon = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
    <rect x="1" y="3" width="6" height="6" stroke="currentColor" strokeWidth="1" fill="none" />
    <path d="M3 3V1h6v6H7" stroke="currentColor" strokeWidth="1" fill="none" />
  </svg>
);

const CloseIcon = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
    <path d="M1 1l8 8M9 1l-8 8" stroke="currentColor" strokeWidth="1" fill="none" />
  </svg>
);

/** Minimize / maximize-restore / close buttons for the custom titlebar. */
export function WindowControls() {
  const wc = windowControl();
  const [maximized, setMaximized] = useState(false);

  useEffect(() => {
    if (!wc) return;
    let alive = true;
    void wc.isMaximized().then((m) => {
      if (alive) setMaximized(m);
    });
    return () => {
      alive = false;
    };
  }, [wc]);

  if (!wc) return null;

  return (
    <div className="win-controls">
      <button className="win-btn" title="Minimize" onClick={() => void wc.minimize()}>
        <MinimizeIcon />
      </button>
      <button
        className="win-btn"
        title={maximized ? 'Restore' : 'Maximize'}
        onClick={async () => setMaximized(await wc.toggleMaximize())}
      >
        {maximized ? <RestoreIcon /> : <MaximizeIcon />}
      </button>
      <button className="win-btn win-btn--close" title="Close" onClick={() => void wc.close()}>
        <CloseIcon />
      </button>
    </div>
  );
}

const EDGES: { edge: ResizeEdge; cls: string }[] = [
  { edge: 'north', cls: 'n' },
  { edge: 'south', cls: 's' },
  { edge: 'east', cls: 'e' },
  { edge: 'west', cls: 'w' },
  { edge: 'north-west', cls: 'nw' },
  { edge: 'north-east', cls: 'ne' },
  { edge: 'south-west', cls: 'sw' },
  { edge: 'south-east', cls: 'se' },
];

/** Invisible edge/corner grips that start an OS resize-drag. */
export function WindowResizeHandles() {
  const wc = windowControl();
  if (!wc) return null;
  return (
    <div className="win-resize" aria-hidden>
      {EDGES.map(({ edge, cls }) => (
        <div
          key={edge}
          className={`win-resize-h win-resize-h--${cls}`}
          onMouseDown={(e) => {
            if (e.button !== 0) return;
            e.preventDefault();
            void wc.startResizeDragging(edge);
          }}
        />
      ))}
    </div>
  );
}
