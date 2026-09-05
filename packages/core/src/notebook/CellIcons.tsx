/**
 * Cell chrome glyphs: stroke SVG inheriting `currentColor`.
 *
 * These replace the emoji badges the pane used to render (⏳ ▶ ✓ ✗ and the ✕/+ in the
 * action column). An emoji is a *font* — it arrives at whatever size and colour the
 * platform decided, is full-colour in a monochrome gutter, and cannot be tinted by
 * run state, which is the one thing these glyphs exist to signal. See the project's
 * iconography rule; the pane manifest's rail icon is the documented exception.
 */
import type { ReactElement } from 'react';

import type { CellRunState } from './types';

const svg = {
  viewBox: '0 0 16 16',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
} as const;

/** The four run states, in the gutter. Null for a cell that has not been run. */
export function RunStateIcon({ state }: { state: CellRunState }): ReactElement {
  const cls = `nb-state nb-state--${state}`;
  if (state === 'queued') {
    // A hollow ring: waiting, not yet doing anything.
    return (
      <svg {...svg} className={cls} aria-label="queued" role="img">
        <circle cx="8" cy="8" r="5" strokeDasharray="2 2.4" />
      </svg>
    );
  }
  if (state === 'running') {
    // An open arc, spun by the stylesheet.
    return (
      <svg {...svg} className={cls} aria-label="running" role="img">
        <path d="M13 8a5 5 0 1 0-1.8 3.85" />
      </svg>
    );
  }
  if (state === 'error') {
    return (
      <svg {...svg} className={cls} aria-label="error" role="img">
        <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" />
      </svg>
    );
  }
  return (
    <svg {...svg} className={cls} aria-label="done" role="img">
      <path d="M3.5 8.5l3 3 6-7" />
    </svg>
  );
}

export function PlayIcon(): ReactElement {
  return (
    <svg {...svg} aria-hidden>
      <path d="M5 3.5l7 4.5-7 4.5z" fill="currentColor" strokeWidth={1} />
    </svg>
  );
}

export function PlusIcon(): ReactElement {
  return (
    <svg {...svg} aria-hidden>
      <path d="M8 3.5v9M3.5 8h9" />
    </svg>
  );
}

/** Add-markdown: a plus with a paragraph rule beside it, so the two + buttons differ. */
export function PlusTextIcon(): ReactElement {
  return (
    <svg {...svg} aria-hidden>
      <path d="M3 4.5h6M3 8h4M3 11.5h4M11.5 8v5M9 10.5h5" />
    </svg>
  );
}

export function CloseIcon(): ReactElement {
  return (
    <svg {...svg} aria-hidden>
      <path d="M4 4l8 8M12 4l-8 8" />
    </svg>
  );
}

export function WarnIcon(): ReactElement {
  return (
    <svg {...svg} aria-hidden style={{ width: 11, height: 11, flex: 'none' }}>
      <path d="M8 2.5L14.5 13.5h-13z" />
      <path d="M8 6.5v3.2M8 11.6v.1" />
    </svg>
  );
}
